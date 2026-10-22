"""Classify a failed deploy from its evidence (release state, build output, runtime logs, HTTP check) and propose fixes.

Deterministic on purpose: cheap, testable, and it never needs to send logs to an LLM. Unknown failures are reported honestly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from liara_agent.liara.live import TIER_LABELS, LiveCatalog
from liara_agent.liara.pricing import project_cost

VAT = 1.10
DB_PORTS = {5432: "postgres", 3306: "mysql", 27017: "mongodb", 6379: "redis", 9200: "elasticsearch", 5672: "rabbitmq", 1433: "mssql"}
FOREIGN_HOSTS = r"api\.telegram\.org|api\.openai\.com|generativelanguage\.googleapis\.com|api\.anthropic\.com|discord(?:app)?\.com|hooks\.slack\.com"
NET_ERRORS = r"ConnectTimeout|ConnectError|RemoteProtocolError|ECONNRESET|ETIMEDOUT|ENOTFOUND|network error|getaddrinfo|Connection (?:reset|timed out)|TimeoutError"


@dataclass
class Failure:
    step: str
    state: str | None = None  # release state: FAILED | UNHEALTHY | READY (but not serving)
    reason: str | None = None  # failReason from the release
    build_lines: list[str] = field(default_factory=list)
    runtime_lines: list[str] = field(default_factory=list)
    http_detail: str | None = None

    def text(self) -> str:
        return "\n".join([self.reason or "", *self.build_lines[-80:], *self.runtime_lines[-120:], self.http_detail or ""])


@dataclass
class Fix:
    kind: str  # resize | set_port | set_env | code_patch | refresh_db_credentials | manual
    description: str  # English fallback; the UI localizes by `code` + `params`
    params: dict = field(default_factory=dict)
    cost_delta_hourly: float = 0.0  # toman/hour incl. VAT; positive = more expensive
    code: str = ""
    payload: object = None  # data the executor needs that does not belong in an event (a repair's file contents)

    def as_event(self) -> dict:
        return {"code": self.code, "kind": self.kind, "text": self.description, "params": self.params, "cost_delta_hourly": round(self.cost_delta_hourly, 2)}


# Who has to change what. "code": the developer's source is wrong (Sakoo can patch its copy, or the developer's own agent can fix the repo);
# "config": the service settings on Liara are wrong (fixed through the API); "resources": the plan is too small (costs money);
# "outside": nothing a redeploy or a patch can fix.
CODE_CAUSES = {"missing_python_module", "missing_node_module", "node_engine_mismatch", "db_localhost", "bound_to_localhost", "code_error"}
RESOURCE_CAUSES = {"build_timeout", "out_of_memory"}
OUTSIDE_CAUSES = {"outbound_blocked", "no_dockerfile"}


@dataclass
class Diagnosis:
    code: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    fixes: list[Fix] = field(default_factory=list)
    retryable: bool = True
    params: dict = field(default_factory=dict)  # values the summary mentions, for localized wording

    @property
    def category(self) -> str:
        if self.code in CODE_CAUSES:
            return "code"
        if self.code in RESOURCE_CAUSES:
            return "resources"
        if self.code in OUTSIDE_CAUSES:
            return "outside"
        return "config" if self.code in {"missing_env", "db_auth_failed", "wrong_port", "not_serving"} else "unknown"


@dataclass
class HealContext:
    app: str
    platform: str
    port: int | None
    plan_id: str
    tier: str
    cat: LiveCatalog
    env_names: set[str] = field(default_factory=set)
    db_kinds: set[str] = field(default_factory=set)
    db_kinds_by_id: dict[str, str] = field(default_factory=dict)  # plan database id -> kind, for the repair agent's templates
    project_files: set[str] = field(default_factory=set)  # relative paths available for patching, e.g. {"requirements.txt"}
    db_api_ids: dict[str, str] = field(default_factory=dict)  # plan database id -> Liara database id
    env_templates: dict[str, str] = field(default_factory=dict)  # env with $DB_ references, before substitution
    recreate: Callable[[str, str], None] | None = None  # (plan, tier): delete the app this run created and create it again on another plan/tier


def _evidence(text: str, rx: str, n: int = 3) -> list[str]:
    return [ln.strip()[:200] for ln in text.splitlines() if re.search(rx, ln)][:n]


def _upgrade_tier(ctx: HealContext, need_minutes: float) -> Fix | None:
    bundles = ctx.cat.project_bundles.get(ctx.plan_id, {})
    cur = bundles.get(ctx.tier)
    best = None
    for tier, b in bundles.items():
        if b.available and cur and b.build_timeout > cur.build_timeout and b.build_timeout >= need_minutes:
            c = project_cost(ctx.cat, ctx.plan_id, tier).hourly
            if best is None or c < best[0]:
                best = (c, tier, b.build_timeout)
    if not best:
        return None
    delta = (best[0] - project_cost(ctx.cat, ctx.plan_id, ctx.tier).hourly) * VAT
    names = TIER_LABELS
    return Fix("resize", f"move {ctx.app} from the {names.get(ctx.tier, ctx.tier)} tier to {names.get(best[1], best[1])} (builds may run {best[2]:g} minutes instead of {cur.build_timeout:g})",
               {"plan": ctx.plan_id, "tier": best[1], "tier_label": TIER_LABELS.get(best[1], best[1]), "from_tier": ctx.tier,
                "from_tier_label": TIER_LABELS.get(ctx.tier, ctx.tier), "app": ctx.app, "build_minutes": best[2], "from_build_minutes": cur.build_timeout},
               delta, code="resize_tier")


def _bigger_plan(ctx: HealContext) -> Fix | None:
    cur = ctx.cat.project_plans[ctx.plan_id]
    best = None
    for pid, p in ctx.cat.project_plans.items():
        if p.available and pid.endswith("-g2") and p.ram.amount > cur.ram.amount and ctx.tier in ctx.cat.project_bundles.get(pid, {}):
            if best is None or p.ram.amount < ctx.cat.project_plans[best].ram.amount:
                best = pid
    if not best:
        return None
    delta = (project_cost(ctx.cat, best, ctx.tier).hourly - project_cost(ctx.cat, ctx.plan_id, ctx.tier).hourly) * VAT
    return Fix("resize", f"move {ctx.app} to the {best} plan ({ctx.cat.project_plans[best].ram.amount:g} GB RAM instead of {cur.ram.amount:g})",
               {"plan": best, "tier": ctx.tier, "from_plan": ctx.plan_id, "app": ctx.app, "ram_gb": ctx.cat.project_plans[best].ram.amount,
                "from_ram_gb": cur.ram.amount}, delta, code="resize_plan")


NODE_VERSIONS = [14, 16, 18, 20, 22, 24]  # what Liara's node platform offers (the CLI's catalog); an older pin is moved to the nearest one


def node_for_engine(expected: str) -> tuple[str, str]:
    """(Liara node version to build with, relaxed engines.node) for a package.json engines.node that the default runtime does not satisfy."""
    m = re.search(r"(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?", expected)
    major = int(m.group(1)) if m else NODE_VERSIONS[0]
    version = major if major in NODE_VERSIONS else min((v for v in NODE_VERSIONS if v > major), default=NODE_VERSIONS[-1])
    floor = ".".join(x if x and x.isdigit() else "0" for x in (m.group(1), m.group(2), m.group(3))) if m else str(version)
    return str(version), f">={floor}"


CODE_ERROR = re.compile(r"(?m)^.*(?:SyntaxError|IndentationError|NameError|TypeError|ReferenceError|AttributeError|ImportError|ModuleNotFoundError|ValueError|KeyError|"
                        r"Cannot find module '\.{1,2}/|npm ERR! (?:missing script|Missing script)|error TS\d+|UnhandledPromiseRejection|Uncaught (?:Exception|Error)).*$")
LOCATION = re.compile(r"""(?:File "([^"]+)", line (\d+))|(?:\(?(/?(?:[\w.\-]+/)*[\w.\-]+\.(?:js|jsx|ts|tsx|mjs|cjs|py)):(\d+)(?::\d+)?\)?)""")


def _code_error_lines(t: str, n: int = 4) -> list[str]:
    return [m.group(0).strip()[:300] for m in CODE_ERROR.finditer(t)][:n]


CONTAINER_ROOT = re.compile(r"^/(?:usr/src/app|home/\w+/app|opt/app|workspace|app|code|srv|src)/")   # the folder a Dockerfile copies the project to


def _source_location(t: str) -> str:
    """file:line of the last frame in the project (not node_modules or the standard library) that appears in the log, or ''."""
    best = ""
    for m in LOCATION.finditer(t):
        path, line = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if "node_modules" in path or "site-packages" in path or path.startswith(("node:", "/usr/lib", "internal/")):
            continue
        best = f"{(CONTAINER_ROOT.sub('', path) if path.startswith('/') else path).lstrip('./')}:{line}"
    return best


def diagnose(f: Failure, ctx: HealContext) -> Diagnosis:
    t = f.text()

    if ctx.platform == "node" and (m := re.search(r"""(?i)The engine "node" is incompatible with this module\. Expected version "([^"]+)"|EBADENGINE[^\n]*|Unsupported engine[^\n]*required:?\s*\{?\s*['"]?node['"]?:\s*['"]([^'"]+)""", t)):
        expected = m.group(1) or m.group(2) or ""
        if expected and "package.json" in ctx.project_files:
            version, relaxed = node_for_engine(expected)
            return Diagnosis("node_engine_mismatch", f"package.json pins Node {expected}, which the build runtime does not satisfy, so the install stopped before building.",
                             _evidence(t, r"(?i)engine|incompatible"),
                             [Fix("code_patch", f"build with Node {version} and relax engines.node to {relaxed} in package.json (in a copy of your project)",
                                  {"file": "package.json", "engines_node": relaxed, "platform_version": version, "expected": expected}, code="patch_node_engine")],
                             params={"expected": expected, "version": version, "engines_node": relaxed})

    if re.search(r"(?i)build took more than|build (?:has )?timed? ?out|exceeded the build time|took longer than \d+ minutes|release ended as \w*TIME_?D?_?OUT", t):
        fix = _upgrade_tier(ctx, 0)
        fixes = [fix] if fix else []
        fixes.append(Fix("manual", "or shorten the build: add a .dockerignore/.gitignore, avoid reinstalling dependencies, use a lighter base image", code="shorten_build"))
        limit = ctx.cat.project_bundles.get(ctx.plan_id, {}).get(ctx.tier)
        return Diagnosis("build_timeout", "The build ran past this tier's time limit. This is a plan limit, not a bug in your code.",
                         _evidence(t, r"(?i)took|time"), fixes, params={"tier_label": TIER_LABELS.get(ctx.tier, ctx.tier), "limit_minutes": limit.build_timeout if limit else None})

    if re.search(r"(?i)out of memory|OOMKilled|exit code 137|heap out of memory|MemoryError|\bKilled\b", t):
        fix = _bigger_plan(ctx)
        return Diagnosis("out_of_memory", "The process ran out of memory.", _evidence(t, r"(?i)memory|killed|137|oom"),
                         [fix] if fix else [Fix("manual", "reduce memory use or choose a larger plan in the console", code="reduce_memory")],
                         params={"ram_gb": ctx.cat.project_plans[ctx.plan_id].ram.amount})

    if m := re.search(r"ModuleNotFoundError: No module named '([\w.\-]+)'", t):
        pkg = m.group(1).split(".")[0]
        fixes = ([Fix("code_patch", f"add {pkg} to requirements.txt (check the PyPI name; some modules differ, e.g. yaml is PyYAML)",
                      {"file": "requirements.txt", "append": pkg}, code="patch_add_dependency")]
                 if "requirements.txt" in ctx.project_files else [Fix("manual", f"declare {pkg} in your dependency file", {"module": pkg}, code="declare_dependency")])
        return Diagnosis("missing_python_module", f"The code imports {pkg}, which is not declared as a dependency.", _evidence(t, r"ModuleNotFoundError"), fixes, params={"module": pkg})

    if m := re.search(r"Cannot find module '((?:@[\w.\-]+/)?[\w][\w.\-]*)'", t):
        pkg = m.group(1)
        fixes = ([Fix("code_patch", f"add {pkg} to package.json (the current version from the npm registry)", {"file": "package.json", "add_dependency": pkg}, code="patch_add_npm_dependency")]
                 if "package.json" in ctx.project_files else [Fix("manual", f"run `npm install {pkg}` locally and redeploy", {"module": pkg}, code="npm_install")])
        return Diagnosis("missing_node_module", f"The code requires {pkg}, which is not in package.json.", _evidence(t, r"Cannot find module"), fixes, params={"module": pkg})

    names = set()
    for rx in (r"KeyError: '([A-Z][A-Z0-9_]+)'", r"(?i:missing|required|set the) (?:required )?(?i:env(?:ironment)?(?: variable)?)?:? ?['\"`]?([A-Z][A-Z0-9_]{2,})['\"`]?\b(?!\w)",
               r"\b([A-Z][A-Z0-9_]{3,}) (?:is not defined|is required|must be set|is missing|is not set)", r"Set the ([A-Z][A-Z0-9_]+) environment variable"):
        names |= set(re.findall(rx, t))
    names = {n for n in names if n not in ctx.env_names and n not in {"ERROR", "NODE_ENV", "NOTE", "WARNING", "FATAL"}}
    if names:
        return Diagnosis("missing_env", f"The app needs environment variable(s) that are not set: {', '.join(sorted(names))}.",
                         _evidence(t, "|".join(map(re.escape, names))), [Fix("set_env", f"set {', '.join(sorted(names))}", {"names": sorted(names)}, code="set_env")], params={"names": sorted(names)})

    if re.search(NET_ERRORS, t) and (hosts := set(re.findall(FOREIGN_HOSTS, t))):
        return Diagnosis("outbound_blocked", f"The app cannot reach {', '.join(sorted(hosts))} from Liara's network; this is a network restriction, not a code bug.",
                         _evidence(t, FOREIGN_HOSTS + "|" + NET_ERRORS),
                         [Fix("manual", "options: set a custom DNS server in the app settings, route through a proxy you control, or use an alternative (e.g. Liara AI for OpenAI-style APIs)", {"hosts": sorted(hosts)}, code="outbound_options")],
                         retryable=False, params={"hosts": sorted(hosts)})

    if ctx.db_api_ids and re.search(r"(?i)password authentication failed|Access denied for user|ER_ACCESS_DENIED|MongoServerError: Authentication failed|WRONGPASS|invalid username-password pair|authentication failed for user", t):
        return Diagnosis("db_auth_failed", "The app reached its database but the login was rejected, so it is using credentials that are not the database's current ones.",
                         _evidence(t, r"(?i)authentication failed|access denied|wrongpass|invalid username"),
                         [Fix("refresh_db_credentials", "re-read the database's credentials from Liara and update the app's environment (free)", {}, code="refresh_db_credentials")])

    if m := re.search(r"(?i)\b(mongodb(?:\+srv)?|postgres(?:ql)?|mysql|mariadb|redis)://(?:[^/\s@]*@)?(?:127\.0\.0\.1|localhost)\b", t):  # an app that printed the localhost URL it failed to reach
        kind = {"mongodb+srv": "mongodb", "postgresql": "postgres", "mariadb": "mysql"}.get(m.group(1).lower(), m.group(1).lower())
        have = kind in ctx.db_kinds or (kind == "mysql" and "mariadb" in ctx.db_kinds)
        return Diagnosis("db_localhost", f"The app connects to a {kind} server on localhost, which does not exist on Liara.", _evidence(t, r"(?i)localhost|127\.0\.0\.1"),
                         [Fix("manual", f"point the app's {kind} host setting at the Liara database hostname via an environment variable" if have
                              else f"create a Liara {kind} database and pass its host and credentials as environment variables", {"kind": kind},
                              code="db_point_host" if have else "db_create")], params={"kind": kind})

    if m := re.search(r"(?i)(?:ECONNREFUSED|connection refused|could not connect|can't connect)[^\n]{0,80}?(?:127\.0\.0\.1|localhost|::1)[:'\" ]+(\d{3,5})", t) \
            or re.search(r"(?i)(?:127\.0\.0\.1|localhost)[:'\" ]+(\d{3,5})[^\n]{0,60}(?:refused|ECONNREFUSED)", t):
        kind = DB_PORTS.get(int(m.group(1)))
        if kind:
            have = kind in ctx.db_kinds or (kind == "mysql" and "mariadb" in ctx.db_kinds)
            return Diagnosis("db_localhost", f"The app connects to a {kind} server on localhost, which does not exist on Liara.", _evidence(t, r"(?i)refused|localhost|127\.0\.0\.1"),
                             [Fix("manual", f"point the app's {kind} host setting at the Liara database hostname via an environment variable" if have
                                  else f"create a Liara {kind} database and pass its host and credentials as environment variables", {"kind": kind},
                                  code="db_point_host" if have else "db_create")], params={"kind": kind})

    ports = [int(p) for p in re.findall(r"(?i)(?:listening|running|started|serving)[^\n]{0,60}?(?:port|:)\s*(\d{2,5})", f.runtime_lines and "\n".join(f.runtime_lines) or "")]
    if ports and ctx.port and ports[-1] != ctx.port:
        return Diagnosis("wrong_port", f"The app listens on port {ports[-1]} but Liara routes to {ctx.port}.", _evidence(t, r"(?i)listening|running|started|serving"),
                         [Fix("set_port", f"set the app port to {ports[-1]}", {"port": ports[-1], "from_port": ctx.port}, code="set_port")],
                         params={"actual": ports[-1], "expected": ctx.port})

    if re.search(r"(?i)(?:running|listening|serving)[^\n]{0,40}(?:127\.0\.0\.1|localhost)", "\n".join(f.runtime_lines)) and f.http_detail and re.search(r"50[23]", f.http_detail):
        return Diagnosis("bound_to_localhost", "The app listens on 127.0.0.1, so Liara's router cannot reach it.", _evidence(t, r"127\.0\.0\.1|localhost"),
                         [Fix("manual", "bind to 0.0.0.0 (e.g. `--host 0.0.0.0`, or `app.listen(port, '0.0.0.0')`)", code="bind_all_interfaces")])

    if re.search(r"(?i)dockerfile[^\n]{0,40}(?:not found|no such file)|failed to read dockerfile|unable to prepare context", t):
        return Diagnosis("no_dockerfile", "A Docker build was requested but no Dockerfile was found in the uploaded source.", _evidence(t, r"(?i)dockerfile"),
                         [Fix("manual", "add a Dockerfile, or deploy with a framework platform instead of docker", code="add_dockerfile")], retryable=False)

    if f.http_detail and re.search(r"50[23]", f.http_detail) and f.state == "READY":
        return Diagnosis("not_serving", f"The release is READY but the app answers {f.http_detail}; the logs do not say why.", _evidence(t, r"(?i)error|exception") or [],
                         [Fix("manual", "check the port setting and that the app binds to 0.0.0.0", code="check_port_and_bind")], params={"http": f.http_detail})

    if errs := _code_error_lines(t):
        where = _source_location(t)
        return Diagnosis("code_error", "The app stops because of an error in its own code" + (f" ({where})." if where else "."), errs,
                         [Fix("manual", "fix the error in the code", {"location": where} if where else {}, code="fix_code_error")], params={"location": where or ""})

    return Diagnosis("unknown", f"I could not classify this failure ({f.reason or f.state or f.http_detail}).", _evidence(t, r"(?i)error|failed|exception"),
                     [Fix("manual", "read the log lines above, or fix the build locally first", code="read_logs")], retryable=False,
                     params={"state": f.state, "reason": f.reason})
