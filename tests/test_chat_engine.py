import json

import pytest

from liara_agent.chat.engine import ChatBusy, ChatEngine, MemoryStore, Tool, ToolError, redact_secrets
from liara_agent.llm.client import MockLLM


def call(tool, id="c1", **args):
    return {"content": None, "tool_calls": [{"id": id, "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}


def say(text):
    return {"content": text, "tool_calls": None}


class Ctx:
    def __init__(self):
        self.ran = []


def tools():
    return [
        Tool("get_thing", "reads", {"type": "object", "properties": {}}, lambda ctx, a: {"thing": 42}),
        Tool("boom", "raises", {"type": "object", "properties": {}}, lambda ctx, a: (_ for _ in ()).throw(ToolError("no live app"))),
        Tool("set_secret", "changes", {"type": "object", "properties": {"name": {"type": "string"}}},
             lambda ctx, a: ctx.ran.append(a) or {"set": a["name"], "had_value": "_value" in a}, change=True,
             describe=lambda ctx, a: {"summary": f"set {a['name']}", "needs_value": True, "confirm_name": None}),
        Tool("wipe", "dangerous", {"type": "object", "properties": {}}, lambda ctx, a: ctx.ran.append("wiped") or {"ok": True}, change=True,
             describe=lambda ctx, a: {"summary": "delete everything", "needs_value": False, "confirm_name": "shop"}),
    ]


def engine(script):
    llm, ctx, store = MockLLM(script), Ctx(), MemoryStore()
    return ChatEngine(llm, tools(), ctx, store, "system"), llm, ctx, store


def test_a_read_tool_runs_at_once_and_the_model_sees_its_result():
    e, llm, ctx, store = engine([call("get_thing"), say("It is 42.")])
    e.send("what is the thing?")
    roles = [m["role"] for m in store.messages]
    assert roles == ["user", "assistant", "tool", "assistant"] and store.messages[-1]["content"] == "It is 42."
    assert json.loads(store.messages[2]["content"]) == {"thing": 42}
    assert llm.seen[1][-1]["role"] == "tool"          # the second model call got the tool result


def test_a_tool_error_is_told_to_the_model_in_words():
    e, llm, ctx, store = engine([call("boom"), say("There is no live app yet.")])
    e.send("status?")
    assert json.loads(store.messages[2]["content"]) == {"error": "no live app"}


def test_a_change_tool_waits_for_the_person_and_runs_only_after_approval_with_their_value():
    e, llm, ctx, store = engine([call("set_secret", name="STRIPE_KEY"), say("Done: STRIPE_KEY is set.")])
    e.send("set my stripe key")
    p = store.pending()
    assert p and p["tool"] == "set_secret" and p["info"]["needs_value"] and ctx.ran == []   # nothing ran
    assert store.messages[-1]["role"] == "assistant" and store.messages[-1].get("tool_calls")      # the model's call is open
    with pytest.raises(ChatBusy):
        e.send("hello?")                                                                       # one thing at a time
    with pytest.raises(ToolError):
        e.decide(p["id"], "approve")                                                           # a value is needed
    e.decide(p["id"], "approve", value="sk-live-abc")
    assert ctx.ran == [{"name": "STRIPE_KEY", "_value": "sk-live-abc"}]
    tool_msg = json.loads(store.messages[-2]["content"])
    assert tool_msg == {"set": "STRIPE_KEY", "had_value": True} and "sk-live" not in json.dumps(store.messages)   # the secret is never in the conversation
    assert store.messages[-1]["content"] == "Done: STRIPE_KEY is set." and store.pending() is None


def test_the_model_cannot_smuggle_a_value_or_any_private_argument():
    e, llm, ctx, store = engine([call("set_secret", name="K", _value="sk-model-made-this-up"), say("ok")])
    e.send("set K")
    e.decide(store.pending()["id"], "approve", value="real")
    assert ctx.ran == [{"name": "K", "_value": "real"}]


def test_declining_changes_nothing_and_the_model_is_told():
    e, llm, ctx, store = engine([call("wipe"), say("Okay, I left everything as it is.")])
    e.send("delete it all")
    e.decide(store.pending()["id"], "decline")
    assert ctx.ran == [] and json.loads(store.messages[-2]["content"])["declined"] is True


def test_a_dangerous_action_needs_the_name_typed():
    e, llm, ctx, store = engine([call("wipe"), say("Deleted.")])
    e.send("delete it all")
    aid = store.pending()["id"]
    with pytest.raises(ToolError):
        e.decide(aid, "approve", confirm="nope")
    assert ctx.ran == [] and store.pending()
    e.decide(aid, "approve", confirm="shop")
    assert ctx.ran == ["wiped"]


def test_only_the_first_change_in_one_reply_is_asked_the_rest_are_skipped():
    two = {"content": None, "tool_calls": [{"id": "a", "type": "function", "function": {"name": "wipe", "arguments": "{}"}},
                                           {"id": "b", "type": "function", "function": {"name": "get_thing", "arguments": "{}"}}]}
    e, llm, ctx, store = engine([two, say("done")])
    e.send("do both")
    assert [m["role"] for m in store.messages] == ["user", "assistant", "tool"]
    assert "waiting" in json.loads(store.messages[2]["content"])["skipped"]


def test_secrets_typed_into_the_chat_never_reach_the_model():
    text = "my key is sk-abcdefghijklmnop123456 and DB_PASSWORD=hunter2hunter2 and ghp_abcdefghijklmnopqrstuvwx"
    clean, hidden = redact_secrets(text)
    assert hidden and "sk-abc" not in clean and "hunter2" not in clean and "ghp_" not in clean and clean.count("[hidden]") == 3
    assert redact_secrets("why is blog-web slow since version 3?") == ("why is blog-web slow since version 3?", False)
    e, llm, ctx, store = engine([say("I hid that.")])
    assert e.send(text) is True
    assert "sk-abc" not in json.dumps(llm.seen)


def test_a_runaway_tool_loop_stops():
    e, llm, ctx, store = engine([call("get_thing", id=f"c{i}") for i in range(20)])
    e.send("loop")
    assert store.messages[-1]["role"] == "assistant" and "too many steps" in store.messages[-1]["content"] and len(llm.seen) == 8


def test_long_history_is_windowed_without_splitting_a_tool_exchange():
    e, llm, ctx, store = engine([say("ok")])
    for i in range(30):
        store.append({"role": "user", "content": f"q{i}"})
        store.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"t{i}", "type": "function", "function": {"name": "get_thing", "arguments": "{}"}}]})
        store.append({"role": "tool", "tool_call_id": f"t{i}", "content": "{}"})
    e.send("now")
    sent = llm.seen[0][1:]
    assert len(sent) <= 41 and sent[0]["role"] == "user"
