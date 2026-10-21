import json
import tarfile
from pathlib import Path

import httpx
import pytest

from liara_agent.deploy import executor
from liara_agent.deploy.executor import execute
from liara_agent.heal.loop import HealIO
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.plan.model import AppSpec, DeployPlan

CAT = snapshot_catalog()


class FakeLiara:
    """Replays a scripted list of release outcomes and runtime logs; records every mutating call."""

    def __init__(self, releases, runtime=None):
        self.releases, self.runtime, self.calls, self.uploads = list(releases), list(runtime or []), [], []
        self.n = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        if m != "GET":
            self.calls.append((m, path, json.loads(req.content) if req.headers.get("content-type", "").startswith("application/json") and req.content else None))
        if path == "/v1/projects" and m == "GET":
            return httpx.Response(200, json={"projects": []})
        if path == "/v1/networks":   # every app needs a private network: the account starts with none, and the agent makes one
            return httpx.Response(200, json={"networks": []} if m == "GET" else {"network": {"_id": "net-new"}})
        if path.endswith("/sources"):
            self.uploads.append(req.content)
            return httpx.Response(200, json={"sourceID": f"src{len(self.uploads)}"})
        if path.endswith("/releases") and m == "POST":
            self.n += 1
            return httpx.Response(200, json={"releaseID": f"r{self.n}"})
        if "/build-logs" in path:
            rel = self.releases[min(self.n, len(self.releases)) - 1]
            return httpx.Response(200, json={"release": rel, "buildOutput": []})
        if path.endswith("/logs"):
            lines = self.runtime.pop(0) if self.runtime else []
            return httpx.Response(200, json={"data": [{"metaData": {}, "values": [[str(i), ln] for i, ln in enumerate(lines)]}]})
        return httpx.Response(200, json={})


def run(tmp_path, fake, *, io, files=None, port=3000, platform="docker", env=None):
    for name, text in (files or {"Dockerfile": "FROM python:3.12\n"}).items():
        (tmp_path / name).write_text(text)
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake))
    client = LiaraClient("tok", dry_run=False, journal=Journal(), http=http)
    plan = DeployPlan(apps=[AppSpec(name="t-web", platform=platform, port=port, env=env or {})])
    events = []
    out = execute(plan, CAT, client, tmp_path, network_id="n1", emit=lambda k, d: events.append((k, d)), heal=io)
    return out, client, events


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    results = {"ok": True, "detail": "HTTP 200"}
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (results["ok"], results["detail"]))
    return results


TIMEOUT = {"state": "FAILED", "failReason": "build took more than 5 minutes"}
READY = {"state": "READY"}
ops = lambda fake: [c[1] for c in fake.calls]  # noqa: E731


def test_build_timeout_resizes_after_confirmation_then_succeeds(tmp_path):
    fake = FakeLiara([TIMEOUT, READY])
    asked = []
    out, client, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: asked.append(q) or True))
    assert out.ok and out.urls == {"t-web": "https://t-web.liara.run/"}
    assert not any("/resize" in p for p in ops(fake))  # Liara's resize endpoint rejects bundlePlanID: a tier change recreates the app instead
    steps = [(c[0], c[1]) for c in fake.calls if c[1] in ("/v1/projects/t-web", "/v1/projects")]
    assert steps == [("POST", "/v1/projects"), ("DELETE", "/v1/projects/t-web"), ("POST", "/v1/projects")]  # created at basic, deleted, created again
    created = [c[2] for c in fake.calls if (c[0], c[1]) == ("POST", "/v1/projects")]
    assert created[0]["bundlePlanID"] == "basic" and created[1]["bundlePlanID"] == "standard" and created[1]["name"] == "t-web"
    assert "toman/hour" in asked[0] and "15 minutes" in asked[0]  # the user sees the price and what they get
    assert len(fake.uploads) == 2  # redeployed after the plan change


def test_declining_the_paid_fix_stops_with_a_clear_diagnosis(tmp_path):
    fake = FakeLiara([TIMEOUT])
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: False))
    assert not out.ok and out.diagnosis.code == "build_timeout"
    assert not any("/resize" in p for p in ops(fake)) and len(fake.uploads) == 1


def test_default_io_never_spends_money(tmp_path):
    fake = FakeLiara([TIMEOUT])
    out, _, _ = run(tmp_path, fake, io=HealIO())
    assert not out.ok and not any("/resize" in p for p in ops(fake))


def test_code_patch_goes_to_a_copy_and_never_touches_the_users_files(tmp_path):
    crash = {"state": "UNHEALTHY"}
    fake = FakeLiara([crash, READY], runtime=[["ModuleNotFoundError: No module named 'flask_cors'"]])
    files = {"Dockerfile": "FROM python:3.12\n", "requirements.txt": "flask\n"}
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: True), files=files)
    assert out.ok
    assert (tmp_path / "requirements.txt").read_text() == "flask\n"  # original untouched
    assert "flask_cors" in (tmp_path / ".agent-state" / "patched" / "t-web" / "requirements.txt").read_text()
    assert "+flask_cors" in (tmp_path / ".agent-state" / "fixes.patch").read_text()
    names = lambda blob: tarfile.open(fileobj=__import__("io").BytesIO(blob.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0])).extractfile("requirements.txt").read()  # noqa: E731
    assert b"flask_cors" not in names(fake.uploads[0]) and b"flask_cors" in names(fake.uploads[1])


def test_wrong_port_is_updated_without_redeploying(tmp_path, no_real_http):
    fake = FakeLiara([READY], runtime=[["Server listening on port 8080"]])
    state = iter([(False, "HTTP 502"), (True, "HTTP 200")])
    executor.verify_http = lambda url, **kw: next(state)
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: True))
    assert out.ok and {"project": "t-web", "port": 8080} == next(c for c in fake.calls if c[1].endswith("update-port"))[2]
    assert len(fake.uploads) == 1


def test_missing_env_is_asked_for_and_secret_values_are_not_recorded(tmp_path):
    fake = FakeLiara([{"state": "UNHEALTHY"}, READY], runtime=[["Error: JWT_SECRET is not defined"]])
    out, client, _ = run(tmp_path, fake, io=HealIO(ask_value=lambda n: "hunter2-hunter2"))
    assert out.ok
    assert "hunter2" not in json.dumps([a.body for a in client.actions])
    sent = next(c for c in fake.calls if c[1].endswith("update-envs"))[2]
    assert sent["variables"] == [{"key": "JWT_SECRET", "value": "hunter2-hunter2"}]  # the real value did go to Liara


def test_blocked_foreign_service_is_reported_not_retried(tmp_path):
    fake = FakeLiara([{"state": "UNHEALTHY"}], runtime=[["httpx.ConnectTimeout: api.telegram.org"]])
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: True))
    assert not out.ok and out.diagnosis.code == "outbound_blocked" and len(fake.uploads) == 1


def test_escalation_asks_each_time_and_never_repeats_a_change(tmp_path):
    fake = FakeLiara([TIMEOUT] * 6)
    asked = []
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: asked.append(q) or True))
    assert not out.ok
    sizes = [c[2]["bundlePlanID"] for c in fake.calls if (c[0], c[1]) == ("POST", "/v1/projects")][1:]  # the first create is the original app
    assert sizes == ["standard", "pro"]  # silver, then gold; the same change is never applied twice
    assert len(asked) == len(sizes)  # every paid step was confirmed separately
    assert len(fake.uploads) <= 4  # bounded by max_heal_attempts


def test_an_identical_fix_is_skipped(tmp_path):
    """If the diagnosis proposes exactly what was already tried, the loop stops instead of looping."""
    fake = FakeLiara([{"state": "READY"}] * 6, runtime=[["Server listening on port 8080"]] * 6)
    calls = iter([(False, "HTTP 502")] * 6)
    executor.verify_http = lambda url, **kw: next(calls)
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: True))
    assert not out.ok
    assert sum(1 for c in fake.calls if c[1].endswith("update-port")) == 1


def test_no_healing_in_dry_run(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM x\n")
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    client = LiaraClient("tok", dry_run=True, journal=Journal(), http=http)
    out = execute(DeployPlan(apps=[AppSpec(name="t-web", platform="docker", port=3000)]), CAT, client, tmp_path, network_id=None, heal=HealIO(confirm=lambda q, **kw: True))
    assert out.ok and not any(a.op == "resize_app" for a in client.actions)


@pytest.mark.parametrize("rel", [{"state": "BUILD_TIMEOUT"}, {"state": "BUILDING", "failReason": "build took more than 5 minutes"}, {"state": "CANCELED"}])
def test_wait_for_release_ends_on_any_failure_shaped_release(rel):
    from liara_agent.liara.client import LiaraClient
    c = LiaraClient("t", team_id="", dry_run=False)
    c.build_logs = lambda rid, since=None: {"buildOutput": [], "release": rel}
    out = c.wait_for_release("r1", poll_seconds=0)
    assert out["state"] == "FAILED" and out["failReason"]


NODE_PIN = {"package.json": json.dumps({"name": "x", "engines": {"node": "13.12.0"}, "scripts": {"start": "node s.js"}}), "s.js": "1\n"}


def test_an_exact_node_pin_is_patched_before_the_first_build(tmp_path):
    fake = FakeLiara([READY])
    asked, heal_events = [], []
    io = HealIO(confirm=lambda q, **kw: asked.append(kw.get("code")) or True, emit=lambda k, d: heal_events.append(k))
    out, _, events = run(tmp_path, fake, io=io, files=NODE_PIN, platform="node")
    assert out.ok and asked == ["patch_node_engine"]
    assert len(fake.uploads) == 1  # one build only: no failed build before the fix
    releases = [c for c in fake.calls if c[1].endswith("/releases")]
    assert releases[0][2]["platformConfig"] == {"version": "14"}
    assert json.loads((tmp_path / "package.json").read_text())["engines"]["node"] == "13.12.0"  # the user's file is untouched
    assert json.loads((tmp_path / ".agent-state" / "patched" / "t-web" / "package.json").read_text())["engines"]["node"] == ">=13.12.0"
    assert heal_events == ["patch_ready", "fix_applied"] and not [k for k, _ in events if k == "heal_attempt"]  # offered up front, not counted as a heal attempt


def test_declining_the_node_patch_still_builds_and_heals_afterwards(tmp_path):
    fake = FakeLiara([{"state": "FAILED", "failReason": 'The engine "node" is incompatible with this module. Expected version "13.12.0"'}, READY])
    answers = iter([False, True])
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: next(answers)), files=NODE_PIN, platform="node")
    assert out.ok and len(fake.uploads) == 2


def test_no_node_patch_offer_when_the_pin_is_satisfied(tmp_path):
    fake = FakeLiara([READY])
    files = {"package.json": json.dumps({"name": "x", "engines": {"node": ">=18"}}), "s.js": "1\n"}
    asked = []
    out, _, _ = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: asked.append(q) or True), files=files, platform="node")
    assert out.ok and not asked
