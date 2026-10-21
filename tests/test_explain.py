from liara_agent.events import Event
from liara_agent.explain import ExplainError, explain, log_tail
from liara_agent.llm.client import MockLLM
import pytest

from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401


def ev(kind, **d):
    return Event(1, 0.0, kind, d)


def test_tail_keeps_failures_and_masks_secrets():
    events = [ev("build", step="x", line="npm ERR! missing script"), ev("build_error", line="DB_PASSWORD=hunter2hunter2 failed"), ev("test_result", ok=True, name="http_200")]
    tail = log_tail(events)
    assert "missing script" in tail and "hunter2" not in tail and "http_200 passed" in tail   # a passing check says the problem ended


def test_explain_sends_only_the_scrubbed_tail_in_the_users_language():
    llm = MockLLM([{"content": "  Build broke.  ", "tool_calls": None}])
    out = explain(llm, [ev("build_error", line="mongodb://u:pa55word123@db/x refused")], "fa")
    sent = llm.seen[0]
    assert out == "Build broke." and "pa55word123" not in sent[1]["content"] and "Persian" in sent[0]["content"] and llm.seen and "tools" not in sent[0]


def test_explain_needs_a_model_and_something_to_say():
    with pytest.raises(ExplainError) as e:
        explain(None, [ev("build", line="x")])
    assert e.value.code == "llm_unavailable"
    with pytest.raises(ExplainError) as e:
        explain(MockLLM([]), [])
    assert e.value.code == "nothing_to_explain"


def test_explain_endpoint(make):
    llm = MockLLM([{"content": "It works.", "tool_calls": None}])
    c, app, deps = make(llm=llm)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    app.state.manager.get_live(rid).run.emit("build_error", {"step": "web", "line": "npm ERR! boom"})
    r = c.post(f"/api/runs/{rid}/explain")
    assert r.status_code == 200 and r.json() == {"explanation": "It works."}
    c2, *_ = make()
    register(c2, "bob")
    assert c2.post(f"/api/runs/{rid}/explain").status_code == 404


def test_report_download_lists_names_never_values(make):
    c, app, deps = make()
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    r = c.get(f"/api/runs/{rid}/report")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown") and "attachment" in r.headers["content-disposition"]
    assert r.text.startswith("# Deployment report: blog") and "## Cost" in r.text and "blog-web" in r.text
    c2, *_ = make()
    register(c2, "bob")
    assert c2.get(f"/api/runs/{rid}/report").status_code == 404


def test_the_tail_says_what_the_agent_fixed():
    tail = log_tail([Event(1, 0.0, "diagnosis", {"code": "build_timeout", "summary": "the build ran past the limit"}), Event(2, 0.0, "fix_applied", {"kind": "resize", "code": "resize_tier"}),
                     Event(3, 0.0, "resource_updated", {"kind": "app", "id": "x", "tier": "standard", "tier_label": "silver", "previous": {"tier": "basic"}})])
    assert "fix applied: resize_tier" in tail and "moved from basic to standard (silver)" in tail and "the build ran past the limit" in tail
