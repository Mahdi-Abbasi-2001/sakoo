from types import SimpleNamespace

import httpx
from openai import BadRequestError

from liara_agent.config import Settings
from liara_agent.llm.client import LLMError, OpenAICompatLLM


def reply():
    msg = SimpleNamespace(content="ok", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))


def refusal():
    r = httpx.Response(400, request=httpx.Request("POST", "https://x/v1/chat/completions"), json={})
    return BadRequestError("Function tools with reasoning_effort are not supported; set reasoning_effort to 'none'", response=r, body=None)


def make(settings, behaviour):
    llm = OpenAICompatLLM(settings)
    calls = []

    def create(**kw):
        calls.append(kw)
        out = behaviour(kw)
        if isinstance(out, Exception):
            raise out
        return out

    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return llm, calls


def test_a_model_that_refuses_tools_with_reasoning_is_retried_once_with_effort_none_and_remembered():
    llm, calls = make(Settings(llm_api_key="k"), lambda kw: reply() if kw.get("reasoning_effort") == "none" else refusal())
    assert llm.chat([{"role": "user", "content": "hi"}])["content"] == "ok"
    assert [("reasoning_effort" in c) for c in calls] == [False, True]
    llm.chat([{"role": "user", "content": "again"}])
    assert len(calls) == 3 and calls[2]["reasoning_effort"] == "none"  # remembered: no second failed attempt
    assert llm.usage.calls == 2 and llm.usage.prompt_tokens == 20


def test_effort_can_be_set_explicitly_and_other_errors_are_not_swallowed():
    llm, calls = make(Settings(llm_api_key="k", llm_reasoning_effort="low"), lambda kw: reply())
    llm.chat([{"role": "user", "content": "x"}])
    assert calls[0]["reasoning_effort"] == "low"
    other = BadRequestError("invalid json", response=httpx.Response(400, request=httpx.Request("POST", "https://x"), json={}), body=None)
    llm2, _ = make(Settings(llm_api_key="k"), lambda kw: other)
    try:
        llm2.chat([{"role": "user", "content": "x"}])
        raise AssertionError("should have raised")
    except LLMError as e:                      # other errors are not swallowed: they surface as the model being unusable, with only the kind of failure
        assert e.why == "HTTP 400" and "invalid json" not in str(e)
