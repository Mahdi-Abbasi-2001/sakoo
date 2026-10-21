import io
import json
import sqlite3
import time
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient
from test_heal import READY, FakeLiara

from liara_agent.config import Settings
from liara_agent.deploy import executor
from liara_agent.liara.client import LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.web.app import create_app
from liara_agent.web.config import WebSettings

ORIGIN = {"Origin": "http://localhost:3000"}
TOKEN = "liara-test-token-0123456789"


class FakeAPI:
    def __init__(self, teams=None, networks=None):
        self.teams = teams if teams is not None else []
        self.networks = networks if networks is not None else []   # none: the agent makes the project's own network (with some, it asks which)

    def list_teams(self):
        return self.teams

    def list_networks(self):
        return self.networks

    def list_app_ids(self):
        return ["existing-app"]

    def live_catalog(self):
        return snapshot_catalog()


class FakeDeps:
    def __init__(self, fake_liara, api=None, llm=None):
        self.fake, self._api, self._llm = fake_liara, api or FakeAPI(), llm
        self.tokens_seen = []

    def api(self, token, team_id):
        self.tokens_seen.append(token)
        if token == "invalid-token-000000":
            raise httpx.HTTPStatusError("401", request=httpx.Request("GET", "https://api.liara.ir/v1/me"), response=httpx.Response(401))
        if token == "forbidden-token-000000":
            raise httpx.HTTPStatusError("403", request=httpx.Request("GET", "https://api.liara.ir/v1/me"), response=httpx.Response(403))
        if token == "unreachable-token-0000":
            raise httpx.ConnectError("no route")
        return self._api

    def client(self, token, team_id, dry_run, journal):
        http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(self.fake))
        dns = getattr(self.fake, "dns", None)
        dns_http = httpx.Client(base_url="https://dns-service.iran.liara.ir", transport=httpx.MockTransport(dns)) if dns else None
        return LiaraClient(token, dry_run=dry_run, journal=journal, http=http, dns_http=dns_http)

    def llm(self, settings):
        return self._llm


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))


@pytest.fixture
def make(tmp_path):
    def _make(fake=None, api=None, llm=None, **ws):
        ws.setdefault("static_dir", tmp_path / "no-frontend")  # hermetic: never pick up the project's real frontend/dist
        settings = WebSettings(data_dir=tmp_path / "data", secret_key="test-secret", request_timeout_seconds=5, **ws)
        deps = FakeDeps(fake or FakeLiara([READY]), api, llm)
        app = create_app(settings, deps, Settings(liara_api_token="", llm_api_key=""))
        return TestClient(app, headers=ORIGIN), app, deps

    return _make


def register(c, name="alice", pw="password123"):
    r = c.post("/api/auth/register", json={"username": name, "password": pw})
    assert r.status_code == 201, r.text
    return r


def dockerfile_zip(name="proj"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/Dockerfile", "FROM python:3.12\nEXPOSE 3000\n")
        z.writestr(f"{name}/docker-compose.yml", "services:\n  web:\n    build: .\n    ports: ['3000:3000']\n")
    return buf.getvalue()


def new_project(c, name="blog"):
    r = c.post("/api/projects", data={"name": name}, files={"file": ("p.zip", dockerfile_zip(), "application/zip")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def connect(c):
    r = c.put("/api/settings", json={"liara_token": TOKEN, "language": "en"})
    assert r.status_code == 200 and r.json()["has_liara_token"]


# ---------------------------------------------------------------- auth ----
def test_register_login_logout_and_me(make):
    c, _, _ = make()
    assert c.get("/api/me").status_code == 401
    register(c)
    assert c.get("/api/me").json()["username"] == "alice"
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/auth/login", json={"username": "alice", "password": "password123"}).status_code == 200
    assert c.get("/api/me").status_code == 200


def test_bad_logins_and_validation(make):
    c, _, _ = make()
    register(c)
    assert c.post("/api/auth/register", json={"username": "alice", "password": "password123"}).status_code == 409
    assert c.post("/api/auth/register", json={"username": "x", "password": "password123"}).json()["detail"]["code"] == "bad_username"
    assert c.post("/api/auth/register", json={"username": "bobby", "password": "short"}).json()["detail"]["code"] == "weak_password"
    assert c.post("/api/auth/login", json={"username": "alice", "password": "wrong-pass"}).json()["detail"]["code"] == "bad_credentials"
    assert c.post("/api/auth/login", json={"username": "ghost", "password": "whatever1"}).json()["detail"]["code"] == "bad_credentials"  # same answer


def test_login_is_rate_limited(make):
    c, _, _ = make()
    register(c)
    codes = [c.post("/api/auth/login", json={"username": "alice", "password": f"wrong-{i}x"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


def test_session_cookie_is_httponly_and_only_its_digest_is_stored(make, tmp_path):
    c, app, _ = make()
    r = register(c)
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    raw = c.cookies.get("session")
    rows = sqlite3.connect(tmp_path / "data" / "app.db").execute("SELECT digest FROM sessions").fetchall()
    assert rows and raw not in rows[0][0] and len(rows[0][0]) == 64


def test_csrf_guard_blocks_foreign_origins_and_bare_requests(make):
    c, app, _ = make()
    register(c)
    evil = TestClient(app)  # a browser on another site: our session cookie, but no allowed Origin
    evil.cookies.set("session", c.cookies.get("session"))
    assert evil.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert evil.post("/api/auth/logout").status_code == 403  # no Origin and no X-Requested-With
    assert evil.post("/api/auth/logout", headers={"X-Requested-With": "XMLHttpRequest"}).status_code == 200


# ----------------------------------------------------- liara token storage ----
def test_liara_token_is_encrypted_at_rest_and_never_returned(make, tmp_path):
    c, _, _ = make()
    register(c)
    connect(c)
    assert TOKEN not in json.dumps(c.get("/api/settings").json()) and TOKEN not in json.dumps(c.get("/api/me").json())
    db_bytes = b"".join(f.read_bytes() for f in (tmp_path / "data").glob("app.db*"))
    assert TOKEN.encode() not in db_bytes
    c.delete("/api/settings/liara-token")
    assert c.get("/api/settings").json()["has_liara_token"] is False


def test_liara_check_validates_without_storing(make):
    c, _, deps = make(api=FakeAPI(teams=[{"_id": "t1", "name": "My Team"}], networks=[{"_id": "n1", "name": "buildx-net"}]))
    register(c)
    ok = c.post("/api/liara/check", json={"token": TOKEN}).json()
    assert ok == {"valid": True, "teams": [{"id": "t1", "name": "My Team"}], "team_required": True, "networks": ["buildx-net"], "apps": ["existing-app"],
                  "code": None, "params": {}}
    bad = c.post("/api/liara/check", json={"token": "invalid-token-000000"}).json()
    assert bad["valid"] is False and bad["code"] == "liara_token_invalid" and bad["params"] == {"status": 401}
    assert c.get("/api/settings").json()["has_liara_token"] is False  # checking never saves


def test_settings_validation(make):
    c, _, _ = make()
    register(c)
    assert c.put("/api/settings", json={"language": "xx"}).json()["detail"]["code"] == "bad_language"
    assert c.put("/api/settings", json={"language": "fa"}).json()["language"] == "fa"


# ------------------------------------------------------------- projects ----
def test_project_upload_list_and_delete(make, tmp_path):
    c, _, _ = make()
    register(c)
    pid = new_project(c)
    assert (tmp_path / "data" / "projects" / str(pid) / "src" / "Dockerfile").exists()
    assert [p["name"] for p in c.get("/api/projects").json()["projects"]] == ["blog"]
    assert c.delete(f"/api/projects/{pid}", params={"confirm": "wrong"}).json()["detail"]["code"] == "confirm_mismatch"
    assert c.delete(f"/api/projects/{pid}").status_code == 422        # the name must be typed
    out = c.delete(f"/api/projects/{pid}", params={"confirm": "blog"})
    assert out.status_code == 200 and out.json()["deleted"]["apps"] == []      # nothing was ever created on Liara
    assert c.get("/api/projects").json()["projects"] == [] and not (tmp_path / "data" / "projects" / str(pid)).exists()


def test_deleting_a_project_deletes_everything_its_runs_made_on_liara(make, tmp_path):
    c, _, deps = make()
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    r = c.delete(f"/api/projects/{pid}", params={"confirm": "blog"})
    assert r.status_code == 200 and r.json()["deleted"]["apps"] == ["blog-web"]
    assert [m for m, p, _ in deps.fake.calls if m == "DELETE"] == ["DELETE"]          # the app on Liara is gone
    assert c.get("/api/projects").json()["projects"] == [] and not (tmp_path / "data" / "runs" / rid).exists()


def test_if_liara_cannot_be_cleaned_the_project_is_kept(make):
    c, _, deps = make()
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    c.delete("/api/settings/liara-token")                                              # no way to delete on Liara any more
    r = c.delete(f"/api/projects/{pid}", params={"confirm": "blog"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "no_liara_token"
    assert [p["name"] for p in c.get("/api/projects").json()["projects"]] == ["blog"]  # kept: nothing is left running unseen


def test_hostile_uploads_are_rejected_cleanly(make, tmp_path):
    c, _, _ = make()
    register(c)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../evil.txt", "x")
        z.writestr("ok.txt", "x")
    r = c.post("/api/projects", data={"name": "bad"}, files={"file": ("p.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "path_traversal"
    assert c.get("/api/projects").json()["projects"] == [] and not list((tmp_path / "data" / "projects").glob("*"))
    assert c.post("/api/projects", data={"name": "n"}).json()["detail"]["code"] == "source_required"
    assert c.post("/api/projects", data={"name": "n", "git_url": "http://github.com/a/b"}).json()["detail"]["code"] == "git_scheme"
    assert c.post("/api/projects", data={"name": "n", "git_url": "https://evil.example/a/b"}).json()["detail"]["code"] == "git_host"


def test_upload_size_cap(make):
    c, _, _ = make(max_upload_mb=1)
    register(c)
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
        z.writestr("blob.bin", bytes(range(256)) * 6000 * 2)  # ~3 MB, stored uncompressed
    r = c.post("/api/projects", data={"name": "big"}, files={"file": ("p.zip", big.getvalue(), "application/zip")})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "upload_too_large"


# --------------------------------------------------------- user isolation ----
def test_users_cannot_see_or_touch_each_others_data(make):
    c, app, deps = make()
    register(c, "alice")
    connect(c)
    pid = new_project(c)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "preview"}).json()["id"]
    bob = TestClient(app, headers=ORIGIN)
    register(bob, "bobby")
    assert bob.get("/api/projects").json()["projects"] == [] and bob.get("/api/runs").json()["runs"] == []
    for method, path in [("get", f"/api/projects/{pid}"), ("delete", f"/api/projects/{pid}"), ("get", f"/api/runs/{rid}"),
                         ("get", f"/api/runs/{rid}/events"), ("get", f"/api/runs/{rid}/stream")]:
        assert getattr(bob, method)(path).status_code == 404, path
    assert bob.post(f"/api/runs/{rid}/respond", json={"request_id": "x", "value": True}).status_code == 404
    assert bob.post(f"/api/runs/{rid}/cleanup", json={"confirm": "blog"}).status_code == 404
    assert bob.post(f"/api/projects/{pid}/runs", json={}).status_code == 404


# ------------------------------------------------------------------ runs ----
def wait_for(c, rid, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/runs/{rid}").json()
        if pred(r):
            return r
        time.sleep(0.05)
    raise AssertionError(f"timed out; last: {r}")


def test_run_requires_a_connected_liara_account(make):
    c, _, _ = make()
    register(c)
    pid = new_project(c)
    assert c.post(f"/api/projects/{pid}/runs", json={}).json()["detail"]["code"] == "no_liara_token"


def test_preview_run_finishes_without_creating_anything(make):
    c, _, deps = make()
    register(c)
    connect(c)
    pid = new_project(c)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "preview"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed"))
    assert r["status"] == "succeeded" and r["summary"]["dry_run"] is True and deps.fake.calls == []
    assert TOKEN in deps.tokens_seen  # the decrypted token was used server-side only


def test_real_run_pauses_for_approval_then_deploys_after_the_browser_answers(make):
    c, _, deps = make()
    register(c)
    connect(c)
    pid = new_project(c)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] == "waiting")
    (req,) = r["open_requests"]
    assert req["kind"] == "confirm_request" and req["data"]["kind"] == "plan" and req["data"]["code"] == "create_resources" and req["data"]["price_delta_hourly"] > 0
    assert deps.fake.calls == []  # nothing was created while waiting
    assert c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True}).status_code == 200
    r = wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed"))
    assert r["status"] == "succeeded" and r["summary"]["created"]["apps"]
    assert r["open_requests"] == []


def test_declining_the_approval_creates_nothing(make):
    c, _, deps = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": False})
    assert wait_for(c, rid, lambda r: r["status"] == "cancelled")["status"] == "cancelled" and deps.fake.calls == []


def test_unknown_or_stale_request_ids_are_rejected(make):
    c, _, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "deploy"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "waiting")
    assert c.post(f"/api/runs/{rid}/respond", json={"request_id": "nope", "value": True}).json()["detail"]["code"] == "no_such_request"


def test_events_endpoint_supports_after_and_matches_replay_from_disk(make):
    c, app, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    ev = c.get(f"/api/runs/{rid}/events").json()["events"]
    assert [e["id"] for e in ev] == list(range(1, len(ev) + 1)) and ev[-1]["kind"] == "run_finished"
    assert [e["id"] for e in c.get(f"/api/runs/{rid}/events?after=3").json()["events"]][0] == 4
    app.state.manager.live.clear()  # as after a server restart: now served from the log file
    assert c.get(f"/api/runs/{rid}/events").json()["events"] == ev


def parse_sse(text):
    out = []
    for block in text.split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.split("\n") if ": " in l and not l.startswith(":"))
        if "data" in lines:
            out.append((int(lines["id"]), lines["event"], json.loads(lines["data"])))
    return out


def test_sse_stream_delivers_everything_in_order_and_resumes_from_last_event_id(make):
    c, _, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    full = parse_sse(c.get(f"/api/runs/{rid}/stream").text)
    assert [e[0] for e in full] == list(range(1, len(full) + 1)) and full[-1][1] == "run_finished" and full[0][1] == "run_started"
    resumed = parse_sse(c.get(f"/api/runs/{rid}/stream", headers={"Last-Event-ID": "5"}).text)
    assert [e[0] for e in resumed] == [e[0] for e in full if e[0] > 5]


def test_sse_stream_of_a_live_run_ends_when_the_run_does(make):
    c, _, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    import threading
    threading.Timer(0.5, lambda: c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})).start()
    kinds = [e[1] for e in parse_sse(c.get(f"/api/runs/{rid}/stream").text)]  # blocks until run_finished
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished" and "confirm_response" in kinds


def test_team_account_without_a_team_selected_gets_a_clear_error(make):
    c, _, _ = make(api=FakeAPI(teams=[{"_id": "t1", "name": "T"}]))
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] == "failed")
    assert r["summary"]["reason_code"] == "team_required"


def test_several_networks_are_the_agents_business_not_a_reason_to_stop(make):
    """The person is never asked for a network up front: the agent picks or makes the project's network itself (and asks only on a real run)."""
    nets = [{"_id": "a", "name": "net-a"}, {"_id": "b", "name": "net-b"}]
    c, _, _ = make(api=FakeAPI(networks=nets))
    register(c)
    connect(c)
    pid = new_project(c)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "preview"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed"))
    assert r["status"] == "succeeded"
    plan = next(e for e in c.get(f"/api/runs/{rid}/events").json()["events"] if e["kind"] == "plan_ready")["data"]
    assert plan["network"] is not None                                              # Liara needs a network for every app


def test_concurrent_run_limit(make):
    c, _, _ = make(max_concurrent_runs_per_user=1)
    register(c)
    connect(c)
    pid = new_project(c)
    first = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    wait_for(c, first, lambda r: r["status"] == "waiting")
    assert c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).status_code == 429


def test_unexpected_errors_end_the_run_without_leaking_details(make):
    class Boom(FakeAPI):
        def list_teams(self):
            raise RuntimeError("secret internal detail: " + TOKEN)

    c, _, _ = make(api=Boom())
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] == "failed")
    assert r["summary"]["reason_code"] == "internal_error" and TOKEN not in json.dumps(r) and "secret internal" not in json.dumps(r)


def test_cleanup_deletes_only_what_the_run_created_and_needs_typed_confirmation(make):
    c, _, deps = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c, 'blog')}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    assert c.post(f"/api/runs/{rid}/cleanup", json={"confirm": "wrong"}).json()["detail"]["code"] == "confirm_mismatch"
    r = c.post(f"/api/runs/{rid}/cleanup", json={"confirm": "blog"})
    assert r.status_code == 200 and r.json()["deleted"]["apps"] == ["blog-web"]
    assert [m for m, p, _ in deps.fake.calls if m == "DELETE"] == ["DELETE"]
    assert c.post(f"/api/runs/{rid}/cleanup", json={"confirm": "blog"}).json()["deleted"]["apps"] == []  # nothing left to delete


def test_runs_left_running_by_a_restart_are_marked_interrupted(make, tmp_path):
    c, app, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "deploy"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "waiting")
    from liara_agent.web.runs import RunManager
    RunManager(app.state.settings, app.state.db, app.state.manager.deps, app.state.manager.agent_settings)  # a fresh process starting up
    app.state.manager.live.clear()
    assert c.get(f"/api/runs/{rid}").json()["status"] == "interrupted"


def test_answers_must_be_one_of_the_offered_options(make):
    c, _, _ = make()
    register(c)
    connect(c)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Dockerfile", "FROM x\n")
        z.writestr("docker-compose.yml", "services:\n  worker:\n    build: .\n")
    pid = c.post("/api/projects", data={"name": "w"}, files={"file": ("p.zip", buf.getvalue(), "application/zip")}).json()["id"]
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    assert req["kind"] == "question" and req["data"]["options"] == ["worker", "needs_port"]
    bad = c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": "sure, a worker"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_option"
    assert c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": "worker"}).status_code == 200


def deploy_and_approve(c, name="blog"):
    pid = new_project(c, name)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    return pid, rid


def test_project_rows_carry_last_run_live_resources_cost_and_free_address_deadline(make):
    c, _, _ = make()
    register(c)
    connect(c)
    empty = new_project(c, "fresh")
    pid, rid = deploy_and_approve(c, "blog")
    rows = {p["name"]: p for p in c.get("/api/projects").json()["projects"]}
    assert rows["fresh"]["last_run"] is None and rows["fresh"]["live"] is None
    row = rows["blog"]
    assert row["last_run"]["status"] == "succeeded" and row["last_run"]["result"]["ok"] is True and row["last_run"]["result"]["created"]["apps"] == ["blog-web"]
    live = row["live"]
    assert live["run_id"] == rid and live["urls"] == {"blog-web": "https://blog-web.liara.run/"}
    assert [(r["kind"], r["id"], r["tier_label"]) for r in live["resources"]] == [("app", "blog-web", "basic")]
    assert live["hourly_incl_vat"] == 1145.83 and live["monthly_incl_vat"] == 825000
    assert abs(live["free_address_expires_at"] - (time.time() + 7 * 86400)) < 60  # seven days from the first deploy


def test_live_cost_follows_a_priced_fix_and_disappears_after_cleanup(make):
    from test_heal import TIMEOUT
    c, _, _ = make(fake=FakeLiara([TIMEOUT, READY]))
    register(c)
    connect(c)
    pid = new_project(c, "blog")
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})
    fix = wait_for(c, rid, lambda r: any(q["kind"] == "confirm_request" and q["data"]["kind"] == "fix" for q in r["open_requests"]))["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": fix["data"]["id"], "value": True})
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    live = c.get(f"/api/projects/{pid}").json()["live"]
    assert live["resources"][0]["tier_label"] == "silver" and live["hourly_incl_vat"] == 1451.38  # the cost after the fix, not before
    assert c.post(f"/api/runs/{rid}/cleanup", json={"confirm": "blog"}).status_code == 200
    again = c.get(f"/api/projects/{pid}").json()
    assert again["live"] is None and again["runs"][0]["cleaned_at"] is not None


def test_run_rows_have_a_structured_result_not_just_text(make):
    c, _, _ = make(api=FakeAPI(teams=[{"_id": "t1", "name": "T"}]))
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "failed")
    row = c.get("/api/runs").json()["runs"][0]
    assert row["result"]["reason_code"] == "team_required" and row["result"]["ok"] is False and row["result"]["created"] == {"apps": [], "databases": [], "database_ids": []}
    assert set(row["result"]) == {"ok", "stopped", "reason_code", "reason_params", "failed_step", "diagnosis_code", "created", "urls", "dry_run", "fixes", "heal_attempts"}
    assert row["result"]["fixes"] == [] and row["result"]["heal_attempts"] == 0


def test_limits_endpoint_and_error_params_carry_the_numbers(make):
    c, _, _ = make(max_upload_mb=1, max_files=5)
    register(c)
    lim = c.get("/api/limits").json()
    assert lim["max_upload_mb"] == 1 and lim["max_files"] == 5 and "github.com" in lim["git_hosts"] and lim["approval_timeout_seconds"] == 5
    big = io.BytesIO()
    with zipfile.ZipFile(big, "w", zipfile.ZIP_STORED) as z:
        z.writestr("blob.bin", bytes(range(256)) * 6000 * 2)
    d = c.post("/api/projects", data={"name": "b"}, files={"file": ("p.zip", big.getvalue(), "application/zip")}).json()["detail"]
    assert d["code"] == "upload_too_large" and d["params"] == {"limit_mb": 1}
    many = io.BytesIO()
    with zipfile.ZipFile(many, "w") as z:
        for i in range(9):
            z.writestr(f"f{i}.txt", "x")
    d = c.post("/api/projects", data={"name": "m"}, files={"file": ("p.zip", many.getvalue(), "application/zip")}).json()["detail"]
    assert d["code"] == "too_many_files" and d["params"] == {"limit": 5}
    d = c.post("/api/projects", data={"name": "g", "git_url": "https://evil.example/a/b"}).json()["detail"]
    assert d["code"] == "git_host" and d["params"]["host"] == "evil.example" and "github.com" in d["params"]["allowed"]


def test_built_frontend_is_served_with_a_spa_fallback_and_never_shadows_the_api(make, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside the build")
    c, _, _ = make(static_dir=dist)
    assert c.get("/").text == "<html>app</html>"
    assert c.get("/projects/12/runs/abc").text == "<html>app</html>"  # a client-side route on reload
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/health").json() == {"ok": True}  # the API still wins
    assert c.get("/api/nope").status_code == 404 and c.get("/api/nope").json()["detail"]["code"] == "not_found"
    assert c.get("/../secret.txt").text == "<html>app</html>" and "outside" not in c.get("/..%2Fsecret.txt").text


def test_no_frontend_folder_means_api_only(make):
    c, _, _ = make()
    assert c.get("/").status_code == 404 and c.get("/api/health").status_code == 200


def test_run_result_lists_the_fixes_the_agent_applied(make):
    from test_heal import TIMEOUT
    c, _, _ = make(fake=FakeLiara([TIMEOUT, READY]))
    register(c)
    connect(c)
    pid = new_project(c, "blog")
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})
    fix = wait_for(c, rid, lambda r: any(q["data"].get("kind") == "fix" for q in r["open_requests"]))["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": fix["data"]["id"], "value": True})
    result = wait_for(c, rid, lambda r: r["status"] == "succeeded")["result"]
    assert result["heal_attempts"] == 1 and [f["code"] for f in result["fixes"]] == ["resize_tier"] and result["fixes"][0]["params"]["tier_label"] == "silver"


def test_check_distinguishes_refused_forbidden_and_unreachable(make):
    c, _, _ = make()
    register(c)
    got = {tok: c.post("/api/liara/check", json={"token": tok}).json() for tok in ("invalid-token-000000", "forbidden-token-000000", "unreachable-token-0000")}
    assert [(g["code"], g["params"]) for g in got.values()] == [("liara_token_invalid", {"status": 401}), ("liara_forbidden", {"status": 403}), ("liara_unreachable", {})]


def test_team_name_is_stored_and_returned_and_cleared_when_the_team_changes(make):
    c, _, _ = make()
    register(c)
    assert c.get("/api/settings").json()["team_name"] == ""
    s = c.put("/api/settings", json={"liara_token": TOKEN, "team_id": "t1", "team_name": "My Team"}).json()
    assert s["team_id"] == "t1" and s["team_name"] == "My Team" and c.get("/api/me").json()["team_name"] == "My Team"
    assert c.put("/api/settings", json={"language": "fa"}).json()["team_name"] == "My Team"  # unrelated change keeps it
    assert c.put("/api/settings", json={"team_id": "t2"}).json()["team_name"] == ""  # a different team without a name
    assert c.put("/api/settings", json={"team_id": ""}).json()["team_name"] == ""


def test_session_endpoint_never_errors_for_anonymous_visitors(make):
    c, _, _ = make()
    assert c.get("/api/session").status_code == 200 and c.get("/api/session").json() == {"authenticated": False, "user": None}
    register(c)
    assert c.get("/api/session").json() == {"authenticated": True, "user": {"id": 1, "username": "alice"}}


def test_openapi_documents_responses_so_the_frontend_can_generate_types(make):
    c, app, _ = make()
    paths = app.openapi()["paths"]
    for path, method in [("/api/me", "get"), ("/api/projects", "get"), ("/api/projects/{pid}", "get"), ("/api/runs/{rid}", "get"), ("/api/runs/{rid}/events", "get"),
                         ("/api/liara/check", "post"), ("/api/session", "get"), ("/api/limits", "get")]:
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema, (path, method)
    comps = app.openapi()["components"]["schemas"]
    assert {"ProjectRowOut", "RunRowOut", "RunResultOut", "CheckOut", "LiveOut"} <= set(comps)


def test_csrf_guard_accepts_the_apps_own_origin_but_not_a_lookalike(make):
    c, app, _ = make()
    own = TestClient(app, base_url="https://agent.example.com")  # the deployed site talking to itself
    assert own.post("/api/auth/register", json={"username": "carol", "password": "password123"},
                    headers={"Origin": "https://agent.example.com"}).status_code == 201
    for evil in ("https://agent.example.com.evil.net", "https://evil.example", "http://agent.example.com:8443", "null"):
        assert own.post("/api/auth/logout", headers={"Origin": evil}).status_code == 403, evil


def test_limits_include_the_heal_attempt_cap(make):
    c, _, _ = make(max_heal_attempts=5)
    register(c)
    assert c.get("/api/limits").json()["max_heal_attempts"] == 5


class _Revoked(FakeAPI):
    def list_teams(self):
        raise httpx.HTTPStatusError("401", request=httpx.Request("GET", "https://api.liara.ir/v2/teams"), response=httpx.Response(401))


class _Offline(FakeAPI):
    def list_teams(self):
        raise httpx.ConnectError("no route")


@pytest.mark.parametrize("api,code,params", [(_Revoked(), "liara_token_invalid", {"status": 401}), (_Offline(), "liara_unreachable", {})])
def test_a_token_revoked_after_saving_ends_the_run_with_its_own_code(make, api, code, params):
    c, _, _ = make(api=api)
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    r = wait_for(c, rid, lambda r: r["status"] == "failed")
    assert r["result"]["reason_code"] == code and r["result"]["reason_params"] == params


def test_live_resources_carry_monthly_prices(make):
    c, _, _ = make()
    register(c)
    connect(c)
    pid, _ = deploy_and_approve(c, "blog")
    res = c.get(f"/api/projects/{pid}").json()["live"]["resources"][0]
    assert res["monthly_price"] == 825000 and res["hourly_price"] == 1145.83


def test_frontend_is_compressed_and_hashed_assets_are_cacheable_but_the_stream_is_not_compressed(make, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>" + "x" * 3000 + "</html>")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1);" * 400)
    c, _, _ = make(static_dir=dist)
    js = c.get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert js.headers["content-encoding"] == "gzip" and "immutable" in js.headers["cache-control"] and js.text.startswith("console.log")
    html = c.get("/", headers={"Accept-Encoding": "gzip"})
    assert html.headers["content-encoding"] == "gzip" and "immutable" not in html.headers.get("cache-control", "") and "no-cache" in html.headers["cache-control"]
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    stream = c.get(f"/api/runs/{rid}/stream", headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in stream.headers and stream.text.startswith("id: 1")


def test_a_finished_run_never_reports_a_status_without_its_result(make):
    """status is derived from the live log, so result must be too: the database row is written slightly later."""
    c, app, _ = make()
    register(c)
    connect(c)
    rid = c.post(f"/api/projects/{new_project(c)}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "succeeded")
    app.state.db.run("UPDATE runs SET summary=NULL WHERE id=?", (rid,))  # the moment before the row is written
    row = c.get(f"/api/runs/{rid}").json()
    assert row["status"] == "succeeded" and row["result"]["ok"] is True and row["result"]["dry_run"] is True


def test_an_invite_code_can_be_required_to_register(make):
    c, app, _ = make(registration_code="open-sesame-1")
    assert c.get("/api/limits").json()["registration_code_required"] is True
    r = c.post("/api/auth/register", json={"username": "carol", "password": "password123"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "invite_required"
    assert c.post("/api/auth/register", json={"username": "carol", "password": "password123", "invite": "wrong"}).status_code == 403
    ok = c.post("/api/auth/register", json={"username": "carol", "password": "password123", "invite": " open-sesame-1 "})
    assert ok.status_code == 201
    c2, *_ = make()      # no code configured: registration stays open, as before
    assert c2.get("/api/limits").json()["registration_code_required"] is False
    assert c2.post("/api/auth/register", json={"username": "dave", "password": "password123"}).status_code == 201


def test_a_run_can_be_stopped_while_it_waits_and_nothing_is_created(make):
    c, _, deps = make()
    register(c)
    connect(c)
    pid = new_project(c, "blog")
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "waiting")
    assert c.post(f"/api/runs/{rid}/cancel").status_code == 202
    r = wait_for(c, rid, lambda r: r["status"] == "cancelled")
    assert r["summary"]["stopped"] == "cancelled" and r["summary"]["reason_code"] == "run_cancelled" and r["summary"]["created"]["apps"] == []
    assert [m for m, p, _ in deps.fake.calls if m == "POST"] == []                      # nothing reached Liara
    assert c.post(f"/api/runs/{rid}/cancel").json()["detail"]["code"] == "run_not_active"   # already over


def test_stopping_someone_elses_run_is_not_possible(make):
    c, _, _ = make()
    register(c)
    connect(c)
    pid = new_project(c, "blog")
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] == "waiting")
    c.post("/api/auth/logout")
    register(c, "mallory")
    assert c.post(f"/api/runs/{rid}/cancel").status_code == 404
