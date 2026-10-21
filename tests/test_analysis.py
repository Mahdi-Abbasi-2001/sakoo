import json

from liara_agent.analysis import analyze, context
from liara_agent.llm.client import MockLLM

LOGS = ["Server listening on 3000", "ECONNRESET reading from upstream cache.internal:6379", "worker 3 exited with code 1", "token=abc123supersecretvalue99"]
RESULT = {"state": "down", "signals": {"http": {"status": 502, "ms": None, "error": None}, "release": {"state": "READY", "age_minutes": 400.0}, "memory": {"peak_percent": 30}, "cpu": None}, "causes": []}


def report(*hyps):
    return {"content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "report_causes", "arguments": json.dumps({"hypotheses": list(hyps)})}}]}


def test_the_model_reads_scrubbed_evidence_and_never_sees_a_secret():
    llm = MockLLM([report()])
    analyze(llm, RESULT, LOGS, "en")
    seen = json.dumps(llm.seen[0])
    assert "ECONNRESET" in seen and "abc123supersecretvalue99" not in seen and "rules_found" in seen
    assert "abc123supersecretvalue99" not in context(RESULT, LOGS)


def test_a_quote_that_is_not_in_the_log_is_dropped_and_the_cause_is_marked_ungrounded():
    llm = MockLLM([report(
        {"title": "The cache connection drops", "explanation": "ECONNRESET to the cache.", "evidence": ["ECONNRESET reading from upstream cache.internal:6379", "invented line"], "next_step": "check the cache", "confidence": "medium"},
        {"title": "A disk is full", "explanation": "guess", "evidence": ["no space left on device"], "next_step": "look at disk", "confidence": "medium"})])
    got = analyze(llm, RESULT, LOGS, "en")
    assert got[0]["grounded"] and got[0]["evidence"] == ["ECONNRESET reading from upstream cache.internal:6379"] and got[0]["confidence"] == "medium"
    assert not got[1]["grounded"] and got[1]["evidence"] == [] and got[1]["confidence"] == "low"     # no real quote: never above low


def test_at_most_three_and_nothing_when_the_model_says_nothing():
    many = [{"title": f"cause {i}", "explanation": "x", "evidence": [], "next_step": "y", "confidence": "low"} for i in range(5)]
    assert len(analyze(MockLLM([report(*many)]), RESULT, LOGS)) == 3
    assert analyze(MockLLM([{"content": "no idea", "tool_calls": None}]), RESULT, LOGS) == []
    assert analyze(MockLLM([{"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "report_causes", "arguments": "{not json"}}]}]), RESULT, LOGS) == []
