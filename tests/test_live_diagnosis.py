from liara_agent.liara.live import snapshot_catalog
from liara_agent.live_diagnosis import Snapshot, age_minutes, rank

CAT = snapshot_catalog()


def snap(**kw) -> Snapshot:
    base = dict(app="shop", url="https://shop.liara.run/", http_status=200, http_ms=180, plan_id="small-g2", tier="basic", platform="node", port=3000, ram_gb=0.5, cpu_cores=0.5,
                mem_now=150e6, mem_max=200e6, cpu_now=0.05, cpu_max=0.1, current_release={"id": "r2", "state": "READY", "age_minutes": 600.0},
                previous_release={"id": "r1", "age_minutes": 2000.0}, cat=CAT)
    base.update(kw)
    return Snapshot(**base)


def codes(out):
    return [c["code"] for c in out["causes"]]


def test_a_healthy_app_has_no_causes():
    out = rank(snap())
    assert out["state"] == "healthy" and out["causes"] == []


def test_errors_in_the_logs_of_a_site_that_still_answers_are_flagged_gently():
    out = rank(snap(logs=["GET / 200", "Error: connect ETIMEDOUT 10.0.0.5:443"]))
    assert out["state"] == "degraded" and codes(out) == ["errors_in_logs"]


def test_a_slow_site_with_idle_resources_points_at_the_code():
    out = rank(snap(http_ms=4200))
    assert out["state"] == "degraded" and codes(out) == ["slow_response"] and out["causes"][0]["params"]["ms"] == 4200


def test_a_502_minutes_after_a_release_with_no_errors_is_a_slow_start():
    out = rank(snap(http_status=502, current_release={"id": "r2", "state": "READY", "age_minutes": 3.0}))
    assert out["state"] == "down" and out["causes"][0]["code"] == "still_starting"


def test_a_500_after_a_fresh_release_names_the_code_error_and_offers_the_rollback():
    logs = ["Server listening on 3000", "TypeError: Cannot read properties of undefined (reading 'id')", "    at /app/routes/cart.js:42:17"]
    out = rank(snap(http_status=500, logs=logs, current_release={"id": "r2", "state": "READY", "age_minutes": 25.0}))
    assert out["causes"][0]["code"] == "code_error" and "bad_release" in codes(out)
    assert any(f.get("tool") == "rollback" and f["args"] == {"release_id": "r1"} for c in out["causes"] for f in c["fixes"])
    assert any("TypeError" in e for e in out["causes"][0]["evidence"])


def test_memory_at_the_plan_limit_is_the_cause_and_the_fix_is_a_bigger_plan():
    out = rank(snap(http_status=503, mem_max=0.48 * 1024 ** 3))
    top = out["causes"][0]
    assert top["code"] == "memory_exhausted" and top["params"]["peak_percent"] >= 90
    assert top["fixes"][0]["tool"] == "resize_plan" and top["fixes"][0]["args"]["plan"].endswith("-g2")


def test_an_out_of_memory_line_is_decisive():
    out = rank(snap(http_status=502, logs=["FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory"]))
    assert codes(out)[0] == "out_of_memory" and any(f.get("tool") == "resize_plan" for f in out["causes"][0]["fixes"])


def test_a_missing_variable_in_the_logs_is_named_with_the_card_to_set_it():
    out = rank(snap(http_status=500, logs=["Error: STRIPE_SECRET_KEY is not set"]))
    top = out["causes"][0]
    assert top["code"] == "missing_env" and top["params"]["names"] == ["STRIPE_SECRET_KEY"] and top["fixes"][0]["tool"] == "set_env"


def test_a_saturated_cpu_with_a_slow_site_is_a_cause_with_a_resize():
    out = rank(snap(http_ms=3500, cpu_max=0.48))
    assert codes(out)[0] == "cpu_saturated" and "slow_response" not in codes(out)


def test_an_unhealthy_release_offers_the_last_good_one():
    out = rank(snap(http_status=502, current_release={"id": "r2", "state": "UNHEALTHY", "age_minutes": 90.0}))
    assert "release_unhealthy" in codes(out) and any(f.get("tool") == "rollback" for c in out["causes"] for f in c["fixes"])


def test_no_answer_at_all_and_no_clue_is_reported_as_not_serving():
    out = rank(snap(http_status=None, http_error="ConnectTimeout"))
    assert out["state"] == "down" and out["causes"][0]["code"] == "not_serving"
    assert out["signals"]["http"]["error"] == "ConnectTimeout"


def test_age_of_a_release_from_liaras_timestamp():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    assert round(age_minutes("2026-10-21T11:30:00.000Z", now)) == 30 and age_minutes(None) is None and age_minutes("nonsense") is None


# ---- the endpoint and the chat tool, against a fake Liara -----------------------------------------------------------------------------------
import json  # noqa: E402

import httpx  # noqa: E402
from test_chat_engine import call, say  # noqa: E402
from test_heal import READY  # noqa: E402
from test_history import Releases  # noqa: E402
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: E402,F401
from test_web_chat import LISTED  # noqa: E402

from liara_agent.llm.client import MockLLM  # noqa: E402


class Sick(Releases):
    """An app that answers 503, was killed for memory, and sits at 95% of its plan's memory."""

    def __call__(self, req):
        if "/metrics/memory" in req.url.path:
            return httpx.Response(200, json={"result": [{"applet": "a", "values": [[1, str(int(0.47 * 1024 ** 3))], [2, str(int(0.48 * 1024 ** 3))]]}]})
        if "/metrics/cpu" in req.url.path:
            return httpx.Response(200, json={"result": [{"applet": "a", "values": [[1, "0.05"]]}]})
        if req.method == "GET" and req.url.path.endswith("/logs"):
            return httpx.Response(200, json={"data": [{"metaData": {}, "values": [["1", "Server listening on 3000"], ["2", "FATAL ERROR: JavaScript heap out of memory"]]}]})
        if req.method == "GET" and req.url.path == "/v1/projects/blog-web":
            return httpx.Response(200, json={"project": {"envs": [{"key": "PORT", "value": "3000"}]}})
        return super().__call__(req)


def test_the_endpoint_and_the_chat_tool_rank_the_causes_of_a_sick_app(make, monkeypatch):
    monkeypatch.setattr("liara_agent.web.app.httpx.get", lambda url, **kw: httpx.Response(503, request=httpx.Request("GET", url)))
    llm = MockLLM([call("diagnose_app"), say("It ran out of memory.")])
    c, app, deps = make(Sick(LISTED, "r2", [READY]), llm=llm)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    out = c.get(f"/api/projects/{pid}/diagnosis").json()
    assert out["state"] == "down" and out["app"] == "blog-web" and out["signals"]["http"]["status"] == 503
    top = out["causes"][0]
    assert top["code"] == "out_of_memory" and any("heap out of memory" in e for e in top["evidence"])
    assert any(f.get("tool") == "resize_plan" for f in top["fixes"]) and out["signals"]["memory"]["peak_percent"] >= 90
    chat = c.post("/api/chat/messages", json={"project_id": pid, "text": "the site shows 503"}).json()
    assert chat["messages"][1]["looked"] == ["diagnose_app"] and chat["messages"][-1]["text"] == "It ran out of memory."
    told = next(m for m in llm.seen[1] if m["role"] == "tool")
    assert "out_of_memory" in told["content"] and "STRIPE" not in told["content"]


def test_nothing_to_diagnose_before_a_deployment(make):
    c, app, deps = make(Releases(LISTED, "r2", [READY]))
    register(c)
    connect(c)
    pid = __import__("test_web").new_project(c, "blog")
    r = c.get(f"/api/projects/{pid}/diagnosis")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_live_app"


def test_a_fix_from_the_panel_is_previewed_as_a_card_and_applied_only_on_the_call(make, monkeypatch):
    class Fake(Sick):
        resized = []

        def __call__(self, req):
            if req.method in ("PUT", "POST", "PATCH") and "resize" in req.url.path:
                self.resized.append(json.loads(req.content or b"{}"))
                return httpx.Response(200, json={})
            return super().__call__(req)
    fake = Fake(LISTED, "r2", [READY])
    c, app, deps = make(fake)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    diag = c.get(f"/api/projects/{pid}/diagnosis")
    monkeypatch.setattr("liara_agent.web.app.httpx.get", lambda url, **kw: httpx.Response(503, request=httpx.Request("GET", url)))
    fix = next(f for cz in c.get(f"/api/projects/{pid}/diagnosis").json()["causes"] for f in cz["fixes"] if f.get("tool") == "resize_plan")
    card = c.post(f"/api/projects/{pid}/fixes/preview", json={"tool": fix["tool"], "args": fix["args"]}).json()
    assert card["status"] == "pending" and card["code"] == "resize_plan" and card["price_delta_hourly"] and not fake.resized     # a preview changes nothing
    done = c.post(f"/api/projects/{pid}/fixes/apply", json={"tool": fix["tool"], "args": fix["args"]}).json()
    assert done["ok"] and done["result"]["plan"] == fix["args"]["plan"] and len(fake.resized) == 1
    assert diag.status_code == 200


def test_only_the_changes_a_diagnosis_can_name_may_be_made_from_the_panel(make):
    c, app, deps = make(Releases(LISTED, "r2", [READY]))
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    for tool in ("delete_everything", "edit_files", "start_run"):
        assert c.post(f"/api/projects/{pid}/fixes/apply", json={"tool": tool, "args": {}}).json()["detail"]["code"] == "fix_not_allowed"
    assert c.post(f"/api/projects/{pid}/fixes/apply", json={"tool": "rollback", "args": {"release_id": "nope"}}).json()["detail"]["code"] == "fix_invalid"
    bob = __import__("test_web").TestClient(app, headers=__import__("test_web").ORIGIN)
    register(bob, "bobby")
    assert bob.post(f"/api/projects/{pid}/fixes/preview", json={"tool": "rollback", "args": {}}).status_code == 404   # someone else's project


def test_the_demo_shops_500_is_diagnosed_as_the_missing_payment_key(monkeypatch):
    """The demo: the shop (demo/payments-shop) loses PAYMENT_API_KEY and answers 500; this is what its log looks like."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("shop", Path(__file__).resolve().parents[1] / "demo" / "payments-shop" / "app.py")
    shop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shop)
    monkeypatch.delenv("PAYMENT_API_KEY", raising=False)
    try:
        shop.payment_key()
        raise AssertionError("the shop must refuse to work without its key")
    except RuntimeError as e:
        reason = str(e)
    monkeypatch.setenv("PAYMENT_API_KEY", "demo-key-0000")
    assert shop.payment_key() == "demo-key-0000"
    logs = ["Payments shop listening on 0.0.0.0:3000", "Traceback (most recent call last):", "  File \"/app/app.py\", line 14, in payment_key",
            f"RuntimeError: {reason}", '10.0.0.1 "GET / HTTP/1.1" 500 -']
    out = rank(snap(http_status=500, logs=logs, env_names={"PORT"}, current_release={"id": "r2", "state": "READY", "age_minutes": 40.0}))
    top = out["causes"][0]
    assert out["state"] == "down" and top["code"] == "missing_env" and top["params"]["names"] == ["PAYMENT_API_KEY"]
    assert top["fixes"] == [{"code": "set_env", "text": "set PAYMENT_API_KEY", "params": {"names": ["PAYMENT_API_KEY"]}, "tool": "set_env", "args": {"name": "PAYMENT_API_KEY"}}]


def test_when_the_app_goes_down_the_agent_diagnoses_it_by_itself_and_the_alert_says_why(make, monkeypatch):
    from liara_agent.web import alerts, monitor
    monkeypatch.setattr(alerts, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(alerts.validate_webhook, "__defaults__", (lambda host: ["93.184.216.34"],))
    sent = []
    monkeypatch.setattr(alerts, "send", lambda url, payload, **kw: sent.append(payload) or True)
    c, app, deps = make(Sick(LISTED, "r2", [READY]))
    register(c)
    connect(c)
    assert c.put("/api/settings/alert-webhook", json={"url": "https://hooks.example/abc"}).status_code == 200
    pid, rid = deploy_and_approve(c, "blog")
    assert c.get(f"/api/projects/{pid}/diagnosis/latest").json() == {"diagnosis": None, "source": None}
    monkeypatch.setattr("liara_agent.web.app.httpx.get", lambda url, **kw: httpx.Response(503, request=httpx.Request("GET", url)))
    for _ in range(2):   # two failed checks in a row are an outage
        monitor.record(app.state.db, pid, "https://blog-web.liara.run/", lambda u: (False, 503, 5), lambda p, e, i: app.state.notify(p, e, i))
    found = c.get(f"/api/projects/{pid}/diagnosis/latest").json()["diagnosis"]
    assert found["state"] == "down" and found["causes"][0]["code"] == "out_of_memory"
    alert = sent[0]
    assert alert["event"] == "down" and alert["diagnosis"]["cause"] == "out_of_memory" and "ran out of memory" in alert["text"] and alert["diagnosis"]["evidence"]
    assert "heap out of memory" in alert["text"]


def test_a_healthy_app_that_is_running_close_to_its_limits_gets_advice_before_it_breaks():
    out = rank(snap(mem_max=0.38 * 1024 ** 3, cpu_max=0.35, http_ms=1400))
    assert out["state"] == "healthy" and out["causes"] == []
    assert [a["code"] for a in out["advice"]] == ["memory_tight", "cpu_tight", "a_little_slow"]
    assert out["advice"][0]["fixes"][0]["tool"] == "resize_plan"
    assert rank(snap())["advice"] == []


# ---- the scheduled health check and the model's second opinion --------------------------------------------------------------------------------------------
class Mystery(Sick):
    """An app that answers 502 with logs no rule recognises."""

    def __call__(self, req):
        if req.method == "GET" and req.url.path.endswith("/logs"):
            return httpx.Response(200, json={"data": [{"metaData": {}, "values": [["1", "Server listening on 3000"], ["2", "ECONNRESET reading from upstream cache.internal:6379"]]}]})
        if "/metrics/memory" in req.url.path:
            return httpx.Response(200, json={"result": [{"applet": "a", "values": [[1, str(int(0.1 * 1024 ** 3))]]}]})
        return super().__call__(req)


def hypothesis(llm_text="ECONNRESET reading from upstream cache.internal:6379"):
    args = {"hypotheses": [{"title": "The cache connection drops", "explanation": "the app loses its cache", "evidence": [llm_text, "made up line"], "next_step": "check the cache service", "confidence": "medium"}]}
    return {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "report_causes", "arguments": json.dumps(args)}}]}


def test_the_model_gives_a_second_opinion_only_when_asked_and_only_with_real_quotes(make, monkeypatch):
    monkeypatch.setattr("liara_agent.web.app.httpx.get", lambda url, **kw: httpx.Response(502, request=httpx.Request("GET", url)))
    c, app, deps = make(Mystery(LISTED, "r2", [READY]), llm=MockLLM([hypothesis()]))
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    plain = c.get(f"/api/projects/{pid}/diagnosis").json()
    assert plain["causes"][0]["code"] == "not_serving" and plain.get("ai") is None                      # the rules alone: no model call
    out = c.post(f"/api/projects/{pid}/diagnosis/analyze").json()["hypotheses"]
    assert out[0]["title"] == "The cache connection drops" and out[0]["grounded"] and out[0]["confidence"] == "medium"
    assert out[0]["evidence"] == ["ECONNRESET reading from upstream cache.internal:6379"]                # the invented line was dropped
    nomodel = make(Mystery(LISTED, "r2", [READY]))[0]
    register(nomodel, "carol")
    assert nomodel.post(f"/api/projects/1/diagnosis/analyze").status_code in (404, 503)


def test_the_scheduled_check_keeps_the_result_alerts_only_when_something_needs_attention_and_asks_the_model_when_unsure(make, monkeypatch):
    from liara_agent.web import alerts
    monkeypatch.setattr(alerts, "_resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(alerts.validate_webhook, "__defaults__", (lambda host: ["93.184.216.34"],))
    sent = []
    monkeypatch.setattr(alerts, "send", lambda url, payload, **kw: sent.append(payload) or True)
    answers = {"code": 502}
    monkeypatch.setattr("liara_agent.web.app.httpx.get", lambda url, **kw: httpx.Response(answers["code"], request=httpx.Request("GET", url)))
    c, app, deps = make(Mystery(LISTED, "r2", [READY]), llm=MockLLM([hypothesis()]))
    register(c)
    connect(c)
    assert c.put("/api/settings/alert-webhook", json={"url": "https://hooks.example/abc"}).status_code == 200
    pid, rid = deploy_and_approve(c, "blog")
    assert app.state.health_round() == 1
    latest = c.get(f"/api/projects/{pid}/diagnosis/latest").json()
    assert latest["source"] == "daily" and latest["diagnosis"]["state"] == "down" and latest["diagnosis"]["ai"][0]["title"] == "The cache connection drops"
    assert sent[0]["event"] == "report" and "daily health check" in sent[0]["text"]
    answers["code"] = 200                                                                               # healthy now, nothing near its limits: kept, but no alert
    sent.clear()
    assert app.state.health_round() == 1 and sent == []
    assert c.get(f"/api/projects/{pid}/diagnosis/latest").json()["diagnosis"]["state"] in ("healthy", "degraded")
