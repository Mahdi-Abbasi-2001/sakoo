import pytest

from liara_agent.liara import metrics
from liara_agent.web import alerts, monitor
from test_heal import READY
from test_history import Releases
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401
from test_web_history import LISTED


def test_a_single_failed_check_is_not_an_alert_but_two_are_and_recovery_is_too():
    t = alerts.transition
    assert t([False]) is None and t([False, True]) is None and t([True, False]) is None
    assert t([False, False]) == "down" and t([False, False, True]) == "down" and t([False, False, False]) is None
    assert t([True, False, False]) == "up" and t([True, True, False]) is None


def public(host):
    return ["93.184.216.34"]


@pytest.mark.parametrize("url,code", [("http://x.example/hook", "webhook_scheme"), ("https://u:p@x.example/h", "webhook_credentials"), ("https://nowhere.example/h", "webhook_unresolved")])
def test_webhook_addresses_are_checked(url, code):
    with pytest.raises(alerts.WebhookError) as e:
        alerts.validate_webhook(url, lambda h: [] if "nowhere" in h else public(h))
    assert e.value.code == code


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1"])
def test_a_webhook_may_not_point_inside_a_private_network(ip):
    with pytest.raises(alerts.WebhookError) as e:
        alerts.validate_webhook("https://hooks.example/x", lambda h: [ip])
    assert e.value.code == "webhook_private"
    assert alerts.send("https://hooks.example/x", {}, resolve=lambda h: [ip], post=lambda u, j: (_ for _ in ()).throw(AssertionError("must not be called"))) is False


def test_send_posts_json_and_reports_acceptance():
    sent = []
    ok = alerts.send("https://hooks.example/x", {"a": 1}, resolve=public, post=lambda u, j: (sent.append((u, j)), type("R", (), {"status_code": 200})())[1])
    assert ok and sent == [("https://hooks.example/x", {"a": 1})]


def project(c):
    return c.post("/api/projects", data={"name": "x"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]


def test_record_notifies_on_the_second_failure_and_on_recovery(make):
    c, app, _ = make()
    register(c)
    pid = project(c)
    events = []
    for ok in (True, False, False, False, True):
        monitor.record(app.state.db, pid, "https://x.liara.run", lambda u, ok=ok: (ok, 200 if ok else 502, 5), lambda p, e, i: events.append(e))
    assert events == ["down", "up"]
    assert monitor.is_down(app.state.db, pid) is False


def test_webhook_is_stored_encrypted_and_never_returned(make, monkeypatch):
    monkeypatch.setattr(alerts, "_resolve", public)
    monkeypatch.setattr(alerts.validate_webhook, "__defaults__", (public,))
    c, app, _ = make()
    register(c)
    r = c.put("/api/settings/alert-webhook", json={"url": "https://hooks.example/secret-path-123"})
    assert r.status_code == 200 and r.json()["has_alert_webhook"] is True and "secret-path" not in r.text
    assert "secret-path" not in app.state.db.one("SELECT alert_webhook_enc FROM settings")["alert_webhook_enc"]
    assert c.put("/api/settings/alert-webhook", json={"url": "http://insecure.example/x"}).json()["detail"]["code"] == "webhook_scheme"
    assert c.delete("/api/settings/alert-webhook").json()["has_alert_webhook"] is False
    assert c.post("/api/settings/alert-webhook/test").json()["detail"]["code"] == "no_webhook"


def test_metrics_are_summed_over_instances_and_thinned():
    payload = {"end": 1, "result": [{"applet": "a", "values": [[1, "0.5"], [2, "1"]]}, {"applet": "b", "values": [[1, "0.25"], [2, "bad"]]}]}
    assert metrics.parse_series(payload) == [{"ts": 1.0, "value": 0.75}, {"ts": 2.0, "value": 1.0}]
    big = [{"ts": i, "value": i} for i in range(1000)]
    t = metrics.thin(big, 100)
    assert len(t) == 100 and t[-1] == big[-1]
    assert metrics.parse_series({}) == []


def test_metrics_endpoint_reads_the_running_app(make):
    class Fake(Releases):
        def __call__(self, request):
            if "/metrics/" in request.url.path:
                self.seen = request.url.path + "?" + str(request.url.query)
                import httpx
                return httpx.Response(200, json={"end": 9, "result": [{"applet": "a", "values": [[10, "0.1"], [20, "0.2"]]}]})
            return super().__call__(request)
    fake = Fake(LISTED, "r2", [READY])
    c, app, _ = make(fake)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    out = c.get(f"/api/projects/{pid}/metrics?hours=2").json()
    assert out["hours"] == 2 and out["cpu"][-1] == {"ts": 20.0, "value": 0.2} and "since=" in fake.seen and "blog-web" in fake.seen
