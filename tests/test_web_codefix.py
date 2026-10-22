"""The code-error choice through the web app: the card, the remembered answer, the MCP server the developer's own agent talks to, and the download of the fix."""
import json
import time

from fastapi.testclient import TestClient
from test_heal import READY, FakeLiara
from test_web import ORIGIN, connect, new_project, register, wait_for

from liara_agent.config import Settings
from liara_agent.web.app import create_app
from liara_agent.web.config import WebSettings
import test_web  # noqa: F401  (the autouse fixture that fakes the HTTP check comes with this import)
from test_web import FakeDeps, fast  # noqa: F401

CRASH = {"state": "UNHEALTHY"}
SYNTAX = ["/app/server.js:3", "const port = ;", "SyntaxError: Unexpected token ';'"]
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def make_app(tmp_path, llm=None, **ws):
    fake = FakeLiara([CRASH, READY, READY, READY], runtime=[SYNTAX])
    settings = WebSettings(data_dir=tmp_path / "data", secret_key="test-secret", request_timeout_seconds=5, static_dir=tmp_path / "no-frontend", **ws)
    deps = FakeDeps(fake, None, llm)
    return create_app(settings, deps, Settings(liara_api_token="", llm_api_key="")), fake


def rpc(c, token, method, params=None, id_=1, headers=None):
    h = {**ACCEPT, **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
    return c.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


def tool(c, token, name, /, **args):
    r = rpc(c, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    text = body["content"][0]["text"] if body.get("content") else "{}"
    return body.get("isError", False), (json.loads(text) if not body.get("isError") else text)


def start_to_choice(c):
    register(c)
    connect(c)
    pid = new_project(c)
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})   # approve the plan
    r = wait_for(c, rid, lambda r: any(q["kind"] == "question" for q in r["open_requests"]))
    return pid, rid, next(q for q in r["open_requests"] if q["kind"] == "question")


def test_a_code_error_asks_who_fixes_it_and_the_developers_agent_can_fix_it_over_mcp(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        pid, rid, q = start_to_choice(c)
        assert q["data"]["code"] == "code_fix_choice" and q["data"]["options"][:2] == ["sakoo", "own_agent"]
        assert q["data"]["params"]["location"] == "server.js:3" and q["data"]["params"]["mcp_url"].endswith("/mcp") and q["data"]["params"]["sakoo_can_fix"] is False   # no model here
        # a token for the developer's agent: shown once, only its digest is stored
        made = c.post("/api/settings/agent-tokens", json={"name": "claude code"}).json()
        token = made["token"]
        assert token.startswith("sk_sakoo_") and token not in json.dumps(c.get("/api/settings/agent-tokens").json())
        assert c.post(f"/api/runs/{rid}/respond", json={"request_id": q["data"]["id"], "value": "own_agent"}).status_code == 200
        wait_for(c, rid, lambda r: any(e["kind"] == "handoff_waiting" for e in c.get(f"/api/runs/{rid}/events").json()["events"]))

        assert rpc(c, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}).status_code == 200
        names = {t["name"] for t in rpc(c, token, "tools/list", id_=2).json()["result"]["tools"]}
        assert {"list_projects", "get_status", "get_failure", "get_logs", "get_pending", "submit_fix", "redeploy", "wait_for_run"} <= names
        assert not any(n.startswith(("delete", "remove", "set_")) for n in names)   # nothing that deletes
        err, refused = tool(c, token, "start_deploy", project=str(pid))
        assert err and "deploy permission" in refused    # a default token fixes code, it does not start or approve anything
        err, failure = tool(c, token, "get_failure", project_or_run=rid)
        assert not err and failure["waiting_for_code_fix"] and failure["category"] == "code" and failure["cause"] == "code_error" and failure["location"] == "server.js:3"
        assert any("SyntaxError" in ln for ln in failure["log_tail"]) and "submit_fix" in failure["next"]
        err, bad = tool(c, token, "submit_fix", run_id=rid, files=[{"path": "../x.js", "content": "1"}])
        assert err and "not a path inside the project" in bad          # refused at once, so the agent can correct itself
        err, ok = tool(c, token, "submit_fix", run_id=rid, files=[{"path": "server.js", "content": "const port = 3000;\n"}], message="fix the semicolon")
        assert not err and ok["accepted"]
        err, done = tool(c, token, "wait_for_run", run_id=rid, timeout_seconds=20)
        assert not err and done["status"] == "succeeded" and done["last_check"]["ok"]
        kinds = [e["kind"] for e in c.get(f"/api/runs/{rid}/events").json()["events"]]
        assert "handoff_waiting" in kinds and "external_agent_call" in kinds and "handoff_received" in kinds and "patch_ready" in kinds
        # the fix goes back to the developer: a patch to download
        patch = c.get(f"/api/projects/{pid}/fixes.patch")
        assert patch.status_code == 200 and "+const port = 3000;" in patch.text
        # the person's own file was never touched
        assert not (tmp_path / "data" / "projects" / str(pid) / "src" / "server.js").exists()
        # tokens never reach the log
        assert token not in json.dumps(c.get(f"/api/runs/{rid}/events").json())


def test_mcp_needs_a_valid_token_a_trusted_origin_and_only_shows_your_own_runs(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        pid, rid, q = start_to_choice(c)
        assert rpc(c, None, "tools/list").status_code == 401
        assert rpc(c, "sk_sakoo_notarealtoken", "tools/list").status_code == 401
        assert rpc(c, "not-even-the-prefix", "tools/list").status_code == 401
        token = c.post("/api/settings/agent-tokens", json={}).json()
        assert rpc(c, token["token"], "tools/list", headers={"Origin": "https://evil.example"}).status_code == 403
        bob = TestClient(app, headers=ORIGIN)
        register(bob, "bobby")
        bob_token = bob.post("/api/settings/agent-tokens", json={}).json()["token"]
        err, msg = tool(bob, bob_token, "get_failure", project_or_run=rid)
        assert err and "no project" in msg.lower()
        err, projects = tool(bob, bob_token, "list_projects")
        assert projects == {"projects": []}
        assert tool(c, token["token"], "submit_fix", run_id=rid, files=[{"path": "a.js", "content": "1"}])[0]   # alice, but nothing to fix yet: the run asks first
        assert c.delete(f"/api/settings/agent-tokens/{token['id']}").json() == {"ok": True}
        assert rpc(c, token["token"], "tools/list").status_code == 401                      # revoked
        c.post(f"/api/runs/{rid}/cancel")


def test_remembering_the_choice_skips_the_question_and_the_person_can_choose_sakoo_while_waiting(tmp_path):
    app, fake = make_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        pid, rid, q = start_to_choice(c)
        c.post(f"/api/runs/{rid}/respond", json={"request_id": q["data"]["id"], "value": "own_agent_remember"})
        wait_for(c, rid, lambda r: any(e["kind"] == "handoff_waiting" for e in c.get(f"/api/runs/{rid}/events").json()["events"]))
        assert c.get(f"/api/projects/{pid}").json()["code_fix_choice"] == "own_agent"
        assert c.post(f"/api/runs/{rid}/handoff/sakoo").json() == {"ok": True}        # stop waiting: Sakoo has no model here, so it reports the failure honestly
        r = wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed"))
        assert r["status"] == "failed" and r["summary"]["diagnosis"]["code"] == "code_error"
        assert c.post(f"/api/runs/{rid}/handoff/sakoo").status_code == 409            # no longer waiting
        assert c.put(f"/api/projects/{pid}/code-fix-choice", json={"choice": "sakoo"}).json() == {"code_fix_choice": "sakoo"}
        assert c.put(f"/api/projects/{pid}/code-fix-choice", json={"choice": "nonsense"}).status_code == 422
        assert c.put(f"/api/projects/{pid}/code-fix-choice", json={"choice": None}).json() == {"code_fix_choice": None}


def test_auto_fix_is_a_setting_that_is_off_by_default(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        register(c)
        assert c.get("/api/settings").json()["auto_fix_code"] is False
        assert c.put("/api/settings", json={"auto_fix_code": True}).json()["auto_fix_code"] is True
        assert c.put("/api/settings", json={"language": "fa"}).json()["auto_fix_code"] is True      # other changes keep it


def test_the_mcp_address_is_https_on_a_secure_server_and_follows_the_public_url(tmp_path):
    for ws, expect in (({"cookie_secure": True}, "https://testserver/mcp"), ({"public_url": "https://sakoo.example/"}, "https://sakoo.example/mcp"), ({}, "http://testserver/mcp")):
        app, _ = make_app(tmp_path / str(len(expect)), **ws)
        with TestClient(app, headers=ORIGIN, base_url="https://testserver" if ws.get("cookie_secure") else "http://testserver") as c:   # a secure cookie is only sent over https
            register(c)
            c.get("/api/settings")   # the server learns the address people use from a request
            assert c.get("/api/settings/agent-tokens").json()["mcp_url"] == expect


def deploy_app(tmp_path, scenario=None):
    fake = FakeLiara(scenario or [READY, READY, READY])
    settings = WebSettings(data_dir=tmp_path / "data", secret_key="test-secret", request_timeout_seconds=5, static_dir=tmp_path / "no-frontend")
    return create_app(settings, FakeDeps(fake, None, None), Settings(liara_api_token="", llm_api_key="")), fake


def test_an_agent_with_deploy_permission_goes_from_a_project_to_a_running_app_over_mcp(tmp_path):
    app, fake = deploy_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        register(c)
        connect(c)
        pid = new_project(c)
        fix_only = c.post("/api/settings/agent-tokens", json={"name": "reader"}).json()
        made = c.post("/api/settings/agent-tokens", json={"name": "deployer", "can_deploy": True}).json()
        assert fix_only["can_deploy"] is False and made["can_deploy"] is True
        assert {t["name"]: t["can_deploy"] for t in c.get("/api/settings/agent-tokens").json()["tokens"]} == {"reader": False, "deployer": True}
        token = made["token"]

        err, _ = tool(c, fix_only["token"], "start_deploy", project=str(pid))
        assert err                                                       # the reading token cannot start a run
        err, started = tool(c, token, "start_deploy", project=str(pid))
        assert not err and started["run_id"]
        rid = started["run_id"]
        err, waited = tool(c, token, "wait_for_run", run_id=rid, timeout_seconds=20)
        assert not err and waited["open_requests"] == 1 and "get_pending" in waited["next"]
        err, pending = tool(c, token, "get_pending", run_id=rid)
        assert not err and pending["plan"]["apps"] and pending["plan"]["cost"]["total_monthly_incl_vat"]
        ask = pending["requests"][0]
        assert ask["type"] == "approval" and ask["monthly_toman_incl_vat"] == pending["plan"]["cost"]["total_monthly_incl_vat"]
        # the person's reading token can look, but not answer
        err, seen = tool(c, fix_only["token"], "get_pending", run_id=rid)
        assert not err and "wait for the person" in seen["next"]
        err, _ = tool(c, fix_only["token"], "approve", run_id=rid, request_id=ask["request_id"], approve=True, expected_monthly_toman=ask["monthly_toman_incl_vat"])
        assert err
        # spending needs the price the agent actually read
        err, text = tool(c, token, "approve", run_id=rid, request_id=ask["request_id"], approve=True)
        assert err and "expected_monthly_toman" in text
        err, text = tool(c, token, "approve", run_id=rid, request_id=ask["request_id"], approve=True, expected_monthly_toman=ask["monthly_toman_incl_vat"] * 3)
        assert err and "not" in text
        err, ok = tool(c, token, "approve", run_id=rid, request_id=ask["request_id"], approve=True, expected_monthly_toman=ask["monthly_toman_incl_vat"])
        assert not err and ok["accepted"]
        err, done = tool(c, token, "wait_for_run", run_id=rid, timeout_seconds=30)
        assert not err and done["status"] == "succeeded" and done["last_check"]["ok"] and done["urls"]
        err, status = tool(c, token, "get_status", project=str(pid))
        assert not err and status["ok"] and status["urls"]
        assert token not in json.dumps(c.get(f"/api/runs/{rid}/events").json())


def test_the_agent_can_decline_and_cancel_and_a_stale_request_is_refused(tmp_path):
    app, _ = deploy_app(tmp_path)
    with TestClient(app, headers=ORIGIN) as c:
        register(c)
        connect(c)
        pid = new_project(c)
        token = c.post("/api/settings/agent-tokens", json={"name": "d", "can_deploy": True}).json()["token"]
        rid = tool(c, token, "start_deploy", project=str(pid))[1]["run_id"]
        tool(c, token, "wait_for_run", run_id=rid, timeout_seconds=20)
        ask = tool(c, token, "get_pending", run_id=rid)[1]["requests"][0]
        err, no = tool(c, token, "approve", run_id=rid, request_id=ask["request_id"], approve=False)
        assert not err and no["accepted"] is False
        err, done = tool(c, token, "wait_for_run", run_id=rid, timeout_seconds=20)
        assert done["status"] in ("failed", "cancelled")                    # declining stops the run before anything is created
        err, text = tool(c, token, "approve", run_id=rid, request_id=ask["request_id"], approve=True, expected_monthly_toman=1)
        assert err and "no longer" in text or "not waiting" in text
        err, text = tool(c, token, "start_deploy", project="nobody")
        assert err and "no project" in text
        err, text = tool(c, token, "create_project", name="x", git_url="https://not-allowed.example/x.git")
        assert err                                                          # the same source checks as the web form
        err, apps = tool(c, token, "list_ready_made_apps")
        assert not err and isinstance(apps["apps"], list)
