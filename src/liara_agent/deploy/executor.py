"""Run a DeployPlan against Liara, step by step, emitting events (CLI prints them, the web UI streams them)."""
from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from liara_agent.deploy.history import commit_source, discard, save_source
from liara_agent.deploy.provision import provision_bucket, provision_mail
from liara_agent.deploy.randvars import substitute_random
from liara_agent.deploy.services import SVAR, mail_id, substitute_service_vars
from liara_agent.deploy.dbvars import DB_VAR as _DB_VAR, substitute_db_vars  # noqa: F401  (re-exported for callers and tests)
from liara_agent.heal.diagnose import Diagnosis, Failure, HealContext, diagnose
from liara_agent.heal.loop import HealIO, HealState, HealStep, heal_once
from liara_agent.liara.archive import build_archive
from liara_agent.liara.client import LiaraAPIError, LiaraClient
from liara_agent.liara.live import TIER_LABELS
from liara_agent.liara.live import LiveCatalog
from liara_agent.liara.pricing import choose_database_plan, choose_project_plan, database_cost, project_cost
from liara_agent.plan.model import DeployPlan

Emit = Callable[[str, dict], None]


@dataclass
class Outcome:
    ok: bool
    failed_step: str | None = None
    reason: str | None = None
    releases: dict[str, dict] = field(default_factory=dict)
    urls: dict[str, str] = field(default_factory=dict)
    reason_code: str | None = None
    reason_params: dict = field(default_factory=dict)
    diagnosis: Diagnosis | None = None
    heal_steps: list[HealStep] = field(default_factory=list)
    follow_ups: list[dict] = field(default_factory=list)  # what the person still has to do: DNS records for mail, a mode switch ...


def verify_http(url: str, *, attempts: int = 24, delay: float = 5, get=None, sleep=time.sleep) -> tuple[bool, str]:
    """A release turns READY slightly before the container answers (a real deploy returned 502 for ~10s)."""
    import httpx
    get = get or (lambda u: httpx.get(u, timeout=15, follow_redirects=True))
    last = "no response"
    for i in range(attempts):
        try:
            r = get(url)
            if r.status_code < 400:
                return True, f"HTTP {r.status_code}"
            last = f"HTTP {r.status_code}"
        except Exception as e:  # network errors are expected while the container starts
            last = type(e).__name__
        if i < attempts - 1:
            sleep(delay)
    return False, last


def execute(plan: DeployPlan, cat: LiveCatalog, client: LiaraClient, project_root: str | Path, *, network_id: str | None,
            emit: Emit = lambda kind, data: None, db_wait_seconds: float = 600, db_poll_seconds: float = 5.0, heal: HealIO | None = None,
            max_heal_attempts: int = 3) -> Outcome:
    root = Path(project_root)
    blockers = [n for n in plan.notes if n.level == "blocker"]
    if blockers:
        return Outcome(False, "preflight", "; ".join(n.message for n in blockers), reason_code="blocked")

    # 1) databases first: apps need their hostnames and credentials
    dbs: dict[str, dict] = {}
    api_ids: dict[str, str] = {}
    for d in plan.databases:
        plan_id, tier = choose_database_plan(cat, d.kind, min_ram_gb=d.min_ram_gb)
        emit("step_started", {"step": f"database:{d.id}"})
        db_id = client.create_database(d.id, d.kind, d.version or cat.default_database_versions[d.kind], plan_id, tier, network_id)
        emit("resource_created", {"kind": "database", "id": d.id, "type": d.kind, "plan": plan_id, "tier": tier, "tier_label": TIER_LABELS.get(tier, tier),
                                  "hourly_price": round(database_cost(cat, plan_id, tier).hourly_with_vat, 2),
                                  "monthly_price": round(database_cost(cat, plan_id, tier).monthly_with_vat), "dry_run": client.dry_run})
        api_ids[d.id] = db_id
        dbs[d.id] = {"hostname": d.id, "type": d.kind} if client.dry_run else _wait_for_db(client, db_id, db_wait_seconds, db_poll_seconds)
        emit("step_finished", {"step": f"database:{d.id}", "dry_run": client.dry_run})
        if not client.dry_run and not (dbs[d.id].get("username") and dbs[d.id].get("root_password")):
            got = dbs[d.id]
            missing = [k for k, f in (("username", "username"), ("password", "root_password"), ("port", "internalPort")) if not got.get(f)]
            return Outcome(False, f"database:{d.id}", f"database did not report credentials in time (status {got.get('status')}, missing: {', '.join(missing) or 'none'}); check the console",
                           reason_code="database_not_ready", reason_params={"id": d.id, "status": got.get("status"), "missing": missing})

    # 1b) buckets and email servers: created before the apps too, and referred to through placeholders filled in below
    services: dict[str, dict[str, str]] = {}
    follow_ups: list[dict] = []
    for st in plan.storage:
        values, fu = provision_bucket(client, cat, st, emit)
        services[f"S3:{st.id}"] = values
        follow_ups += fu
    if plan.mail:
        values, fu = provision_mail(client, cat, plan.mail, emit)
        services[f"MAIL:{mail_id(plan.mail.domain)}"] = values
        follow_ups += fu

    # 2) apps
    outcome = Outcome(True)
    outcome.follow_ups = follow_ups
    state_dir = root / ".agent-state"
    for a in plan.apps:
        step = f"app:{a.name}"
        emit("step_started", {"step": step})
        if not client.dry_run and client.app_exists(a.name):
            return Outcome(False, step, f"an app named {a.name!r} already exists; refusing to reuse it", reason_code="name_taken", reason_params={"name": a.name})
        plan_id, tier = choose_project_plan(cat, min_ram_gb=a.min_ram_gb)
        for attempt in range(4):   # app names are shared by every Liara user: when one is taken, a short random ending makes it free
            try:
                client.create_app(a.name, a.platform, plan_id, tier, network_id)
                break
            except LiaraAPIError as e:
                if e.status != 409 or attempt == 3:
                    raise
                renamed = f"{re.sub(r'-[0-9a-f]{4}$', '', a.name)[:26]}-{secrets.token_hex(2)}"
                emit("warning", {"step": step, "code": "app_renamed", "params": {"old": a.name, "new": renamed}, "message": f"the name {a.name} is taken on Liara: using {renamed}"})
                a.env = {k: v.replace(f"{a.name}.liara.run", f"{renamed}.liara.run") for k, v in a.env.items()}   # a ready-made app's own address
                a.name = renamed
                step = f"app:{a.name}"
        emit("resource_created", {"kind": "app", "id": a.name, "platform": a.platform, "plan": plan_id, "tier": tier, "tier_label": TIER_LABELS.get(tier, tier),
                                  "hourly_price": round(project_cost(cat, plan_id, tier).hourly_with_vat, 2),
                                  "monthly_price": round(project_cost(cat, plan_id, tier).monthly_with_vat), "dry_run": client.dry_run})
        a.env = substitute_random(a.env)  # once: a fix that re-applies the environment must not change a secret the app already uses
        env = substitute_service_vars(substitute_db_vars(a.env, dbs), services)
        left = sorted({m.group(0) for v in env.values() for m in [*_DB_VAR.finditer(v), *SVAR.finditer(v)]})
        if left:
            emit("warning", {"step": step, "code": "unresolved_db_vars", "params": {"names": left}, "message": f"unresolved database variables: {left}"})
        if env:
            client.set_env(a.name, env)
        if a.disks:  # a release can only mount a disk that exists: the CLI makes the person create it first, so the agent does
            have = _existing_disks(client, a.name)
            for d in fit_disks(a.disks, cat.project_plans[plan_id].volume if plan_id in cat.project_plans else None):
                if d.name not in have:
                    client.create_disk(a.name, d.name, d.size_gb)
                    emit("build", {"step": step, "line": f"created disk {d.name} ({d.size_gb} GB) to mount at {d.mount_to}"})
        ctx = HealContext(app=a.name, platform=a.platform, port=a.port or 80, plan_id=plan_id, tier=tier, cat=cat,
                          env_names=set(env), db_kinds={d.kind for d in plan.databases}, db_api_ids=dict(api_ids), env_templates=dict(a.env),
                          project_files={f for f in ("requirements.txt", "package.json") if (root / a.path / f).is_file()})
        hstate = HealState(source_dir=root / a.path)

        def recreate(new_plan: str, new_tier: str, a=a, env=env) -> None:
            client.delete_app(a.name)
            for _ in range(24):  # a deleted name stays reserved for a short while
                if not client.app_exists(a.name):
                    break
                time.sleep(5)
            for attempt in range(12):
                try:
                    client.create_app(a.name, a.platform, new_plan, new_tier, network_id)
                    break
                except LiaraAPIError:
                    if attempt == 11:
                        raise
                    time.sleep(5)
            if env:
                client.set_env(a.name, env)
        ctx.recreate = recreate
        if heal and not client.dry_run and not a.image and a.platform in ("node", "next") and "package.json" in ctx.project_files:
            _offer_node_engine_patch(a, ctx, hstate, client, heal, root / a.path, state_dir, cat)

        def release_once(a=a, ctx=ctx, hstate=hstate, step=step) -> Failure | None:
            build: list[str] = []
            disks = [{"name": d.name, "mountTo": d.mount_to} for d in a.disks]
            if a.image:
                rid = client.create_release(a.name, None, a.platform, ctx.port, disks=disks, image=a.image, args=a.args or None)
            else:
                arc = build_archive(hstate.source_dir, max_mb=_source_limit(cat, ctx.plan_id, ctx.tier))
                for secret in arc.excluded_secrets:
                    emit("warning", {"step": step, "code": "secret_file_not_uploaded", "params": {"file": secret},
                                         "message": f"{secret} was not uploaded (secrets are set as environment variables)"})
                saved = None
                try:
                    sid = client.upload_source(a.name, arc.path)
                    if not client.dry_run:
                        saved = save_source(state_dir, arc.path)  # kept so this release can be rolled back to later
                finally:
                    arc.path.unlink(missing_ok=True)
                pconf = {**a.platform_config, **hstate.platform_config} or None
                try:
                    rid = client.create_release(a.name, sid, a.platform, ctx.port, disks=disks, args=a.args or None, platformConfig=pconf)
                except BaseException:
                    discard(saved)
                    raise
                if saved:
                    commit_source(saved, state_dir, rid, {"app": a.name, "platform": a.platform, "port": ctx.port, "platform_config": pconf or {}, "disks": disks})

            def on_event(kind, text):
                build.append(text)
                emit(kind, {"step": step, "line": text})
            rel = client.wait_for_release(rid, on_event)
            outcome.releases[a.name] = rel
            if rel.get("state") in ("FAILED", "UNHEALTHY"):
                return Failure(step, rel.get("state"), rel.get("failReason"), build, _runtime(client, a.name, rid))
            return None

        def verify(a=a, ctx=ctx, step=step) -> Failure | None:
            if client.dry_run or a.role != "web":
                return None
            url = f"https://{a.name}.liara.run/"
            ok, detail = verify_http(url)
            logs: list[str] | None = None
            for extra in range(3):   # some apps need minutes on their first start (n8n ran 6 minutes of database set-up on the smallest plan): wait on while the logs show no crash
                if ok or not (detail in ("HTTP 502", "HTTP 503", "HTTP 504") or detail in ("ConnectError", "ReadTimeout", "ConnectTimeout")):
                    break
                logs = _runtime(client, a.name, None)
                if diagnose(Failure(step, "READY", None, [], logs, detail), ctx).code != "not_serving":
                    break
                emit("warning", {"step": step, "code": "slow_start", "params": {"minutes": 2 * (extra + 1)}, "message": "the app is still starting: waiting longer"})
                logs = None
                ok, detail = verify_http(url)
            emit("test_result", {"step": step, "name": "http_200", "ok": ok, "detail": detail, "url": url})
            if ok:
                outcome.urls[a.name] = url
                return None
            return Failure(step, "READY", None, [], logs if logs else _runtime(client, a.name, None, retries=3), detail)

        failure = release_once() or verify()
        attempts = 0
        while failure and heal and attempts < max_heal_attempts and not client.dry_run:
            attempts += 1
            emit("heal_attempt", {"step": step, "attempt": attempts, "max_attempts": max_heal_attempts})
            if not heal_once(failure, ctx, client, hstate, heal, root / a.path, state_dir):
                break
            failure = (release_once() if hstate.redeploy_needed else None) or verify()
        if failure:
            outcome.ok, outcome.failed_step = False, step
            outcome.reason = failure.reason or failure.http_detail or failure.state
            outcome.diagnosis = diagnose(failure, ctx)
            outcome.heal_steps = hstate.steps
            return outcome
        outcome.heal_steps += hstate.steps
        emit("step_finished", {"step": step, "state": outcome.releases.get(a.name, {}).get("state"), "dry_run": client.dry_run})
    if plan.wordpress:
        _deliver_wordpress(plan.wordpress, root, client, emit, outcome)
    return outcome


def fit_disks(disks: list, volume_gb: float | None) -> list:
    """The app's disks must fit inside the plan's storage (a real run: 1 GB + 5 GB on a 5 GB plan was refused with `not_enough_storage_space`). The largest disks give
    way first, one GB at a time, never below 1 GB."""
    fitted = [d.model_copy() for d in disks]
    if volume_gb is None:
        return fitted
    while sum(d.size_gb for d in fitted) > volume_gb:
        biggest = max(fitted, key=lambda d: d.size_gb)
        if biggest.size_gb <= 1:
            break
        biggest.size_gb -= 1
    return fitted


def _existing_disks(client: LiaraClient, app: str, attempts: int = 3, wait: float = 4.0) -> set[str]:
    """The names of the app's disks. Liara answered 500 to this read for an app created a moment before (seen on a real run), so it is retried and, if it keeps
    failing, treated as 'none known': creating a disk that exists is rejected by Liara and that error is then shown."""
    if client.dry_run:
        return set()
    for i in range(attempts):
        try:
            return {d.get("name") for d in client.disks(app)}
        except httpx.HTTPError:
            if i < attempts - 1:
                time.sleep(wait)
    return set()


def _deliver_wordpress(spec, root: Path, client: LiaraClient, emit, outcome: Outcome) -> None:
    """Send the project's own wp-content to the WordPress app's disk: an FTPS access is made through the API, used once, and removed. A failure here does not fail
    the deploy (the site is up with Liara's WordPress); it becomes a follow-up the person can act on."""
    from liara_agent import wpdeliver
    step = f"app:{spec.app}"
    follow = {"code": "wordpress_db_import", "params": {"app": spec.app}, "records": []}
    try:
        plan = wpdeliver.collect(root)
    except wpdeliver.WpError as e:
        outcome.follow_ups.append({"code": "wordpress_nothing_sent", "params": {"why": e.code}, "records": []})
        outcome.follow_ups.append(follow)
        return
    access = client.create_ftp(spec.app, spec.disk, "launchpad")
    emit("build", {"step": step, "line": f"sending {len(plan['files'])} wp-content files over FTPS"})
    if client.dry_run:
        outcome.follow_ups.append(follow)
        return
    host, _, port = str(access.get("host", "")).partition(":")
    try:
        out = wpdeliver.upload(plan, host=host, port=int(port) if port.isdigit() else 2112, user=access["username"], password=access["password"])
        emit("build", {"step": step, "line": f"sent {out['sent']} files"})
        outcome.follow_ups.append({"code": "wordpress_permissions", "params": {"app": spec.app}, "records": []})
    except wpdeliver.WpError as e:
        outcome.follow_ups.append({"code": "wordpress_upload_failed", "params": {"app": spec.app, "why": e.code}, "records": []})
    finally:
        try:
            client.delete_ftp(access["id"])
        except Exception as e:  # the access also dies with the app and cleanup retries it; say so instead of hiding it
            status = getattr(e, "status", None) or getattr(getattr(e, "response", None), "status_code", None)
            emit("build", {"step": step, "line": f"could not remove the temporary FTPS access now ({type(e).__name__} {status or ''}); it is removed with the app"})
    outcome.follow_ups.append(follow)


def _runtime(client: LiaraClient, app: str, release_id: str | None, retries: int = 0, wait: float = 6.0) -> list[str]:
    """Runtime log lines for a failed release. A container that has just crashed may not have its lines indexed yet, so an empty answer is retried."""
    for i in range(retries + 1):
        try:
            if lines := client.runtime_logs(app, release_id):
                return lines
        except Exception:  # logs are best-effort evidence; never let them mask the real failure
            pass
        if i < retries:
            time.sleep(wait)
    return []


def _source_limit(cat: LiveCatalog, plan_id: str, tier: str) -> float | None:
    return cat.project_bundles.get(plan_id, {}).get(tier).max_source_size or None


def _wait_for_db(client: LiaraClient, db_id: str, seconds: float, poll: float = 5.0) -> dict:
    """Ready = status RUNNING, credentials present, and the SAME credentials on two consecutive reads. (A real deploy that took the
    credentials the instant `username` appeared got a login failure; waiting for them to settle avoids that.)"""
    deadline, db, prev = time.time() + seconds, {}, None
    while True:
        db = client.database(db_id)
        port = db.get("internalPort") or db.get("port")
        if db.get("status") in (None, "RUNNING") and db.get("username") and db.get("root_password") and port:
            key = (db["username"], db["root_password"], port)
            if key == prev:
                return db
            prev = key
        else:
            prev = None
        if time.time() >= deadline:
            return db
        time.sleep(poll)


def _offer_node_engine_patch(a, ctx: HealContext, hstate: HealState, client: LiaraClient, heal: HealIO, project: Path, state_dir: Path, cat: LiveCatalog) -> None:
    """A package.json that pins engines.node to a version the build will not use makes yarn/npm stop before building. Offer the fix up front
    instead of after a failed build (a build that fails this way still costs up to the tier's whole build time)."""
    from liara_agent.detect.versions import engine_conflict
    from liara_agent.heal.diagnose import Diagnosis, Fix, node_for_engine
    from liara_agent.heal.loop import HealStep, apply_fix
    from liara_agent.liara.catalog import catalog as _catalog

    versions = _catalog()["platforms"].get(a.platform, {}).get("versions") or {}
    runtime = a.platform_config.get("version") or versions.get("defaultVersion")
    if not runtime or not (pin := engine_conflict(project, int(str(runtime).split(".")[0]))):
        return
    version, relaxed = node_for_engine(pin)
    fix = Fix("code_patch", f"build with Node {version} and relax engines.node to {relaxed} in package.json (in a copy of your project)",
              {"file": "package.json", "engines_node": relaxed, "platform_version": version, "expected": pin}, code="patch_node_engine")
    diagnosis = Diagnosis("node_engine_mismatch", f"package.json pins Node {pin}, which the build runtime does not satisfy, so the install would stop before building.",
                          [], [fix], params={"expected": pin, "version": version, "engines_node": relaxed})
    applied, note = apply_fix(fix, ctx, client, hstate, heal, project, state_dir)
    hstate.steps.append(HealStep(diagnosis, fix, applied, note))
    heal.emit("fix_applied" if applied else "fix_declined", {"fix": fix.description, "note": note, "kind": fix.kind, "params": fix.params,
                                                             "cost_delta_hourly": 0.0, "app": ctx.app, "code": fix.code})
