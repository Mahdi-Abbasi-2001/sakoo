"""Turn a docker-compose project into a Liara DeployPlan.

Liara cannot take a compose file: each service becomes either a managed database (known DB images) or a
separate app, all on one private network, wired together with environment variables.
"""
from __future__ import annotations

import re

from liara_agent.detect.intent import ComposeProject, ComposeService
from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan, DiskSpec, Evidence, Note, Question

# image repository (last path segment, without registry) -> Liara database type
DB_IMAGES = {
    "postgres": "postgres", "postgresql": "postgres", "pgvector": "postgres", "postgis": "postgres",
    "mysql": "mysql", "mariadb": "mariadb", "mongo": "mongodb", "mongodb": "mongodb",
    "redis": "redis", "valkey": "redis", "redis-stack": "redis", "elasticsearch": "elasticsearch",
    "rabbitmq": "rabbitmq", "clickhouse-server": "clickhouse", "clickhouse": "clickhouse", "server": None,
}
# services Liara already provides (routing, TLS, caching): dropped with a note
PROXY_IMAGES = {"nginx", "traefik", "caddy", "haproxy", "envoy", "nginx-proxy", "certbot"}
DEFAULT_PORTS = {"postgres": 5432, "mysql": 3306, "mariadb": 3306, "mongodb": 27017, "redis": 6379,
                 "elasticsearch": 9200, "rabbitmq": 5672, "clickhouse": 8123, "mssql": 1433}
CRED_KEYS = re.compile(r"(PASSWORD|PASSWD|USER|USERNAME|DB|DATABASE|_URL|_URI)$", re.I)


def _repo(image: str) -> tuple[str, str | None]:
    ref = image.split("@")[0]
    name, _, tag = ref.rpartition(":") if re.search(r":[^/]+$", ref) else (ref, "", None)
    return name.split("/")[-1].lower(), (tag or None)


def db_kind(image: str | None) -> str | None:
    if not image:
        return None
    if "mssql" in image.lower():
        return "mssql"
    return DB_IMAGES.get(_repo(image)[0])


def pick_version(kind: str, wanted: str | None, available: dict[str, list[str]], defaults: dict[str, str]) -> tuple[str | None, str | None]:
    """(version, note). Same major (or major.minor) if Liara offers it, otherwise Liara's default."""
    options = available.get(kind) or []
    if not options:
        return None, None
    if wanted and (m := re.match(r"(\d+)(?:\.(\d+))?", wanted)):
        major, minor = m.group(1), m.group(2)
        same = [v for v in options if v.split(".")[0] == major]
        exact = [v for v in same if minor is not None and v.startswith(f"{major}.{minor}")]
        if exact or same:
            return (exact or same)[0], None
        return defaults.get(kind, options[0]), f"{kind} {wanted} is not offered by Liara; using {defaults.get(kind, options[0])}"
    return defaults.get(kind, options[0]), None


CRED_ENV = {  # env vars of database images that define the credentials the compose file's apps were written against
    "user": ("POSTGRES_USER", "MYSQL_USER", "MARIADB_USER", "MONGO_INITDB_ROOT_USERNAME"),
    "password": ("POSTGRES_PASSWORD", "MYSQL_PASSWORD", "MARIADB_PASSWORD", "MYSQL_ROOT_PASSWORD", "MARIADB_ROOT_PASSWORD", "MONGO_INITDB_ROOT_PASSWORD"),
    "name": ("POSTGRES_DB", "MYSQL_DATABASE", "MARIADB_DATABASE", "MONGO_INITDB_DATABASE"),
}
_URL = re.compile(r"^(?P<scheme>[\w+.-]+)://(?P<user>[^:@/\s]*):(?P<pw>[^@/\s]*)@(?P<host>[^:/\s]+)(?::(?P<port>\d+))?(?:/(?P<db>[^?\s]*))?(?P<rest>.*)$")
_HINT = re.compile(r"(USER|PASS|PWD|NAME|DB|DATABASE)", re.I)


def _db_creds(svc: ComposeService) -> dict[str, str]:
    out = {}
    for field, keys in CRED_ENV.items():
        for k in keys:
            if svc.env.get(k):
                out[field] = svc.env[k]
                break
    if "name" not in out and out.get("user"):
        out["name"] = out["user"]  # postgres defaults the database name to the user name
    return out


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


def decompose(project: ComposeProject, *, project_name: str,
              db_versions: dict[str, list[str]] | None = None, db_defaults: dict[str, str] | None = None) -> DeployPlan:
    db_versions, db_defaults = db_versions or {}, db_defaults or {}
    plan = DeployPlan()
    ev = lambda svc: [Evidence(file=project.file, note=f"service {svc}")]  # noqa: E731
    db_ids: dict[str, str] = {}  # compose service name -> Liara database id
    app_ids: dict[str, str] = {}

    for name, svc in project.services.items():
        kind = db_kind(svc.image) if not svc.build_context else None
        if kind:
            db_ids[name] = _slug(f"{project_name}-{name}")
    creds = {db_ids[n]: _db_creds(project.services[n]) for n in db_ids}
    for name, svc in project.services.items():
        if name not in db_ids and not (svc.image and _repo(svc.image)[0] in PROXY_IMAGES and not svc.build_context):
            app_ids[name] = _slug(f"{project_name}-{name}")

    for name, svc in project.services.items():
        for flag, msg in (("privileged", "uses privileged mode"), ("host_network", "uses host networking")):
            if flag in svc.flags:
                plan.notes.append(Note(level="blocker", message=f"{name} {msg}, which Liara apps do not support", code=f"service_{flag}", params={"service": name}, evidence=ev(name)))
        if "profiles" in svc.flags:
            plan.notes.append(Note(level="info", message=f"{name} uses compose profiles; it was included as if enabled", code="compose_profile", params={"service": name}, evidence=ev(name)))

        if name in db_ids:
            kind = db_kind(svc.image)
            tag = _repo(svc.image)[1]
            version, vnote = pick_version(kind, tag, db_versions, db_defaults)
            plan.databases.append(DatabaseSpec(kind=kind, id=db_ids[name], version=version, evidence=ev(name)))
            if vnote:
                plan.notes.append(Note(level="warning", message=vnote, code="db_version_substituted",
                                       params={"kind": kind, "wanted": tag, "used": version}, evidence=ev(name)))
            if any("docker-entrypoint-initdb.d" in target for _, target in svc.volumes):
                plan.notes.append(Note(level="warning", message=f"{name} runs init scripts on first start; Liara databases start empty, so import them manually or via a migration",
                                       code="db_init_scripts", params={"service": name}, evidence=ev(name)))
            if kind == "clickhouse":
                plan.notes.append(Note(level="warning", message="Liara marks ClickHouse as experimental", code="db_experimental", params={"kind": kind}, evidence=ev(name)))
            continue

        if name not in app_ids:
            plan.notes.append(Note(level="info", message=f"{name} ({svc.image}) is a reverse proxy; Liara provides routing and TLS, so it is dropped",
                                   code="proxy_dropped", params={"service": name, "image": svc.image}, evidence=ev(name)))
            continue

        port = svc.ports[0] if svc.ports else None
        app = AppSpec(
            name=app_ids[name], platform="docker", port=port, role="web" if port else "worker",
            path=svc.build_context or ".", image=None if svc.build_context else svc.image,
            args=svc.command, env=_translate_env(name, svc, project, db_ids, app_ids, plan, creds), evidence=ev(name),
        )
        for source, target in svc.volumes:
            if source.startswith(("/", ".", "~")) and not target.startswith("/docker-entrypoint"):
                plan.notes.append(Note(level="warning", message=f"{name}: host path {source} cannot be mounted; if it holds data, use a Liara disk at {target}",
                                       code="host_path_not_mountable", params={"service": name, "source": source, "target": target}, evidence=ev(name)))
            if source and not source.startswith(("/", ".", "~")):
                app.disks.append(DiskSpec(name=_slug(source), mount_to=target))
        if len(svc.ports) > 1:
            plan.questions.append(Question(text=f"{name} exposes several ports {svc.ports}; Liara routes one. Which should be public? (using {svc.ports[0]})",
                                           code="multiple_ports", params={"service": name, "ports": svc.ports, "chosen": svc.ports[0]}))
        if not port:
            plan.questions.append(Question(text=f"{name} has no port; is it a background worker? (Liara apps still need a port setting, usually any value)",
                                           code="worker_without_port", params={"service": name}, options=["worker", "needs_port"]))
        plan.apps.append(app)
    return plan


def _translate_env(name: str, svc: ComposeService, project: ComposeProject, db_ids: dict[str, str],
                   app_ids: dict[str, str], plan: DeployPlan, creds: dict[str, dict[str, str]]) -> dict[str, str]:
    """Rewrite service hostnames to Liara private-network hostnames and wire credentials to the database's generated ones
    ($DB_<id>_USER etc., filled in after the database exists)."""
    out: dict[str, str] = {}
    ev = [Evidence(file=project.file, note=f"service {name}")]
    for key, value in svc.env.items():
        new = value
        for other, lid in {**db_ids, **app_ids}.items():
            # service name used as a host: db:5432, //db/, "db" alone, or redis://cache:6379
            new = re.sub(rf"(?<![\w-]){re.escape(other)}(?![\w-])", lid, new) if other != name else new
        if key.upper().endswith(("HOST", "HOSTNAME")) and value in db_ids:
            new = db_ids[value]
        wired = False
        if (m := _URL.match(new)) and m.group("host") in creds:  # scheme://user:pass@db:port/name -> all parts from the database
            lid = m.group("host")
            new = f"{m.group('scheme')}://$DB_{lid}_USER:$DB_{lid}_PASSWORD@$DB_{lid}_HOST:$DB_{lid}_PORT" + \
                  (f"/$DB_{lid}_NAME" if m.group("db") else "") + (m.group("rest") or "")
            wired = True
        elif _HINT.search(key):
            for lid, c in creds.items():
                for field, ref in (("password", "PASSWORD"), ("user", "USER"), ("name", "NAME")):
                    if c.get(field) and value == c[field]:
                        new, wired = f"$DB_{lid}_{ref}", True
                        break
                if wired:
                    break
        if wired:
            plan.notes.append(Note(level="info", message=f"{name}.{key} now uses the generated credentials of its database (the compose file's hardcoded ones are not used on Liara)",
                               code="credentials_wired", params={"service": name, "key": key}, evidence=ev))
        out[key] = new
        if not wired and re.search(r"://[^/@\s]*:[^@\s]*@", new) and any(lid in new for lid in db_ids.values()):
            plan.notes.append(Note(level="warning", message=f"{name}.{key} embeds database credentials that could not be mapped automatically; replace them with $DB_<database>_USER and $DB_<database>_PASSWORD",
                               code="credentials_unmapped", params={"service": name, "key": key}, evidence=ev))
    return out
