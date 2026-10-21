"""A ready-made (one-click) app as a deploy plan.

Liara's console installs these from a Liara Compose spec, and nothing public lets a program submit one. The spec is only data, though: an image, a port,
disks, variables and databases, the same shape the compose decomposer already turns into apps and managed databases through the documented API. So the
app is deployed the ordinary way (approval, cost shown first, journal-owned resources) from the image the spec names.
"""
from __future__ import annotations

import re
from pathlib import Path

from liara_agent.liara.oneclick import catalog, compose_spec
from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan, DiskSpec, EnvVar, Evidence, Note

MARKER = ".oneclick"  # a project made from a ready-made app has no source: this file names the app
SECRET_NAME = re.compile(r"SECRET|PASSWORD|TOKEN|PRIVATE|ENCRYPTION|SALT|API_?KEY|KEY$", re.I)
APP_REF = re.compile(r"\$APP_([A-Za-z0-9-]+)_NAME|\$APP_NAME")


class OneClickError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.params = code, params


def marker_app(path: str | Path) -> str | None:
    f = Path(path) / MARKER
    return f.read_text().strip() if f.is_file() else None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")[:40].strip("-") or "app"


def plan_from_spec(app: str, project_name: str, ram_of=lambda plan_id: 0.5) -> DeployPlan:
    """`ram_of(plan_id)` gives the memory in GB of one of Liara's plans (the spec says how big its app and databases should be)."""
    entry = catalog().get(app)
    if entry is None:
        raise OneClickError("oneclick_unknown", f"{app!r} is not a ready-made app", app=app)
    spec = compose_spec(app)
    if not spec:
        raise OneClickError("oneclick_needs_vm", f"{app!r} needs a cloud server, not the app platform", app=app)
    ev = lambda note: [Evidence(file=f"one-click spec {app}", note=note)]  # noqa: E731
    plan = DeployPlan()
    ids = {a["id"]: _slug(project_name) if len(spec["apps"]) == 1 else _slug(f"{project_name}-{a['id']}") for a in spec["apps"]}
    names = {a["id"]: a for a in spec["apps"]}

    def fill(value: str, own: str) -> str:
        value = APP_REF.sub(lambda m: ids.get(m.group(1), m.group(0)) if m.group(1) else own, str(value))
        return value  # $DB_<id>_* is kept: the database ids are renamed after planning and the references follow

    for a in spec["apps"]:
        own = ids[a["id"]]
        env = {k: fill(v, own) for k, v in (a.get("envs") or {}).items()}
        app_spec = AppSpec(name=own, platform="docker", port=a.get("port"), role="web" if a.get("port") else "worker", image=a["image"], args=[str(x) for x in a.get("args") or []],
                           env=env, disks=[DiskSpec(name=_slug(d["name"]), mount_to=d["mountTo"], size_gb=5 if "wordpress" in a["image"] and d["name"] == "data" else 1) for d in a.get("disks") or []], evidence=ev(f"app {a['id']}"),
                           min_ram_gb=max(0.5, ram_of(a.get("planID", ""))))
        plan.apps.append(app_spec)
        for k, v in env.items():
            if SECRET_NAME.search(k) and v and not v.startswith("$") and "$" not in v and len(v) >= 16:
                plan.notes.append(Note(level="warning", message=f"{own}: {k} has a fixed value in the ready-made app's spec, the same for everyone who installs it. Change it.",
                                       code="oneclick_fixed_secret", params={"app": own, "key": k}))
    for d in spec.get("databases") or []:
        plan.databases.append(DatabaseSpec(kind=d["type"], id=d["id"], version=str(d.get("version") or "") or None, evidence=ev(f"database {d['id']}"), min_ram_gb=max(0.5, ram_of(d.get("planID", "")))))
    for a in plan.apps:
        for k, v in list(a.env.items()):
            if "$RANDOM_STRING" in v:
                plan.env.append(EnvVar(name=k, secret=True, source="generated"))
    plan.notes.append(Note(level="info", message=f"{app} is installed from its ready-made image through Liara's API, with its recommended sizes", code="oneclick_plan", params={"app": app}))
    _ = names
    return plan
