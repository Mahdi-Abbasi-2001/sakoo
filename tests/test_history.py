import json

import httpx
import pytest
from test_heal import READY, FakeLiara

from liara_agent.deploy import executor
from liara_agent.deploy.executor import execute
from liara_agent.deploy.history import KEEP, HistoryError, list_releases, rollback, stored
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.plan.model import AppSpec, DeployPlan

CAT = snapshot_catalog()


class Releases(FakeLiara):
    """The deploy fake plus the release list of an app."""

    def __init__(self, releases_list, current, scripted=None):
        super().__init__(scripted or [READY])
        self.listed, self.current = releases_list, current

    def __call__(self, req):
        if req.method == "GET" and req.url.path.endswith("/releases"):
            return httpx.Response(200, json={"total": len(self.listed), "currentRelease": self.current, "readyReleasesCount": 2, "releases": self.listed})
        return super().__call__(req)


def client_for(fake, owned=("shop",)):
    j = Journal()
    j.apps = list(owned)
    return LiaraClient("tok", dry_run=False, journal=j, http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake)))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))


def deploy(tmp_path, fake, n=1):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    c = client_for(fake)
    for _ in range(n):
        out = execute(DeployPlan(apps=[AppSpec(name="shop", platform="docker", port=3000)]), CAT, c, tmp_path, network_id="n1")
        assert out.ok
        c.journal.apps = ["shop"]  # the fake does not need the app created again
    return c


def test_every_deployed_source_is_kept_with_what_it_was_deployed_with(tmp_path):
    fake = Releases([], "")
    deploy(tmp_path, fake)
    archive, meta = stored(tmp_path / ".agent-state", "r1")
    assert archive.is_file() and meta["platform"] == "docker" and meta["port"] == 3000 and meta["app"] == "shop"
    assert not list((tmp_path / ".agent-state" / "releases").glob("pending-*"))  # nothing half-saved is left behind


def test_only_the_last_few_sources_are_kept(tmp_path):
    deploy(tmp_path, Releases([], ""), n=KEEP + 3)
    kept = sorted(p.name for p in (tmp_path / ".agent-state" / "releases").glob("*.tar.gz"))
    assert len(kept) == KEEP and len(list((tmp_path / ".agent-state" / "releases").glob("*.json"))) == KEEP
    assert "r1.tar.gz" not in kept and f"r{KEEP + 3}.tar.gz" in kept


def releases_after(tmp_path):
    fake = Releases([
        {"_id": "r3", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img3", "gitInfo": {"message": "third"}},
        {"_id": "r2", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img2"},
        {"_id": "r1", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img1"},
        {"_id": "r0", "state": "FAILED", "port": 3000, "projectType": "docker", "imageName": "img0"},
        {"_id": "old", "state": "READY", "port": 8080, "projectType": "node", "imageName": "img-old", "createdAt": "2026-10-01T10:00:00Z"},
    ], "r3")
    c = deploy(tmp_path, fake, n=3)  # keeps r1, r2, r3
    fake.n = 10
    return fake, c


def test_the_history_says_which_releases_can_be_rolled_back_to_and_how(tmp_path):
    fake, c = releases_after(tmp_path)
    rows = {r["id"]: r for r in list_releases(c, "shop", tmp_path / ".agent-state")["releases"]}
    assert rows["r3"]["current"] and rows["r3"]["rollback"] is None and rows["r3"]["message"] == "third"
    assert rows["r2"]["rollback"] == "source" and rows["r1"]["rollback"] == "source"
    assert rows["r0"]["rollback"] is None                       # a failed release is not something to go back to
    assert rows["old"]["rollback"] == "image" and rows["old"]["created_at"] == "2026-10-01T10:00:00Z"   # not made by this agent: Liara's own image, the less certain route


def test_rollback_uploads_the_kept_source_again_and_deploys_it(tmp_path):
    fake, c = releases_after(tmp_path)
    uploads_before = len(fake.uploads)
    out = rollback(c, "shop", "r2", tmp_path / ".agent-state")
    assert out["ok"] and out["via"] == "source" and out["release_id"] == f"r{fake.n}"
    assert len(fake.uploads) == uploads_before + 1
    release = [call for call in fake.calls if call[1].endswith("/releases")][-1][2]
    assert release["type"] == "docker" and release["port"] == 3000 and release["message"] == "rollback to r2" and "image" not in release
    # the new release keeps a copy of the same source, so history stays consistent
    assert stored(tmp_path / ".agent-state", out["release_id"])[1]["rolled_back_from"] == "r2"


def test_rollback_to_a_release_the_agent_did_not_make_redeploys_liaras_image(tmp_path):
    fake, c = releases_after(tmp_path)
    out = rollback(c, "shop", "old", tmp_path / ".agent-state")
    assert out["via"] == "image" and out["ok"]
    release = [call for call in fake.calls if call[1].endswith("/releases")][-1][2]
    assert release["type"] == "docker" and release["port"] == 8080 and "image" not in release and release["sourceID"]   # a one-line Dockerfile source, not the image parameter


@pytest.mark.parametrize("rid,code,status", [("nope", "release_not_found", 404), ("r3", "release_current", 409), ("r0", "release_not_ready", 409)])
def test_rollback_refuses_what_makes_no_sense(tmp_path, rid, code, status):
    fake, c = releases_after(tmp_path)
    with pytest.raises(HistoryError) as e:
        rollback(c, "shop", rid, tmp_path / ".agent-state")
    assert e.value.code == code and e.value.status == status


def test_rollback_only_touches_apps_this_agent_created(tmp_path):
    from liara_agent.liara.client import NotOwnedError
    fake, c = releases_after(tmp_path)
    c.journal.apps = []
    with pytest.raises(NotOwnedError):
        rollback(c, "shop", "r2", tmp_path / ".agent-state")


def test_a_failed_rollback_build_is_reported_not_hidden(tmp_path):
    fake, c = releases_after(tmp_path)
    fake.releases = [{"state": "FAILED", "failReason": "build failed"}]
    out = rollback(c, "shop", "r2", tmp_path / ".agent-state")
    assert out["ok"] is False and out["state"] == "FAILED"
