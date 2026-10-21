import time

from liara_agent.web import sources
from test_heal import READY
from test_history import Releases
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401
from test_web_history import LISTED
from test_web_branches import fake_clone


def live_git_project(make, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "clone_git", fake_clone(calls))
    fake = Releases(LISTED, "r2", [READY])
    c, app, deps = make(fake)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    app.state.db.run("UPDATE projects SET source_kind='git', git_url='https://github.com/a/b', git_branch='main' WHERE id=?", (pid,))
    return c, app, fake, pid, calls


def wait_state(c, pid, want, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/projects/{pid}/deploys").json()["deploys"]
        if d and d[0]["state"] == want:
            return d[0]
        time.sleep(0.05)
    raise AssertionError(c.get(f"/api/projects/{pid}/deploys").json())


def test_release_latest_code_now(make, monkeypatch):
    c, app, fake, pid, calls = live_git_project(make, monkeypatch)
    out = c.post(f"/api/projects/{pid}/redeploy").json()
    assert out["status"] == "started" and out["run_id"]
    d = wait_state(c, pid, "succeeded")
    assert d["run_id"] == out["run_id"]                                       # the release has a run page of its own
    run = c.get(f"/api/runs/{out['run_id']}").json()
    assert run["mode"] == "release"
    for _ in range(50):                                                       # the run's last event comes just after the row's state
        events = c.get(f"/api/runs/{out['run_id']}/events").json()["events"]
        if events and events[-1]["kind"] == "run_finished":
            break
        time.sleep(0.05)
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "run_started" and events[0]["data"]["release"] is True and "step_started" in kinds and "step_finished" in kinds
    assert events[-1]["data"]["ok"] is True and events[-1]["data"]["created"]["apps"] == []        # nothing created: nothing for "delete what this run made" to remove
    assert c.get(f"/api/projects/{pid}").json()["last_run"]["mode"] == "deploy"                    # a release is not the project's last deployment
    assert d["message"] == "released from the page" and d["error"] is None and calls and calls[-1][2] == "main"
    kept = list((app.state.settings.data_dir / "projects" / str(pid) / "src" / ".agent-state" / "releases").glob("*.json"))
    assert len(kept) == 2   # the first deploy's source and the new one, both kept for rollback


def test_only_a_git_project_with_a_running_app_can_release_newer_code(make, monkeypatch):
    c, app, fake, pid, calls = live_git_project(make, monkeypatch)
    app.state.db.run("UPDATE projects SET source_kind='zip', git_url=NULL WHERE id=?", (pid,))
    assert c.post(f"/api/projects/{pid}/redeploy").json()["detail"]["code"] == "not_a_git_project"


def test_a_failed_clone_is_a_failed_row_not_a_crash(make, monkeypatch):
    c, app, fake, pid, calls = live_git_project(make, monkeypatch)

    def broken(*a, **k):
        raise sources.SourceError("git_failed", "could not download")
    monkeypatch.setattr(sources, "clone_git", broken)
    assert c.post(f"/api/projects/{pid}/redeploy").status_code == 202
    d = wait_state(c, pid, "failed")
    assert d["error"] == "git_failed"
    for _ in range(50):
        events = c.get(f"/api/runs/{d['run_id']}/events").json()["events"]
        if events and events[-1]["kind"] == "run_finished":
            break
        time.sleep(0.05)
    assert events[-1]["data"]["ok"] is False and events[-1]["data"]["reason_code"] == "release_failed"
