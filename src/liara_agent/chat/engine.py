"""The conversation with the agent: a person writes, a language model answers and may use tools.

Two kinds of tool. A READ tool runs at once and its result goes back to the model. A CHANGE tool never runs on the model's say-so: the engine stops, records a
pending action (a plain-language summary, the price difference, what the person must type) and waits. Only the person's decision, made on a card in the page,
runs it, and the model is then told what happened. Values the model must never see or invent (a secret for an environment variable) arrive only with that
decision, as `_value`, and the model cannot send arguments that start with an underscore.

Nothing here knows about the web or the database: the caller gives a `Store` for one conversation, a language model, and the tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from liara_agent.events import scrub
from liara_agent.llm.client import LLM

MAX_STEPS = 8            # model calls per message: a loop of tool calls must end
MAX_TOOL_CHARS = 6000    # a tool result longer than this is cut before it goes to the model
HISTORY = 40             # messages sent to the model (all of them are kept)
MAX_TEXT = 4000


class ToolError(Exception):
    """A problem the model should hear about in plain words (no live app, unknown release...)."""


class ChatBusy(Exception):
    """A message arrived while an action waits for a decision."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[Any, dict], Any]
    change: bool = False
    # For a change tool: describe(ctx, args) -> {"summary": str, "needs_value": bool, "confirm_name": str | None, "price_delta_hourly": float | None, "warnings": [str]}
    describe: Callable[[Any, dict], dict] | None = None

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


class Store(Protocol):
    def history(self) -> list[dict]: ...
    def append(self, msg: dict) -> None: ...
    def new_action(self, tool: str, args: dict, tool_call_id: str, info: dict) -> str: ...
    def get_action(self, action_id: str) -> dict | None: ...
    def resolve_action(self, action_id: str, status: str, result: Any) -> None: ...
    def pending(self) -> dict | None: ...


# -- what a person types is hidden from the model if it looks like a secret ------------------------------------------------------
SECRET_PATTERNS = [
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bxox[abp]-[A-Za-z0-9\-]{10,}"), re.compile(r"\beyJ[A-Za-z0-9_\-]{15,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),
    re.compile(r"(?i)\b\w*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)\w*\s*[=:]\s*['\"]?[^\s'\"]{6,}"),
    re.compile(r"://[^/\s:@]+:[^@\s/]{4,}@"),
    re.compile(r"\bsk_sakoo_[A-Za-z0-9_\-]{16,}"),   # a personal access token for the developer's own coding agent (MCP)
]
LONG_TOKEN = re.compile(r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{32,}\b")


def redact_secrets(text: str) -> tuple[str, bool]:
    """(text with anything secret-looking replaced by [hidden], whether something was)."""
    out = text
    for p in SECRET_PATTERNS:
        out = p.sub("[hidden]", out)
    out = LONG_TOKEN.sub("[hidden]", out)
    return out, out != text


def _clip(value: Any) -> str:
    text = json.dumps(scrub(value), ensure_ascii=False, default=str) if not isinstance(value, str) else str(scrub(value))
    return text if len(text) <= MAX_TOOL_CHARS else text[:MAX_TOOL_CHARS] + f"… (cut: {len(text) - MAX_TOOL_CHARS} more characters)"


def _args(call: dict) -> dict:
    try:
        a = json.loads(call["function"].get("arguments") or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in a.items() if isinstance(a, dict) and not str(k).startswith("_")} if isinstance(a, dict) else {}


@dataclass
class ChatEngine:
    llm: LLM
    tools: list[Tool]
    ctx: Any
    store: Store
    system: str
    by_name: dict[str, Tool] = field(init=False)
    # Called with {"type": "step"} when the model starts a new answer, {"type": "delta", "text"} for each piece of it, {"type": "tool", "name"} when a read tool is used and
    # {"type": "card"} when a change is proposed. The page draws them live; the stored conversation is what stays.
    on_event: Callable[[dict], None] | None = None

    def __post_init__(self) -> None:
        self.by_name = {t.name: t for t in self.tools}

    # -- a message from the person -------------------------------------------------------------------------------------------
    def send(self, text: str) -> bool:
        """Answer a message. Returns whether something in it looked like a secret (it was hidden from the model)."""
        if self.store.pending():
            raise ChatBusy("an action is waiting for your decision")
        clean, hidden = redact_secrets(text.strip()[:MAX_TEXT])
        self.store.append({"role": "user", "content": clean, "_hidden": hidden})
        self._loop()
        return hidden

    # -- the person's decision about a pending action ------------------------------------------------------------------------
    def decide(self, action_id: str, decision: str, value: str | None = None, confirm: str | None = None) -> None:
        a = self.store.get_action(action_id)
        if not a or a["status"] != "pending":
            raise ToolError("that action is not waiting for a decision")
        tool = self.by_name.get(a["tool"])
        if decision != "approve" or tool is None:
            result, status = {"declined": True, "message": "The person declined. Nothing was changed."}, "declined"
        else:
            info = a.get("info") or {}
            if info.get("confirm_name") and (confirm or "").strip() != info["confirm_name"]:
                raise ToolError("the name typed does not match")
            if info.get("needs_value") and not value:
                raise ToolError("a value is needed")
            try:
                result, status = tool.run(self.ctx, {**a["args"], **({"_value": value} if value is not None else {})}), "done"
            except ToolError as e:
                result, status = {"error": str(e)}, "failed"
            except Exception as e:  # the model hears the kind of failure, not a stack trace or a message that might quote a secret
                result, status = {"error": f"the action failed ({type(e).__name__})"}, "failed"
        self.store.resolve_action(action_id, status, scrub(result))
        seen = {k: v for k, v in result.items() if k != "link"} if isinstance(result, dict) else result   # the page's own link is for the card: the model never repeats it
        self.store.append({"role": "tool", "tool_call_id": a["tool_call_id"], "content": _clip(seen)})
        self._loop()

    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:   # a page that went away must not stop the conversation from being saved
                self.on_event = None

    # -- the loop --------------------------------------------------------------------------------------------------------------
    def _window(self) -> list[dict]:
        h = [m for m in self.store.history()]
        if len(h) > HISTORY:
            h = h[-HISTORY:]
            while h and h[0]["role"] != "user":   # never start in the middle of a tool exchange
                h = h[1:]
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in h]

    def _loop(self) -> None:
        schemas = [t.schema() for t in self.tools]
        for _ in range(MAX_STEPS):
            msgs = [{"role": "system", "content": self.system}, *self._window()]
            if self.on_event:
                self._emit({"type": "step"})
                reply = self.llm.chat(msgs, schemas, on_text=lambda t: self._emit({"type": "delta", "text": t}))
            else:
                reply = self.llm.chat(msgs, schemas)
            calls = reply.get("tool_calls") or []
            self.store.append({"role": "assistant", "content": reply.get("content") or "", **({"tool_calls": calls} if calls else {})})
            if not calls:
                return
            paused = False
            for c in calls:
                tool, args = self.by_name.get(c["function"]["name"]), _args(c)
                if paused:
                    result: Any = {"skipped": "waiting for the person's decision on the previous action"}
                elif tool is None:
                    result = {"error": f"unknown tool {c['function']['name']!r}"}
                elif tool.change:
                    try:
                        info = (tool.describe or (lambda ctx, a: {"summary": tool.name}))(self.ctx, args)
                    except ToolError as e:
                        result = {"error": str(e)}
                    else:
                        self.store.new_action(tool.name, args, c["id"], info)
                        self._emit({"type": "card"})
                        paused = True
                        continue   # the tool message comes with the person's decision
                else:
                    self._emit({"type": "tool", "name": tool.name})
                    try:
                        result = tool.run(self.ctx, args)
                    except ToolError as e:
                        result = {"error": str(e)}
                    except TypeError as e:
                        result = {"error": f"bad arguments: {e}"}
                    except Exception as e:
                        result = {"error": f"the tool failed ({type(e).__name__})"}
                self.store.append({"role": "tool", "tool_call_id": c["id"], "content": _clip(result)})
            if paused:
                return
        self.store.append({"role": "assistant", "content": "I stopped here because this took too many steps. Tell me what to do next."})


# -- an in-memory store: tests, and the command line -------------------------------------------------------------------------------
class MemoryStore:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.actions: dict[str, dict] = {}

    def history(self) -> list[dict]:
        return list(self.messages)

    def append(self, msg: dict) -> None:
        self.messages.append(msg)

    def new_action(self, tool: str, args: dict, tool_call_id: str, info: dict) -> str:
        aid = f"a{len(self.actions) + 1}"
        self.actions[aid] = {"id": aid, "tool": tool, "args": args, "tool_call_id": tool_call_id, "info": info, "status": "pending", "result": None}
        return aid

    def get_action(self, action_id: str) -> dict | None:
        return self.actions.get(action_id)

    def resolve_action(self, action_id: str, status: str, result: Any) -> None:
        self.actions[action_id].update(status=status, result=result)

    def pending(self) -> dict | None:
        return next((a for a in self.actions.values() if a["status"] == "pending"), None)
