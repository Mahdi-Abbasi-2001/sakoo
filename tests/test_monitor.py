import time

from liara_agent.web import monitor
from test_heal import READY
from test_history import Releases
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401
from test_web_history import LISTED


def test_summary_counts_uptime_and_outages(make):
    c, app, _ = make()
    register(c)
    pid = c.post("/api/projects", data={"name": "x"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    db, now = app.state.db, time.time()
    for i, ok in enumerate([1, 1, 0, 0, 1, 1]):
        db.run("INSERT INTO checks(project_id,url,ts,ok,status,ms) VALUES (?,?,?,?,?,?)", (pid, "https://x.liara.run", now - 600 + i * 60, ok, 200 if ok else 502, 100))
    s = monitor.summary(db, pid, now)
    assert s["checks"] == 6 and s["up_percent"] == 66.7 and s["current"] is True
    assert len(s["incidents"]) == 1 and s["incidents"][0]["up_at"] is not None
    db.run("INSERT INTO checks(project_id,url,ts,ok,status,ms) VALUES (?,?,?,?,?,?)", (pid, "u", now, 0, None, 10))
    assert monitor.summary(db, pid, now + 1)["incidents"][-1]["up_at"] is None   # still down


def test_old_checks_are_pruned_and_a_failed_connection_is_a_down_check(make):
    c, app, _ = make()
    register(c)
    pid = c.post("/api/projects", data={"name": "x"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    db = app.state.db
    db.run("INSERT INTO checks(project_id,url,ts,ok,status,ms) VALUES (?,?,?,?,?,?)", (pid, "u", time.time() - 9 * 86400, 1, 200, 1))
    r = monitor.record(db, pid, "https://x.liara.run", lambda u: (False, None, 5))
    assert r["ok"] is False and db.one("SELECT COUNT(*) n FROM checks")["n"] == 1


def test_uptime_endpoint_checks_only_a_running_app(make, monkeypatch):
    seen = []
    monkeypatch.setattr(monitor, "ping", lambda u: (seen.append(u) or (True, 200, 42)))
    c, app, _ = make(Releases(LISTED, "r2", [READY]))
    register(c)
    connect(c)
    pid = c.post("/api/projects", data={"name": "x"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    assert c.post(f"/api/projects/{pid}/uptime/check").json()["detail"]["code"] == "no_live_app"
    assert c.get(f"/api/projects/{pid}/uptime").json()["checks"] == 0
    pid2, rid = deploy_and_approve(c, "blog")
    out = c.post(f"/api/projects/{pid2}/uptime/check").json()
    assert out["checks"] == 1 and out["current"] is True and seen and "liara.run" in seen[0]
    c2, *_ = make()
    register(c2, "bob")
    assert c2.get(f"/api/projects/{pid2}/uptime").status_code == 404
