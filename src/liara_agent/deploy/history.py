"""Deploy history and rollback.

Every source archive the agent uploads is kept (the last few per project, under .agent-state/releases/) together with what it was deployed with. A rollback
uploads that archive again and creates a new release from it: exactly the path of a normal deploy, so nothing new has to be trusted. A release this agent
did not make has no archive; for those Liara's own image of the release is redeployed instead, which is marked as the less certain route.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Callable

from liara_agent.liara.client import LiaraClient

KEEP = 5  # source archives kept per project (each is at most the plan's source limit)


class HistoryError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **params):
        super().__init__(message)
        self.code, self.status, self.params = code, status, params


def _dir(state_dir: Path) -> Path:
    return state_dir / "releases"


def save_source(state_dir: Path, archive: Path) -> Path:
    d = _dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"pending-{uuid.uuid4().hex[:8]}.tar.gz"
    shutil.copy2(archive, dest)
    return dest


def commit_source(saved: Path, state_dir: Path, release_id: str, meta: dict) -> None:
    """Name the kept archive after the release it was deployed as, and prune the old ones."""
    d = _dir(state_dir)
    saved.replace(d / f"{release_id}.tar.gz")
    (d / f"{release_id}.json").write_text(json.dumps({**meta, "saved_at": time.time()}))
    for old in sorted(d.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)[KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix("").with_suffix(".json").unlink(missing_ok=True)


def discard(saved: Path | None) -> None:
    if saved:
        saved.unlink(missing_ok=True)


def stored(state_dir: Path, release_id: str) -> tuple[Path, dict] | None:
    d = _dir(state_dir)
    archive, meta = d / f"{release_id}.tar.gz", d / f"{release_id}.json"
    if release_id and archive.is_file() and meta.is_file():
        return archive, json.loads(meta.read_text())
    return None


def _release_row(r: dict, current: str, kept: set[str]) -> dict:
    rid = str(r.get("_id") or r.get("id") or "")
    git = r.get("gitInfo") or {}
    ready = str(r.get("state", "")).upper() == "READY"
    via = "source" if rid in kept and ready else ("image" if r.get("imageName") and ready else None)
    return {"id": rid, "state": r.get("state"), "current": rid == current, "port": r.get("port"), "type": r.get("projectType") or r.get("type"),
            "image": r.get("imageName"), "created_at": r.get("createdAt") or r.get("created_at"), "message": git.get("message") or r.get("message"), "commit": git.get("commit"),
            "branch": git.get("branch"), "rollback": None if rid == current else via,
            "disks": [d for d in (r.get("disks") or []) if isinstance(d, dict) and d.get("name") and d.get("mountTo")]}


def _image_source(image: str) -> Path:
    """A source archive that holds only `FROM <image>`: building it makes Liara start the app from that earlier image again."""
    import io
    import tarfile
    import tempfile
    data = f"FROM {image}\n".encode()
    out = Path(tempfile.mkstemp(suffix=".tar.gz")[1])
    with tarfile.open(out, "w:gz") as tar:
        info = tarfile.TarInfo("Dockerfile")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return out


def list_releases(client: LiaraClient, app: str, state_dir: Path, count: int = 20) -> dict:
    data = client.releases(app, 1, count)
    kept = {p.name.removesuffix(".tar.gz") for p in _dir(state_dir).glob("*.tar.gz")} if _dir(state_dir).is_dir() else set()
    current = str(data.get("currentRelease") or "")
    return {"app": app, "current": current, "total": data.get("total"), "releases": [_release_row(r, current, kept) for r in data.get("releases", [])]}


def rollback(client: LiaraClient, app: str, release_id: str, state_dir: Path, *, emit: Callable[[str, str], None] = lambda kind, text: None) -> dict:
    """Make an earlier release the running one again. Returns {release_id, state, ok, via}."""
    target = next((r for r in list_releases(client, app, state_dir, 50)["releases"] if r["id"] == release_id), None)
    if target is None:
        raise HistoryError("release_not_found", "that release is not in this app's history", 404)
    if target["current"]:
        raise HistoryError("release_current", "that release is already the running one")
    if str(target["state"]).upper() != "READY":
        raise HistoryError("release_not_ready", "only a release that was running fine can be rolled back to", state=target["state"])
    msg = f"rollback to {release_id[:8]}"
    saved = stored(state_dir, release_id) if target["rollback"] == "source" else None
    if saved:
        archive, meta = saved
        sid = client.upload_source(app, archive)
        rid = client.create_release(app, sid, meta["platform"], meta["port"], disks=meta.get("disks") or None, platformConfig=meta.get("platform_config") or None, message=msg)
        keep = save_source(state_dir, archive)  # the new release is rolled back to this same source, so it keeps a copy too
        commit_source(keep, state_dir, rid, {**meta, "message": msg, "rolled_back_from": release_id})
        via = "source"
    elif target["rollback"] == "image":
        # Liara refuses its own private-registry image through the `image` parameter and says to deploy a one-line Dockerfile that starts FROM it (seen on a real app)
        arc = _image_source(target["image"])
        try:
            sid = client.upload_source(app, arc)
        finally:
            arc.unlink(missing_ok=True)
        rid = client.create_release(app, sid, "docker", target["port"] or 80, disks=target.get("disks") or None, message=msg)
        via = "image"
    else:
        raise HistoryError("rollback_unavailable", "this release cannot be rolled back to", release=release_id)
    rel = client.wait_for_release(rid, emit)
    return {"release_id": rid, "state": rel.get("state"), "ok": str(rel.get("state", "")).upper() == "READY", "via": via, "reason": str(rel.get("failReason") or "")[:200] or None}


def latest_meta(state_dir: Path) -> dict | None:
    """What the newest kept release was deployed with (platform, port, platform version, disks): a new release from fresh source reuses it."""
    d = _dir(state_dir)
    metas = [json.loads(p.read_text()) for p in d.glob("*.json")] if d.is_dir() else []
    return max(metas, key=lambda m: m.get("saved_at", 0)) if metas else None


def redeploy(client: LiaraClient, app: str, source_dir: Path, state_dir: Path, *, message: str, max_mb: float | None = None,
             emit: Callable[[str, str], None] = lambda kind, text: None, meta: dict | None = None) -> dict:
    """Upload fresh source to an app this agent created and release it, exactly as the first deploy did (same platform, port, version, disks).
    Nothing new is created and no setting changes. Returns {release_id, state, ok}."""
    from liara_agent.liara.archive import build_archive
    meta = meta or latest_meta(state_dir)
    if not meta:
        raise HistoryError("no_previous_release", "this app has no earlier deploy to copy the settings from")
    arc = build_archive(source_dir, max_mb=max_mb)
    try:
        sid = client.upload_source(app, arc.path)
        saved = save_source(state_dir, arc.path)
    finally:
        arc.path.unlink(missing_ok=True)
    try:
        rid = client.create_release(app, sid, meta["platform"], meta["port"], disks=meta.get("disks") or None, platformConfig=meta.get("platform_config") or None, message=message)
    except BaseException:
        discard(saved)
        raise
    commit_source(saved, state_dir, rid, {**meta, "message": message})
    rel = client.wait_for_release(rid, emit)
    return {"release_id": rid, "state": rel.get("state"), "ok": str(rel.get("state", "")).upper() == "READY"}
