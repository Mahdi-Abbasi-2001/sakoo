import time

from liara_agent import wpdeliver
from test_web import register, make  # noqa: F401
from test_wpdeliver import FakeFTP, site


def wp_project(make, tmp_path):
    c, app, _ = make()
    register(c)
    pid = c.post("/api/projects", data={"name": "wp"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    src = app.state.settings.data_dir / "projects" / str(pid) / "src"
    site(src)
    return c, app, pid


def test_the_project_is_flagged_and_the_plan_lists_only_code(make, tmp_path):
    c, app, pid = wp_project(make, tmp_path)
    assert c.get(f"/api/projects/{pid}").json()["wordpress"] is True
    plan = c.get(f"/api/projects/{pid}/wordpress").json()
    assert plan["files"] == 2 and plan["parts"] == ["themes", "plugins"] and not any("config" in x for x in plan["sample"])
    assert c.get(f"/api/projects/{pid}/wordpress?uploads=true").json()["files"] == 3


def test_upload_needs_confirmation_runs_in_the_background_and_never_stores_the_password(make, tmp_path, monkeypatch):
    c, app, pid = wp_project(make, tmp_path)
    monkeypatch.setattr(wpdeliver.ftplib, "FTP_TLS", FakeFTP)
    monkeypatch.setattr(wpdeliver, "check_host", lambda h, resolve=None: None)
    body = {"host": "ftp.example.com", "user": "u", "password": "hunter2hunter2"}
    assert c.post(f"/api/projects/{pid}/wordpress/upload", json=body).json()["detail"]["code"] == "confirm_required"
    r = c.post(f"/api/projects/{pid}/wordpress/upload", json={**body, "confirm": True})
    assert r.status_code == 202 and r.json()["total"] == 2
    for _ in range(100):
        st = c.get(f"/api/projects/{pid}/wordpress/upload").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done" and st["sent"] == 2 and st["bytes"] == 6
    assert "hunter2" not in c.get(f"/api/projects/{pid}/wordpress/upload").text
    assert "hunter2" not in "".join(p.read_text(errors="ignore") for p in app.state.settings.data_dir.rglob("*.db*") if p.is_file())


def test_a_private_host_and_a_login_failure_are_reported(make, tmp_path, monkeypatch):
    c, app, pid = wp_project(make, tmp_path)
    r = c.post(f"/api/projects/{pid}/wordpress/upload", json={"host": "localhost", "user": "u", "password": "p", "confirm": True})
    assert r.status_code == 422 and r.json()["detail"]["code"] in ("wp_host_private", "wp_host_unresolved")
    class Bad(FakeFTP):
        def __init__(self, **kw):
            super().__init__(**kw); self.fail_login = True
    monkeypatch.setattr(wpdeliver.ftplib, "FTP_TLS", Bad)
    monkeypatch.setattr(wpdeliver, "check_host", lambda h, resolve=None: None)
    c.post(f"/api/projects/{pid}/wordpress/upload", json={"host": "ftp.example.com", "user": "u", "password": "p", "confirm": True})
    for _ in range(100):
        st = c.get(f"/api/projects/{pid}/wordpress/upload").json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "failed" and st["error"] == "wp_login_failed"
