"""One deployment run, start to finish, as events. Used by the CLI and (later) by the web worker."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from liara_agent.llm.client import LLMError
from liara_agent.deploy.network import decide_network
from liara_agent.agent.planner import apply_env, make_plan
from liara_agent.config import Settings
from liara_agent.deploy.advice import add_advice
from liara_agent.deploy.decisions import decide_build_tier, decide_mail, decide_storage, decide_wordpress, decide_wordpress_server
from liara_agent.detect.wordpress import looks_like_wordpress
from liara_agent.deploy.executor import Outcome, execute
from liara_agent.detect.fingerprints import fingerprint
from liara_agent.detect.readiness import check_readiness
from liara_agent.events import Run
from liara_agent.heal.loop import HealIO
from liara_agent.handoff import CodeFixOptions, make_chooser
from liara_agent.heal.repair import clean_lines, repair_fix
from liara_agent.interact import Channel
from liara_agent.liara.client import LiaraAPIError, LiaraClient
from liara_agent.liara.live import TIER_LABELS, LiveCatalog
from liara_agent.liara.pricing import estimate_plan, total
from liara_agent.llm.client import LLM
from liara_agent.plan.oneclick import plan_from_spec
from liara_agent.plan.model import WordpressDelivery, DeployPlan, Note, Question


# Liara's HTTP statuses mean something specific to the user; the UI explains each by its code
def liara_detail(body: str) -> str:
    """What Liara said it refused, in a few words (its `message`), for the person to read or to search for."""
    try:
        msg = json.loads(body).get("message")
        return str(msg if not isinstance(msg, list) else "; ".join(map(str, msg)))[:160]
    except Exception:
        return ""


LIARA_STATUS_CODES = {401: "liara_token_invalid", 402: "insufficient_balance", 403: "liara_forbidden", 409: "name_taken", 428: "deploy_limit_reached", 429: "liara_rate_limited"}


@dataclass
class PipelineResult:
    ok: bool
    plan: DeployPlan | None = None
    outcome: Outcome | None = None
    stopped: str | None = None  # why it ended without deploying: blocked | cancelled | no_plan | error


def public_plan(plan: DeployPlan) -> dict:
    """The plan as the UI may see it. Environment values are never included: names, whether a value is set, and
    $DB_ references (which are placeholders, not secrets)."""
    d = plan.model_dump()
    for app_dict, app in zip(d["apps"], plan.apps):
        app_dict["env"] = [{"name": k, "reference": v if v.startswith("$") or "$DB_" in v else None, "has_value": bool(v)}
                           for k, v in app.env.items()]
    d["links"] = plan_links(plan)
    return d


def plan_links(plan: DeployPlan) -> list[dict]:
    """Which resource each app talks to, found from its environment: a $DB_<id>_* reference or a value naming another resource.
    The values themselves are never exposed, only the variable names."""
    targets = {**{d.id: "database" for d in plan.databases}, **{a.name: "app" for a in plan.apps}, **{b.id: "bucket" for b in plan.storage},
               **({plan.mail.domain.replace(".", "-"): "mail"} if plan.mail else {})}
    links: dict[tuple[str, str], list[str]] = {}
    for app in plan.apps:
        for name, value in app.env.items():
            for rid in targets:
                if rid != app.name and (f"$DB_{rid}_" in value or f"$S3_{rid}_" in value or f"$MAIL_{rid}_" in value or re.search(rf"(?<![\w-]){re.escape(rid)}(?![\w-])", value)):
                    links.setdefault((app.name, rid), []).append(name)
    return [{"from": a, "to": b, "to_kind": targets[b], "env": sorted(set(names))} for (a, b), names in links.items()]


def request_view(a) -> dict:
    """A request a run would make: the English summary plus a stable `op` and `params` the UI can word in any language."""
    b = a.body or {}
    params = {
        "create_app": {"name": b.get("name"), "platform": b.get("platform"), "plan": b.get("planID"), "tier": b.get("bundlePlanID")},
        "create_database": {"name": b.get("hostname"), "type": b.get("type"), "version": b.get("version"), "plan": b.get("planID"), "tier": b.get("bundlePlanID")},
        "set_env": {"app": b.get("project"), "count": len(b.get("variables") or [])},
        "upload_source": {"app": a.path.split("/")[3] if a.path.count("/") >= 3 else None},
        "create_release": {"app": a.path.split("/")[3] if a.path.count("/") >= 3 else None, "port": b.get("port")},
        "resize_app": {"plan": b.get("planID"), "tier": b.get("bundlePlanID")},
        "update_port": {"app": b.get("project"), "port": b.get("port")},
        "create_disk": {"app": a.path.split("/")[3] if a.path.count("/") >= 3 else None, "name": b.get("name"), "size": b.get("size")},
        "create_ftp": {"app": a.path.split("/")[3] if a.path.count("/") >= 3 else None, "disk": a.path.split("/")[5] if a.path.count("/") >= 5 else None},
    }.get(a.op, {})
    return {"method": a.method, "path": a.path, "summary": a.summary, "op": a.op, "params": {k: v for k, v in params.items() if v is not None}}


def _preview_requests(plan: DeployPlan, cat: LiveCatalog, client: LiaraClient, path: Path, network_id: str | None) -> list[dict]:
    """The exact requests a real run would make, computed with a dry-run client that cannot send anything."""
    try:
        dry = client.dry_copy()
        execute(plan.model_copy(deep=True), cat, dry, path, network_id=network_id)
        return [request_view(a) for a in dry.actions]
    except Exception:  # a preview is a convenience; never let it block the plan
        return []


# conventional connection variables per database kind, in the order they are preferred; the value is built from the database's generated credentials
DB_URL_VARS = {
    "mongodb": (("MONGODB_URI", "MONGO_URI", "MONGO_URL", "MONGODB_URL", "DATABASE_URL"), "mongodb://$DB_{id}_USER:$DB_{id}_PASSWORD@$DB_{id}_HOST:$DB_{id}_PORT/$DB_{id}_NAME?authSource=admin"),
    "postgres": (("DATABASE_URL", "POSTGRES_URL", "POSTGRES_URI", "DB_URL"), "postgres://$DB_{id}_USER:$DB_{id}_PASSWORD@$DB_{id}_HOST:$DB_{id}_PORT/$DB_{id}_NAME"),
    "mysql": (("DATABASE_URL", "MYSQL_URL", "DB_URL"), "mysql://$DB_{id}_USER:$DB_{id}_PASSWORD@$DB_{id}_HOST:$DB_{id}_PORT/$DB_{id}_NAME"),
    "mariadb": (("DATABASE_URL", "MARIADB_URL", "MYSQL_URL", "DB_URL"), "mysql://$DB_{id}_USER:$DB_{id}_PASSWORD@$DB_{id}_HOST:$DB_{id}_PORT/$DB_{id}_NAME"),
    "redis": (("REDIS_URL", "REDIS_URI", "CACHE_URL"), "redis://:$DB_{id}_PASSWORD@$DB_{id}_HOST:$DB_{id}_PORT"),
}


def wire_database_env(plan, detected: set[str]) -> None:
    """A database that no app refers to is useless: the app would look for it on localhost, wait for a connection that never comes and crash.
    When the plan did not connect a database, set the connection variable the code reads (or the conventional one for that kind)."""
    for d in plan.databases:
        if d.kind not in DB_URL_VARS or not plan.apps:
            continue
        ref = f"DB_{d.id}_"
        if any(ref in v for a in plan.apps for v in a.env.values()):
            continue
        names, template = DB_URL_VARS[d.kind]
        name = d.env_var or next((n for n in names if n in detected), names[0])
        app = next((a for a in plan.apps if a.role == "web"), plan.apps[0])
        if name in app.env and "DB_" not in app.env[name]:
            continue  # the plan set this variable on purpose; leave it
        app.env[name] = template.format(id=d.id)
        plan.notes.append(Note(level="info", message=f"{app.name} reaches {d.id} through {name}", code="database_wired",
                               params={"app": app.name, "database": d.id, "env_var": name, "detected": name in detected}))


def pin_platform_versions(plan, root: Path) -> None:
    """Build with the runtime version the project asks for (package.json engines, runtime.txt, composer.json, go.mod, ...) and say so in the plan."""
    from liara_agent.detect.versions import detect
    for a in plan.apps:
        if a.image or a.platform_config:
            continue
        d = detect(a.platform, root / a.path)
        if d:
            a.platform_config = d.config
            plan.notes.append(Note(level="info", message=f"{a.name} will build with {a.platform} {d.version} ({d.source} says {d.wanted})", code="platform_version",
                                   params={"app": a.name, "platform": a.platform, "version": d.version, "source": d.source, "wanted": d.wanted}))


def namespace_databases(plan) -> None:
    """A database id is its hostname on the private network and is unique across Liara: a bare "mongodb" or "main" is generic and
    collides. Prefix it with the first app's name (once) and rewrite the $DB_<id>_* references in the apps' environments."""
    if not plan.apps:
        return
    base = plan.apps[0].name
    for d in plan.databases:
        if d.id == base or d.id.startswith(base + "-") or d.id.startswith(base.split("-")[0] + "-"):
            continue
        old, new = d.id, f"{base}-{d.id}"[:40].strip("-")
        d.id = new
        for a in plan.apps:
            a.env = {k: v.replace(f"DB_{old}_", f"DB_{new}_") for k, v in a.env.items()}


def run_deployment(path: str | Path, project: str, cat: LiveCatalog, cfg: Settings, client: LiaraClient, channel: Channel, run: Run, *,
                   network_id: str | None, execute_: bool, confirm_plan: bool = True, llm: LLM | None = None,
                   max_heal_attempts: int = 3, networks: list[dict] | None = None, network_name: str = "", language: str = "en",
                   auto_fix: bool = False, code_fix: CodeFixOptions | None = None) -> PipelineResult:
    """`networks` (the account's private networks) lets the agent choose or create the project's network; without it `network_id` is used as given."""
    path = Path(path)
    dry = not execute_
    run.emit("run_started", {"project": project, "dry_run": dry})

    def finish(ok: bool, **extra) -> None:
        j = client.journal  # names are what the UI knows; Liara's internal ids are kept separately for cleanup
        created = {"apps": list(j.apps), "databases": [j.database_names.get(i, i) for i in j.databases], "database_ids": list(j.databases),
                   "buckets": list(j.buckets), "mail_servers": [m["domain"] for m in j.mail_servers]}
        run.emit("run_finished", {"ok": ok, "dry_run": dry, "created": created, **extra})

    # 1) analyze: cheap, deterministic, no network
    run.emit("phase_started", {"phase": "analyze"})
    fp = fingerprint(path)
    run.emit("phase_finished", {"phase": "analyze", "manifests": fp.manifests, "hints": [f"{h.kind}={h.value}" for h in fp.hints],
                                "env_var_names": sorted(fp.env_vars)})

    ask = lambda text, options, **kw: channel.question(text, options or None, **kw)  # noqa: E731

    # 1b) a WordPress site has a managed product of its own, much dearer than the generic route: the person chooses before anything is planned
    wp = looks_like_wordpress(path)
    wp_choice = decide_wordpress(cat, wp, ask) if wp else None
    if wp_choice == "managed_console":
        finish(False, reason="managed WordPress chosen", stopped="cancelled", reason_code="wordpress_managed",
               follow_ups=[{"code": "wordpress_console", "params": {"url": "https://console.liara.ir"}, "records": []}])
        return PipelineResult(False, None, stopped="cancelled")

    # 2) plan: compose is decomposed deterministically, anything else is investigated by the agent
    run.emit("phase_started", {"phase": "plan"})
    try:
        if wp_choice == "wordpress_app":  # no investigation needed: Liara's own recipe for WordPress, plus delivery of the project's wp-content
            wp_app = decide_wordpress_server(cat, path, ask)
            plan = plan_from_spec(wp_app, project, ram_of=lambda pid: cat.project_plans[pid].ram.amount if pid in cat.project_plans else 0.5)
            plan.wordpress = WordpressDelivery(app=plan.apps[0].name, disk="data")
            how, res = f"from one-click app {wp_app}", None
        else:
            plan, how, res = make_plan(path, project, cat, cfg, llm=llm, ask=lambda q, options=None: channel.question(q, options), emit=run)
    except RuntimeError as e:
        finish(False, reason=str(e), stopped="error", reason_code="agent_unavailable")
        return PipelineResult(False, stopped="error")
    except LLMError as e:   # the model could not be reached or refused: not our bug, and nothing was created
        finish(False, reason=f"the AI model could not be used ({e.why})", stopped="error", reason_code="agent_unavailable", reason_params={"why": e.why})
        return PipelineResult(False, stopped="error")
    if plan is None:
        finish(False, reason=f"the agent could not produce a valid plan ({res.stopped_because} after {res.steps} steps)", stopped="no_plan",
               reason_code="no_plan", reason_params={"why": res.stopped_because, "steps": res.steps})
        return PipelineResult(False, stopped="no_plan")
    for q in plan.questions:  # questions the plan itself raised (e.g. a worker without a port)
        spec = q if isinstance(q, Question) else Question(text=q)
        answer = channel.question(spec.text, spec.options or None, code=spec.code, params=spec.params)
        plan.notes.append(Note(level="info", message=f"You answered '{spec.text}' with: {answer}", code="question_answered",
                               params={"question_code": spec.code, "question": spec.text, "answer": answer}))
    namespace_databases(plan)
    wire_database_env(plan, set(fp.env_vars))
    hints = {(h.kind, h.value) for h in fp.hints}
    decide_storage(plan, hints, set(fp.env_vars), cat, ask)
    decide_mail(plan, hints, set(fp.env_vars), cat, ask)
    decide_build_tier(plan, path, cat, ask)
    add_advice(plan, path, hints)
    pin_platform_versions(plan, path)
    try:  # a name clash must stop the run BEFORE anything is created (databases are created before apps)
        taken_apps, taken_dbs = client.existing_names()
        for a in plan.apps:
            if a.name in taken_apps:
                plan.notes.append(Note(level="blocker", message=f"an app named {a.name!r} already exists on your Liara account", code="name_taken", params={"kind": "app", "name": a.name}))
        for d in plan.databases:
            if d.id in taken_dbs:
                plan.notes.append(Note(level="blocker", message=f"a database named {d.id!r} already exists on your Liara account", code="name_taken", params={"kind": "database", "name": d.id}))
    except Exception:  # the executor re-checks apps right before creating them
        pass
    choice = None
    if networks is not None:   # the agent decides which private network the project's apps and databases share (a new one is made only after approval)
        choice = decide_network(plan, project, networks, network_name, ask)
        network_id = choice.id
        if choice.name:
            plan.notes.append(Note(level="info", message=f"private network {choice.name}", code="network_new" if choice.new else "network_existing", params={"name": choice.name}))
    lines = estimate_plan(cat, plan)
    tot = total(lines)
    cost = {"lines": [{"kind": l.kind, "name": l.name, "plan": l.plan, "tier": l.tier, "tier_label": TIER_LABELS.get(l.tier, l.tier), "hourly_incl_vat": round(l.cost.hourly_with_vat, 2),
                       "monthly_incl_vat": round(l.cost.monthly_with_vat)} for l in lines],
            "total_hourly_incl_vat": round(tot.hourly_with_vat), "total_monthly_incl_vat": round(tot.monthly_with_vat)}
    disks = [{"app": a.name, "name": d.name, "mount_to": d.mount_to, "price": None, "billing": "unverified"} for a in plan.apps for d in a.disks]
    readiness = check_readiness(path).as_dict()
    if how.startswith("from one-click app "):
        how_code, how_params = "from_oneclick", {"app": how.removeprefix("from one-click app ")}
    elif how == "from fixed rules":
        how_code, how_params = "from_rules", {}
    elif how.startswith("from "):
        how_code, how_params = "from_compose", {"file": how.removeprefix("from ")}
    else:
        how_code, how_params = "investigated_by_agent", {}
    run.emit("plan_ready", {"how": how, "how_code": how_code, "how_params": how_params, "plan": public_plan(plan), "cost": cost, "disks": disks, "readiness": readiness, "network_id": network_id, "network": choice.public() if choice else None,
                            "would_send": _preview_requests(plan, cat, client, path, network_id),
                            "usage": {"calls": res.usage.calls, "prompt_tokens": res.usage.prompt_tokens, "completion_tokens": res.usage.completion_tokens} if res else None})
    run.emit("phase_finished", {"phase": "plan"})

    blockers = [n for n in plan.notes if n.level == "blocker"]
    if blockers:
        finish(False, reason="; ".join(n.message for n in blockers), stopped="blocked", reason_code="blocked",
               blockers=[{"code": n.code, "params": n.params, "message": n.message} for n in blockers])
        return PipelineResult(False, plan, stopped="blocked")

    # 3) confirm: only real runs spend money, so only they need approval
    run.emit("phase_started", {"phase": "confirm"})
    if execute_ and confirm_plan:
        if not channel.confirm(f"Create {len(plan.apps)} app(s) and {len(plan.databases)} database(s) on your Liara account? "
                               f"About {tot.hourly_with_vat:,.0f} toman/hour ({tot.monthly_with_vat:,.0f}/month) including VAT.",
                               price_delta_hourly=tot.hourly_with_vat, kind="plan", code="create_resources",
                               params={"apps": len(plan.apps), "databases": len(plan.databases), "buckets": len(plan.storage), "mail": 1 if plan.mail else 0, "hourly": round(tot.hourly_with_vat),
                                       "monthly": round(tot.monthly_with_vat), "readiness_warnings": readiness["counts"].get("warning", 0)}):
            run.emit("phase_finished", {"phase": "confirm", "approved": False})
            finish(False, reason="not approved", stopped="cancelled", reason_code="not_approved")
            return PipelineResult(False, plan, stopped="cancelled")
    if execute_:
        missing = apply_env(plan, channel.secret)
        if missing:
            run.emit("warning", {"step": "confirm", "code": "missing_env_values", "params": {"names": missing},
                                 "message": f"no value for {', '.join(missing)}; the app may fail to start until they are set"})
    if execute_ and choice and choice.new:   # approved: now the new private network is made
        network_id = client.create_network(choice.name)
    run.emit("phase_finished", {"phase": "confirm", "approved": True})

    # 4) provision, deploy, verify (and self-heal on real runs)
    run.emit("phase_started", {"phase": "deploy"})
    def wait_for_own_agent(d, f, c, root):
        diagnosis = {"code": d.code, "category": d.category, "summary": d.summary, "evidence": d.evidence, "params": d.params, "fixes": [x.as_event() for x in d.fixes]}
        info = {"log_tail": clean_lines(f.build_lines + f.runtime_lines, 60, run.secrets), "state": f.state, "http": f.http_detail, "platform": c.platform}
        return code_fix.handoff.wait(c.app, diagnosis, info, root)

    heal = HealIO(confirm=channel.confirm, ask_value=channel.secret, emit=run, auto_fix=auto_fix,
                  choose_code_fix=make_chooser(channel, code_fix, model_available=llm is not None) if code_fix else None,
                  await_handoff=wait_for_own_agent if code_fix else None,
                  repair=lambda d, f, c, root: repair_fix(llm, d, f, c, root, language=language, emit=run, secrets=run.secrets)) if execute_ else None
    try:
        out = execute(plan, cat, client, path, network_id=network_id, emit=run, heal=heal, max_heal_attempts=max_heal_attempts)
    except LiaraAPIError as e:
        run.emit("phase_finished", {"phase": "deploy", "ok": False})
        finish(False, reason=f"Liara rejected a request: {e}", stopped="error", reason_code=LIARA_STATUS_CODES.get(e.status, "liara_rejected"),
               reason_params={"status": e.status, "detail": liara_detail(e.body)})
        return PipelineResult(False, plan, stopped="error")
    run.emit("phase_finished", {"phase": "deploy", "ok": out.ok})
    applied = [{"code": st.fix.code, "kind": st.fix.kind, "params": st.fix.params, "cost_delta_hourly": round(st.fix.cost_delta_hourly, 2)}
               for st in out.heal_steps if st.applied and st.fix]
    extra = {"urls": out.urls, "fixes": applied, "heal_attempts": len({id(st.diagnosis) for st in out.heal_steps}), "follow_ups": out.follow_ups}
    if dry:
        extra["would_send"] = [request_view(a) for a in client.actions]
    if not out.ok:
        d = out.diagnosis
        extra |= {"failed_step": out.failed_step, "reason": out.reason, "reason_params": out.reason_params,
                  "diagnosis": {"code": d.code, "params": d.params, "summary": d.summary, "evidence": d.evidence, "fixes": [f.as_event() for f in d.fixes]} if d else None,
                  "reason_code": out.reason_code or (d.code if d else "deploy_failed")}
    finish(out.ok, **extra)
    return PipelineResult(out.ok, plan, out)
