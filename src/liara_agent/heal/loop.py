"""Self-heal: diagnose a failed deploy, apply what is safe, ask before anything that costs money or changes code, retry."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from liara_agent.heal.apply import apply_append, apply_engines_node, patched_copy, save_patches
from liara_agent.heal.diagnose import Diagnosis, Failure, Fix, HealContext, diagnose
from liara_agent.liara.client import LiaraClient

Emit = Callable[[str, dict], None]


@dataclass
class HealIO:
    confirm: Callable[..., bool] = lambda prompt, **kw: False  # default: never spend or change code without a yes; called as confirm(prompt, price_delta_hourly=..., kind=...)
    ask_value: Callable[[str], str | None] = lambda name: None
    emit: Emit = lambda kind, data: None


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


def _signature(d: Diagnosis, fix: Fix) -> str:
    return f"{d.code}:{fix.kind}:{sorted(fix.params.items())}"


def heal_once(failure: Failure, ctx: HealContext, client: LiaraClient, state: HealState, io: HealIO,
              project: Path, state_dir: Path) -> bool:
    """Try one fix. Returns True if something was changed and the deploy should be checked/retried."""
    d = diagnose(failure, ctx)
    io.emit("diagnosis", {"code": d.code, "params": d.params, "summary": d.summary, "evidence": d.evidence, "fixes": [f.as_event() for f in d.fixes], "app": ctx.app})
    if not d.retryable:
        state.steps.append(HealStep(d, None, False, "not something a redeploy can fix"))
        return False
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
    return False


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
    if fix.kind == "code_patch":
        if not io.confirm(f"{fix.description} (applied to a copy of your project, never to your files). Apply?", kind="patch", code=fix.code, params=fix.params):
            return False, "declined"
        copy_root = patched_copy(project, state_dir, ctx.app)
        if "engines_node" in fix.params:
            diff = apply_engines_node(copy_root, fix.params["engines_node"])
            state.platform_config["version"] = fix.params["platform_version"]
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
