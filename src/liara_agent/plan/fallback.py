"""A plan made by fixed rules, for when the AI model cannot be used (its address blocked, a key missing, a quota gone).

It only handles the ordinary case, and says so in the plan: ONE app, in one folder, on a platform Liara knows, plus the databases its dependencies name. It refuses anything
else (several apps in several folders, a monorepo, nothing recognisable) by returning None, and the run then reports that the model is needed. Every decision keeps the
file it came from, like a plan from the model, and the plan carries a note so the person checks it before approving. It never guesses a secret: environment variables the
code reads are asked from the person, the same way as in a plan from the model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from liara_agent.detect.fingerprints import SECRETISH, Fingerprint
from liara_agent.liara import catalog
from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan, EnvVar, Evidence, Note

PLATFORM_ORDER = ["docker", "next", "django", "laravel", "flask", "php", "go", "dotnet", "node", "python", "react", "vue", "angular", "static"]
DB_ENV_NAMES = {"DATABASE_URL", "POSTGRES_URL", "POSTGRES_URI", "MONGODB_URI", "MONGO_URI", "MONGO_URL", "MONGODB_URL", "MYSQL_URL", "MARIADB_URL", "DB_URL", "REDIS_URL", "REDIS_URI", "CACHE_URL"}
SKIP_ENV = {"PORT", "NODE_ENV", "PATH", "HOME", "PWD", "USER", "DEBUG", "LOG_LEVEL", "LOGLEVEL", "TZ", "LANG", "CI", "PWD", "TMPDIR", "NODE_OPTIONS", "NPM_CONFIG_PRODUCTION"}   # the platform's or the shell's, or optional tuning
# where the app listens: on Liara that is always every address, and the person is never asked
BIND_ENV = {"IP", "HOST", "HOSTNAME", "BIND", "BIND_IP", "BIND_ADDR", "BIND_ADDRESS", "LISTEN_ADDR", "LISTEN_ADDRESS", "LISTEN_HOST", "SERVER_HOST", "APP_HOST"}
# a name that looks like a key the app makes for itself (not a third party's): safe to invent
SELF_SECRETS = re.compile(r"^(SECRET_KEY|JWT_SECRET|SESSION_SECRET|APP_SECRET|COOKIE_SECRET|NEXTAUTH_SECRET|AUTH_SECRET|APP_KEY)$")


# connection settings the code reads one by one (MONGO_HOST, DB_PASSWORD, REDIS_PORT...): they point at the database the plan creates, so they are never asked
DB_PREFIXES = {"mongodb": ("MONGO", "MONGODB", "DB"), "postgres": ("POSTGRES", "POSTGRESQL", "PG", "DB"), "mysql": ("MYSQL", "DB"), "mariadb": ("MARIADB", "MYSQL", "DB"),
               "redis": ("REDIS", "CACHE")}
DB_PARTS = {"HOST": "HOST", "HOSTNAME": "HOST", "ADDRESS": "HOST", "PORT": "PORT", "USER": "USER", "USERNAME": "USER", "PASSWORD": "PASSWORD", "PASS": "PASSWORD", "PWD": "PASSWORD",
            "NAME": "NAME", "DATABASE": "NAME", "DB": "NAME"}


def _db_part(name: str, db: DatabaseSpec) -> str | None:
    for prefix in DB_PREFIXES.get(db.kind, ()):
        if name.startswith(prefix + "_") and (part := DB_PARTS.get(name[len(prefix) + 1:])):
            return part
    return None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")[:40].strip("-") or "app"


def _exposed_port(dockerfile: Path) -> int | None:
    m = re.search(r"^\s*EXPOSE\s+(\d{2,5})", dockerfile.read_text(errors="ignore"), re.M | re.I)
    return int(m.group(1)) if m else None


def _has_start_script(pkg_dir: Path) -> bool:
    try:
        return bool((json.loads((pkg_dir / "package.json").read_text()).get("scripts") or {}).get("start"))
    except (OSError, ValueError):
        return False


def plan_without_model(root: str | Path, project: str, fp: Fingerprint) -> DeployPlan | None:
    root = Path(root)
    code_dirs = sorted({h.path for h in fp.hints if h.kind == "platform"} | {d for d, names in fp.manifests.items() if "Dockerfile" in names})
    if len(code_dirs) != 1:   # nothing recognised, or several folders (a monorepo): the model is needed to decide how they fit together
        return None
    d = code_dirs[0]
    hints = [h for h in fp.hints if h.path == d]
    platforms = {h.value: h for h in hints if h.kind == "platform"}
    platform = next((p for p in PLATFORM_ORDER if p in platforms), None)
    if platform is None or platform not in catalog.platforms():
        return None
    if platform in ("react", "vue", "angular") and "node" in platforms:
        platform = "node"          # a server and a front end in one folder: the server runs it
    if platform in ("react", "vue", "angular", "static") and any(h.kind == "db" for h in hints):
        platform = "node" if (root / d / "package.json").is_file() and _has_start_script(root / d) else platform
    ev = [platforms[platform].evidence] if platform in platforms else [Evidence(file=f"{d}/package.json".removeprefix("./"))]
    port = catalog.default_port(platform)
    if platform == "docker" and (root / d / "Dockerfile").is_file():
        port = _exposed_port(root / d / "Dockerfile") or port
    app = AppSpec(name=_slug(project), path=d, platform=platform, port=port, evidence=ev)
    plan = DeployPlan(apps=[app])
    seen: set[str] = set()
    for h in hints:
        if h.kind == "db" and h.value in catalog.databases() and h.value not in seen:
            seen.add(h.value)
            plan.databases.append(DatabaseSpec(kind=h.value, id=h.value, evidence=[h.evidence]))
    for name, evidence in sorted(fp.env_vars.items()):
        if name in BIND_ENV:
            app.env[name] = "0.0.0.0"
            continue
        if name in DB_ENV_NAMES or name in SKIP_ENV:
            continue
        wired = next(((db, part) for db in plan.databases if (part := _db_part(name, db))), None)
        if wired and (len(plan.databases) == 1 or not name.startswith("DB_")):
            app.env[name] = f"$DB_{wired[0].id}_{wired[1]}"
            continue
        secret = bool(SECRETISH.search(name))
        plan.env.append(EnvVar(name=name, secret=secret, source="generated" if SELF_SECRETS.match(name) else "user", evidence=[evidence]))
    plan.notes.append(Note(level="warning", message="the AI model could not be used, so this plan comes from fixed rules: check it before you approve", code="planned_without_model"))
    return plan
