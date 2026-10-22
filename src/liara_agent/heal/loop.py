"""Self-heal: diagnose a failed deploy, apply what is safe, ask before anything that costs money or changes code, retry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from liara_agent.heal.apply import apply_append, apply_engines_node, apply_npm_dependency, patched_copy, save_patches
from liara_agent.heal.diagnose import Diagnosis, Failure, Fix, HealContext, diagnose
from liara_agent.liara.client import LiaraClient

Emit = Callable[[str, dict], None]
MAX_REPAIRS = 2   # a model-made fix is tried at most twice per app; a third failure is reported, not retried


@dataclass
class HealIO:
    confirm: Callable[..., bool] = lambda prompt, **kw: False  # default: never spend or change code without a yes; called as confirm(prompt, price_delta_hourly=..., kind=...)
    ask_value: Callable[[str], str | None] = lambda name: None
    emit: Emit = lambda kind, data: None
    # Proposes a fix for a code problem no rule can fix (a model reads the copy of the project): (diagnosis, failure, ctx, copy_root) -> Fix | None. None = no model or no fix.
    repair: Callable[..., Fix | None] | None = None
    # Who fixes a code problem: (diagnosis, ctx) -> "sakoo" | "own_agent". None: Sakoo does (the CLI has no developer agent to hand over to).
    choose_code_fix: Callable[..., str] | None = None
    # Waits for the developer's own agent (MCP): (diagnosis, failure, ctx, copy_root) -> HandoffResult | None (nobody answered in time)
    await_handoff: Callable[..., object] | None = None
    auto_fix: bool = False  # apply patches to the agent's copy without asking (the person turned this on); anything that costs money is still asked


@dataclass
class HealStep:
    diagnosis: Diagnosis
    fix: Fix | None
    applied: bool
    note: str = ""


@dataclass
class HealState:
    source_dir: Path  # where the next upload comes from (a patched copy after a code fix)
    tried: set[str] = field(default_factory=set)
    steps: list[HealStep] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)
    redeploy_needed: bool = True
    platform_config: dict = field(default_factory=dict)  # sent with every release, e.g. {"version": "14"} for node
    repairs: int = 0   # how many times the repair agent was asked in this run
    code_choice: str | None = None   # "sakoo" | "own_agent": asked once per run, kept for the next code problem


def _signature(d: Diagnosis, fix: Fix) -> str:
    return f"{d.code}:{fix.kind}:{sorted(fix.params.items())}"


def heal_once(failure: Failure, ctx: HealContext, client: LiaraClient, state: HealState, io: HealIO,
              project: Path, state_dir: Path) -> bool:
    """Try one fix. Returns True if something was changed and the deploy should be checked/retried."""
    d = diagnose(failure, ctx)
    io.emit("diagnosis", {"code": d.code, "category": d.category, "params": d.params, "summary": d.summary, "evidence": d.evidence, "fixes": [f.as_event() for f in d.fixes], "app": ctx.app})
    if not d.retryable:
        state.steps.append(HealStep(d, None, False, "not something a redeploy can fix"))
        return False
    if d.category == "code" and io.choose_code_fix:
        state.code_choice = state.code_choice or io.choose_code_fix(d, ctx)
        if state.code_choice == "own_agent" and io.await_handoff:
            handled = _handoff(d, failure, ctx, state, io, project, state_dir)
            if handled is not None:
                return handled
    for fix in d.fixes:
        sig = _signature(d, fix)
        if fix.kind == "manual" or sig in state.tried:
            continue
        state.tried.add(sig)
        applied, note = _apply(fix, ctx, client, state, io, project, state_dir)
        state.steps.append(HealStep(d, fix, applied, note))
        io.emit("fix_applied" if applied else "fix_declined", {"fix": fix.description, "note": note, "kind": fix.kind, "params": fix.params,
                                                               "cost_delta_hourly": round(fix.cost_delta_hourly, 2), "app": ctx.app, "code": fix.code})
        if applied:
            return True
    if d.category == "code" and io.repair and state.repairs < MAX_REPAIRS:
        state.repairs += 1
        fix = io.repair(d, failure, ctx, patched_copy(project, state_dir, ctx.app))
        if fix is None:
            return False
        sig = _signature(d, fix)
        if sig in state.tried:   # the model proposed what it already proposed: stop instead of looping
            return False
        state.tried.add(sig)
        applied, note = _apply(fix, ctx, client, state, io, project, state_dir)
        state.steps.append(HealStep(d, fix, applied, note))
        io.emit("fix_applied" if applied else "fix_declined", {"fix": fix.description, "note": note, "kind": fix.kind, "params": fix.params,
                                                               "cost_delta_hourly": 0, "app": ctx.app, "code": fix.code})
        return applied
    return False


def _handoff(d: Diagnosis, failure: Failure, ctx: HealContext, state: HealState, io: HealIO, project: Path, state_dir: Path) -> bool | None:
    """The developer's own agent fixes the code. True: something changed, redeploy. False: nobody answered, report the failure. None: the person chose Sakoo after all."""
    res = io.await_handoff(d, failure, ctx, patched_copy(project, state_dir, ctx.app))
    if res is None:
        state.steps.append(HealStep(d, None, False, "no fix arrived from your own agent"))
        return False
    if res.how == "sakoo":
        state.code_choice = "sakoo"
        return None
    if res.how == "redeploy":
        state.source_dir = res.source_dir
        state.redeploy_needed = True
        state.steps.append(HealStep(d, Fix("manual", "your own agent pushed the fix; the newest code of the repository was released", code="handoff_redeploy"), True, "released the newest code"))
        return True
    from liara_agent.web import chat_code

    chat_code.apply(patched_copy(project, state_dir, ctx.app), res.plan, state_dir / "handoff-backup")
    state.diffs.append(res.plan["diff"] + "\n")
    io.emit("patch_ready", {"file": ", ".join(f["path"] for f in res.plan["files"]), "diff": res.plan["diff"]})
    state.source_dir = patched_copy(project, state_dir, ctx.app)
    save_patches(state_dir, state.diffs)
    state.redeploy_needed = True
    state.steps.append(HealStep(d, Fix("manual", "your own agent sent the fix", code="handoff_fix"), True, "applied the files from your agent"))
    return True


def _approved(io: HealIO, fix: Fix, prompt: str) -> bool:
    """A change to the agent's copy of the code: asked, unless the person turned auto-fix on (then it is applied at once and the page is told)."""
    if io.auto_fix:
        io.emit("fix_auto_applied", {"code": fix.code, "kind": fix.kind, "text": fix.description, "params": fix.params})
        return True
    return io.confirm(prompt, kind="patch", code=fix.code, params=fix.params)


def _apply_repair(fix: Fix, ctx: HealContext, client: LiaraClient, state: HealState, io: HealIO, project: Path, state_dir: Path) -> tuple[bool, str]:
    """Write a repair agent's file changes to the copy and set its database-based variables on the app."""
    plan, env = (fix.payload or {}).get("plan"), (fix.payload or {}).get("env") or {}
    shown = {"files": [f["path"] for f in (plan or {}).get("files", [])], "env": sorted(env), "summary": fix.params.get("summary", "")}
    # the person decides on what will change: the diff is part of the question (cut at 8000 characters; the whole diff is kept as patch_ready)
    asked = {**shown, **({"diff": plan["diff"][:8000]} if plan else {})}
    if not _approved(io, Fix(fix.kind, fix.description, asked, code=fix.code), f"{fix.description}. Apply to a copy of your project (never your files)?"):
        return False, "declined"
    if env:
        from liara_agent.deploy.dbvars import DB_VAR, substitute_db_vars

        fresh = {pid: client.database(api_id) for pid, api_id in ctx.db_api_ids.items()}
        client.set_env(ctx.app, substitute_db_vars(env, fresh))   # an env change redeploys the app by itself (verified on Liara)
        ctx.env_names |= set(env)
        ctx.env_templates.update(env)
        state.redeploy_needed = False
    if plan:
        from liara_agent.web import chat_code

        copy_root = patched_copy(project, state_dir, ctx.app)
        chat_code.apply(copy_root, plan, state_dir / "repair-backup")
        state.diffs.append(plan["diff"] + "\n")
        io.emit("patch_ready", {"file": ", ".join(shown["files"]), "diff": plan["diff"]})
        state.source_dir = copy_root
        save_patches(state_dir, state.diffs)
        state.redeploy_needed = True
    return True, "changed " + ", ".join(shown["files"] + shown["env"])


def apply_fix(fix: Fix, ctx: HealContext, client: LiaraClient, state: HealState, io: HealIO, project: Path, state_dir: Path) -> tuple[bool, str]:
    return _apply(fix, ctx, client, state, io, project, state_dir)


def _apply(fix: Fix, ctx: HealContext, client: LiaraClient, state: HealState, io: HealIO, project: Path, state_dir: Path) -> tuple[bool, str]:
    if fix.kind == "resize":
        price = f" (+{fix.cost_delta_hourly:,.0f} toman/hour incl. VAT, about +{fix.cost_delta_hourly * 720:,.0f}/month)" if fix.cost_delta_hourly else ""
        if not io.confirm(f"{fix.description}{price}. Apply?", price_delta_hourly=fix.cost_delta_hourly, kind="fix", code=fix.code, params=fix.params):
            return False, "declined"
        if fix.params["tier"] != ctx.tier:  # Liara cannot change the tier of an existing app: recreate it (it has no data: its build never finished)
            if ctx.recreate is None:
                return False, "this tier cannot be changed automatically"
            ctx.recreate(fix.params["plan"], fix.params["tier"])
        else:
            client.resize_app(ctx.app, fix.params["plan"])
        ctx.plan_id, ctx.tier = fix.params["plan"], fix.params["tier"]
        from liara_agent.liara.live import TIER_LABELS
        from liara_agent.liara.pricing import project_cost
        io.emit("resource_updated", {"kind": "app", "id": ctx.app, "plan": ctx.plan_id, "tier": ctx.tier, "tier_label": TIER_LABELS.get(ctx.tier, ctx.tier),
                                     "hourly_price": round(project_cost(ctx.cat, ctx.plan_id, ctx.tier).hourly_with_vat, 2),
                                     "monthly_price": round(project_cost(ctx.cat, ctx.plan_id, ctx.tier).monthly_with_vat),
                                     "previous": {"plan": fix.params.get("from_plan", ctx.plan_id), "tier": fix.params.get("from_tier", ctx.tier)}})
        state.redeploy_needed = True
        return True, "plan changed"
    if fix.kind == "set_port":
        if not io.confirm(f"{fix.description}. Apply?", kind="fix", code=fix.code, params=fix.params):
            return False, "declined"
        client.update_port(ctx.app, fix.params["port"])
        ctx.port = fix.params["port"]
        state.redeploy_needed = False  # update-port redeploys by itself
        return True, "port changed"
    if fix.kind == "refresh_db_credentials":
        from liara_agent.deploy.dbvars import DB_VAR, substitute_db_vars

        fresh = {pid: client.database(api_id) for pid, api_id in ctx.db_api_ids.items()}
        env = {k: v for k, v in substitute_db_vars(ctx.env_templates, fresh).items() if DB_VAR.search(ctx.env_templates[k])}
        if not env:
            return False, "no database variables to refresh"
        client.set_env(ctx.app, env)  # an env change redeploys the app by itself (verified on Liara)
        state.redeploy_needed = False
        return True, f"refreshed {', '.join(sorted(env))}"
    if fix.kind == "set_env":
        values = {n: v for n in fix.params["names"] if (v := io.ask_value(n))}
        if not values:
            return False, "no value provided"
        client.set_env(ctx.app, values)
        ctx.env_names |= set(values)
        state.redeploy_needed = False
        return True, f"set {', '.join(values)}"
    if fix.kind == "code_changes":
        return _apply_repair(fix, ctx, client, state, io, project, state_dir)
    if fix.kind == "code_patch":
        if not _approved(io, fix, f"{fix.description} (applied to a copy of your project, never to your files). Apply?"):
            return False, "declined"
        copy_root = patched_copy(project, state_dir, ctx.app)
        if "engines_node" in fix.params:
            diff = apply_engines_node(copy_root, fix.params["engines_node"])
            state.platform_config["version"] = fix.params["platform_version"]
        elif "add_dependency" in fix.params:
            try:
                diff = apply_npm_dependency(copy_root, fix.params["add_dependency"])
            except LookupError as e:
                return False, str(e)
        else:
            diff = apply_append(copy_root, fix.params["file"], fix.params["append"])
        if diff is None and "engines_node" not in fix.params:
            return False, "already present"
        if diff:
            state.diffs.append(diff)
            io.emit("patch_ready", {"file": fix.params["file"], "diff": diff})
        state.source_dir = copy_root
        save_patches(state_dir, state.diffs)
        state.redeploy_needed = True
        return True, f"patched {fix.params['file']} (saved to {state_dir / 'fixes.patch'})"
    return False, f"cannot apply {fix.kind} automatically"
