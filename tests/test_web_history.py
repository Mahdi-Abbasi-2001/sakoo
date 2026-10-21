from test_heal import READY
from test_history import Releases
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401  (make and fast are fixtures)

LISTED = [{"_id": "r2", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img2", "gitInfo": {"message": "newer"}},
          {"_id": "r1", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img1"},
          {"_id": "r0", "state": "FAILED", "port": 3000, "projectType": "docker"}]


def deployed(make):
    fake = Releases(LISTED, "r2", [READY])
    c, app, deps = make(fake)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    return c, deps, pid


def test_history_lists_releases_and_what_can_be_rolled_back_to(make):
    c, deps, pid = deployed(make)
    out = c.get(f"/api/projects/{pid}/releases").json()
    assert out["app"] == "blog-web" and out["current"] == "r2"
    rows = {r["id"]: r for r in out["releases"]}
    assert rows["r2"]["current"] and rows["r2"]["rollback"] is None and rows["r2"]["message"] == "newer"
    assert rows["r1"]["rollback"] == "source"      # the run above deployed it, so its source was kept
    assert rows["r0"]["rollback"] is None


def test_rollback_needs_an_approval_and_then_redeploys_the_kept_source(make):
    c, deps, pid = deployed(make)
    url = f"/api/projects/{pid}/releases/r1/rollback"
    assert c.post(url, json={}).json()["detail"]["code"] == "confirm_required"
    uploads = len(deps.fake.uploads)
    r = c.post(url, json={"confirm": True})
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] and out["via"] == "source" and out["url"] == "https://blog-web.liara.run/" and out["serving"] is True
    assert len(deps.fake.uploads) == uploads + 1


def test_rollback_errors_are_translatable_codes(make):
    c, deps, pid = deployed(make)
    base = f"/api/projects/{pid}/releases"
    assert c.post(base + "/r2/rollback", json={"confirm": True}).json()["detail"]["code"] == "release_current"
    assert c.post(base + "/r0/rollback", json={"confirm": True}).json()["detail"]["code"] == "release_not_ready"
    gone = c.post(base + "/nope/rollback", json={"confirm": True})
    assert gone.status_code == 404 and gone.json()["detail"]["code"] == "release_not_found"


def test_history_needs_a_running_app_and_the_right_user(make):
    fake = Releases(LISTED, "r2", [READY])
    c, _, _ = make(fake)
    register(c)
    connect(c)
    pid = c.post("/api/projects", data={"name": "fresh"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    assert c.get(f"/api/projects/{pid}/releases").json()["detail"]["code"] == "no_live_app"
