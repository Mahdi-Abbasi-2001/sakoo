"""Why is a RUNNING app failing or slow? The signals are gathered (an HTTP probe, the runtime logs, the releases, CPU and memory against the plan's limits) and the likely
causes are ranked, each with the lines or numbers that point to it and the fix that follows. Nothing here changes anything: a fix is only named, and the person approves it
as a card (resize, set a variable, roll back, edit the code and release it)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from liara_agent.heal.diagnose import Failure, HealContext, diagnose
from liara_agent.liara.live import LiveCatalog

SLOW_MS = 2000
RECENT_RELEASE_MINUTES = 60
STARTING_MINUTES = 8
ERROR_LINE = re.compile(r"(?i)\b(error|exception|traceback|fatal|unhandled|panic|econnrefused|eaddrinuse|enomem|segfault|killed)\b")
OOM = re.compile(r"(?i)out of memory|oomkilled|\bkilled\b|heap out of memory|memoryerror|cannot allocate memory|enomem")
CODE_ERROR = re.compile(r"(?i)traceback \(most recent call last\)|\b(TypeError|ReferenceError|SyntaxError|AttributeError|KeyError|ValueError|NameError|ImportError|UnhandledPromiseRejection)\b|cannot read propert|is not a function|undefined is not")
# what the heal loop already knows how to read from logs: these mean the app itself is misconfigured, not slow
SPECIFIC = {"out_of_memory", "missing_python_module", "missing_node_module", "missing_env", "outbound_blocked", "db_auth_failed", "db_localhost", "wrong_port", "bound_to_localhost"}


@dataclass
class Snapshot:
    app: str
    url: str | None = None
    http_status: int | None = None
    http_ms: int | None = None
    http_error: str | None = None            # the kind of failure when there was no answer: ConnectError, ReadTimeout...
    logs: list[str] = field(default_factory=list)
    platform: str = "docker"
    plan_id: str = ""
    tier: str = "basic"
    port: int | None = None
    ram_gb: float = 0.0
    cpu_cores: float = 0.0
    mem_now: float | None = None             # bytes
    mem_max: float | None = None
    cpu_now: float | None = None             # cores
    cpu_max: float | None = None
    current_release: dict | None = None      # {id, state, age_minutes}
    previous_release: dict | None = None     # the newest earlier release that ran fine and can be rolled back to: {id, age_minutes}
    env_names: set[str] = field(default_factory=set)
    cat: LiveCatalog | None = None


def probe(get, url: str, timeout: float = 20) -> tuple[int | None, int | None, str | None]:
    """(status, milliseconds, error kind) of one request to the site. A slow first answer is asked once more: the first request after a restart is often cold, and a slowness that
    is not there the second time is not worth reporting."""
    import time
    best: tuple[int | None, int | None, str | None] = (None, None, None)
    for attempt in range(2):
        t0 = time.time()
        try:
            r = get(url, timeout=timeout, follow_redirects=True)
            got = (r.status_code, round((time.time() - t0) * 1000), None)
        except Exception as e:  # the kind of failure only: the message may quote the address
            got = (None, None, type(e).__name__)
        if attempt == 0 or (got[0] is not None and (best[0] is None or (got[1] or 0) < (best[1] or 0))):
            best = got
        if best[0] is not None and best[0] < 500 and (best[1] or 0) < SLOW_MS:
            break
        if best[0] is not None and best[0] >= 500:
            break
    return best


def age_minutes(iso: str | None, now: datetime | None = None) -> float | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0.0, ((now or datetime.now(timezone.utc)) - t).total_seconds() / 60)


def error_lines(logs: list[str], n: int = 6) -> list[str]:
    seen, out = set(), []
    for ln in reversed(logs):
        ln = ln.strip()
        if ERROR_LINE.search(ln) and ln[:80] not in seen:
            seen.add(ln[:80])
            out.append(ln[:220])
            if len(out) == n:
                break
    return list(reversed(out))


def _ratio(value: float | None, limit: float) -> float | None:
    return None if value is None or limit <= 0 else value / limit


def _cause(code: str, score: int, evidence: list[str] | None = None, fixes: list[dict] | None = None, **params) -> dict:
    seen: list[str] = []
    for e in evidence or []:   # the same line often appears in a traceback twice
        if e.strip() not in seen:
            seen.append(e.strip())
    return {"code": code, "score": score, "params": params, "evidence": seen, "fixes": fixes or []}


def _bigger_plan(s: Snapshot) -> dict | None:
    if not s.cat or s.plan_id not in s.cat.project_plans:
        return None
    cur = s.cat.project_plans[s.plan_id].ram.amount
    best = None
    for pid, p in s.cat.project_plans.items():
        if p.available and pid.endswith("-g2") and p.ram.amount > cur and s.tier in s.cat.project_bundles.get(pid, {}):
            if best is None or p.ram.amount < s.cat.project_plans[best].ram.amount:
                best = pid
    return {"tool": "resize_plan", "args": {"plan": best}, "text": f"move to {best} ({s.cat.project_plans[best].ram.amount:g} GB RAM instead of {cur:g})"} if best else None


CAUSE_TEXT = {
    "still_starting": "the new release is still starting", "out_of_memory": "the app ran out of memory", "memory_exhausted": "memory is at the plan's limit",
    "cpu_saturated": "CPU is saturated", "bad_release": "the newest release is the first suspect", "release_unhealthy": "the current release is not healthy",
    "code_error": "the code throws an error", "missing_env": "an environment variable is missing", "missing_python_module": "a Python module is missing",
    "missing_node_module": "a Node package is missing", "outbound_blocked": "the app cannot reach an outside service", "db_auth_failed": "the database refuses the login",
    "db_localhost": "the app looks for its database on localhost", "wrong_port": "the app listens on another port", "bound_to_localhost": "the app listens only on 127.0.0.1",
    "slow_response": "the site is slow while no resource is short", "errors_in_logs": "errors are being logged", "not_serving": "the app is not serving",
}


def summary_text(result: dict) -> str:
    """One sentence for an alert: the most likely cause and the log line or number behind it."""
    if not result.get("causes"):
        return "No clear cause was found."
    top = result["causes"][0]
    names = top.get("params", {}).get("names")
    text = CAUSE_TEXT.get(top["code"], top["code"]) + (f" ({', '.join(names)})" if names else "")
    return f"Most likely: {text}." + (f" Evidence: {top['evidence'][0][:140]}" if top.get("evidence") else "")


def _advice(s: Snapshot, failing: bool, mem_ratio: float | None, cpu_ratio: float | None) -> list[dict]:
    """What to improve BEFORE it breaks: the app is answering, but a resource is running close to its limit or the site is on the slow side."""
    out: list[dict] = []
    fix = _bigger_plan(s)
    if mem_ratio is not None and 0.7 <= mem_ratio < 0.9 and not failing:
        out.append({"code": "memory_tight", "params": {"peak_percent": round(mem_ratio * 100)}, "fixes": [fix] if fix else []})
    if cpu_ratio is not None and 0.6 <= cpu_ratio < 0.85 and not failing:
        out.append({"code": "cpu_tight", "params": {"peak_percent": round(cpu_ratio * 100)}, "fixes": [fix] if fix else []})
    if not failing and s.http_ms is not None and 1000 <= s.http_ms < SLOW_MS:
        out.append({"code": "a_little_slow", "params": {"ms": s.http_ms}, "fixes": []})
    return out


def rank(s: Snapshot) -> dict:
    """{state: healthy|degraded|down, signals, causes[{code, score, params, evidence, fixes}]}: the most likely cause first."""
    failing = s.http_status is None or s.http_status >= 500
    slow = s.http_status is not None and s.http_status < 500 and (s.http_ms or 0) >= SLOW_MS
    mem_ratio, cpu_ratio = _ratio(s.mem_max, s.ram_gb * 1024 ** 3), _ratio(s.cpu_max, s.cpu_cores)
    errors = error_lines(s.logs)
    cur_age = (s.current_release or {}).get("age_minutes")
    recent = cur_age is not None and cur_age <= RECENT_RELEASE_MINUTES
    causes: list[dict] = []

    # 1) the logs name the problem themselves (module missing, variable missing, wrong port, database refused ...): the heal loop's own reader
    named = None
    if s.cat and s.plan_id in s.cat.project_plans and (failing or slow or errors):
        ctx = HealContext(app=s.app, platform=s.platform, port=s.port, plan_id=s.plan_id, tier=s.tier, cat=s.cat, env_names=set(s.env_names))
        d = diagnose(Failure("live", "READY", None, [], s.logs, f"HTTP {s.http_status}" if s.http_status else s.http_error), ctx)
        if d.code in SPECIFIC:
            named = d
            fixes = []
            for f in d.fixes:
                if f.code == "resize_plan" and f.params.get("plan"):
                    fixes.append({"code": f.code, "text": f.description, "params": f.params, "tool": "resize_plan", "args": {"plan": f.params["plan"]}})
                elif f.code == "set_env":
                    fixes += [{"code": "set_env", "text": f"set {n}", "params": {"names": [n]}, "tool": "set_env", "args": {"name": n}} for n in f.params.get("names", [])]
                else:
                    fixes.append({"code": f.code, "text": f.description, "params": f.params})
            causes.append(_cause(d.code, 90, d.evidence, fixes, **d.params))

    # 2) memory: the app is at the plan's limit, or the system killed it
    oom_lines = [ln.strip()[:200] for ln in s.logs if OOM.search(ln)][-3:]
    if named and named.code == "out_of_memory":
        pass   # already ranked above with its own fix
    elif oom_lines or (mem_ratio is not None and mem_ratio >= 0.9):
        fix = _bigger_plan(s)
        causes.append(_cause("memory_exhausted", 88 if oom_lines else 78, oom_lines, [fix] if fix else [{"code": "reduce_memory", "text": "reduce what the app keeps in memory, or choose a bigger plan"}],
                             ram_gb=s.ram_gb, peak_percent=round((mem_ratio or 0) * 100)))

    # 3) CPU: saturated while the site answers badly
    if cpu_ratio is not None and cpu_ratio >= 0.85 and (failing or slow):
        fix = _bigger_plan(s)
        causes.append(_cause("cpu_saturated", 72, [], [fix] if fix else [{"code": "optimise_cpu", "text": "find the slow request or loop in the code, or choose a bigger plan"}],
                             peak_percent=round(cpu_ratio * 100)))

    # 4) a release made minutes ago and the site is failing since: the newest change is the first suspect
    if failing and recent and s.previous_release and not named:
        causes.append(_cause("bad_release", 76, error_lines(s.logs, 3), [{"tool": "rollback", "args": {"release_id": s.previous_release["id"]}, "text": "go back to the release before it",
                                                                           "code": "rollback"}], minutes=round(cur_age)))

    # 5) still starting: a new release has had little time and nothing is crashing
    if failing and cur_age is not None and cur_age <= STARTING_MINUTES and not errors and not named:
        causes.append(_cause("still_starting", 92, [], [{"code": "wait", "text": "give it a few minutes; the first start of some apps takes long"}], minutes=round(cur_age)))

    # 6) the code itself throws
    if failing and not named and any(CODE_ERROR.search(ln) for ln in s.logs):
        lines = [ln.strip()[:220] for ln in s.logs if CODE_ERROR.search(ln)][-4:]
        causes.append(_cause("code_error", 80, lines, [{"code": "fix_code", "text": "read the file the error points to, fix it, then release the edit"},
                                                      *([{"tool": "rollback", "args": {"release_id": s.previous_release["id"]}, "text": "or go back to the last release that worked", "code": "rollback"}] if s.previous_release else [])]))

    # 7) the release is not healthy at all
    if (s.current_release or {}).get("state") in ("FAILED", "UNHEALTHY"):
        causes.append(_cause("release_unhealthy", 84, [], [*([{"tool": "rollback", "args": {"release_id": s.previous_release["id"]}, "text": "go back to the last release that worked", "code": "rollback"}] if s.previous_release else [])],
                             state=(s.current_release or {}).get("state")))

    # 8) slow but not failing, and no resource is short
    if slow and not any(c["code"] in ("memory_exhausted", "cpu_saturated") for c in causes):
        causes.append(_cause("slow_response", 55, errors[:3], [{"code": "profile", "text": "look for slow database queries, calls to other services, or work done on every request; add caching"}],
                             ms=s.http_ms))

    # 9) it answers 5xx or not at all and nothing above explains it: the container is not serving
    if failing and not causes:
        d = None
        if s.cat and s.plan_id in s.cat.project_plans:
            d = diagnose(Failure("live", "READY", None, [], s.logs, f"HTTP {s.http_status}" if s.http_status else s.http_error),
                         HealContext(app=s.app, platform=s.platform, port=s.port, plan_id=s.plan_id, tier=s.tier, cat=s.cat, env_names=set(s.env_names)))
        causes.append(_cause("not_serving", 45, errors[:3] or (d.evidence if d else []), [{"code": "check_port_and_bind", "text": "check that the app listens on the port set for it, on 0.0.0.0"},
                                                                                             {"code": "read_logs", "text": "read the logs around the first error"}],
                             http=s.http_status or s.http_error))

    causes.sort(key=lambda c: -c["score"])
    state = "down" if (s.http_status is None or s.http_status >= 500) else ("degraded" if slow or causes else "healthy")
    if state == "healthy" and errors:
        causes.append(_cause("errors_in_logs", 30, errors[:4], [{"code": "read_logs", "text": "the site answers, but these errors are in the logs: check they are expected"}]))
        state = "degraded"
    return {"state": state, "signals": {
        "http": {"status": s.http_status, "ms": s.http_ms, "error": s.http_error},
        "release": s.current_release, "previous_release": s.previous_release,
        "memory": None if s.mem_max is None else {"peak_percent": round((mem_ratio or 0) * 100), "now_mb": round((s.mem_now or 0) / 1024 ** 2), "limit_gb": s.ram_gb},
        "cpu": None if s.cpu_max is None else {"peak_percent": round((cpu_ratio or 0) * 100), "now_cores": round(s.cpu_now or 0, 3), "limit_cores": s.cpu_cores},
        "log_errors": errors}, "causes": causes, "advice": _advice(s, failing, mem_ratio, cpu_ratio)}
