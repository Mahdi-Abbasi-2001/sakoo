"""OpenAI-compatible chat client with usage tracking, plus a scripted MockLLM for tests."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from openai import APIConnectionError, APIError, APIStatusError, BadRequestError, OpenAI

from liara_agent.config import Settings


class LLMError(Exception):
    """The model could not be used: its address is unreachable, or the provider refused (a key, a quota, a country it does not serve). `why` is safe to show."""

    def __init__(self, why: str):
        super().__init__(why)
        self.why = why


def _why(e: Exception) -> str:
    status = getattr(e, "status_code", None)
    return f"HTTP {status}" if status else "unreachable"


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.calls += 1


class LLM(Protocol):
    usage: Usage

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, on_text: Callable[[str], None] | None = None) -> dict[str, Any]:
        """Return an assistant message dict: {"content": str|None, "tool_calls": [...]|None}. With `on_text`, the answer's text is also handed over piece by piece as it is written."""


class OpenAICompatLLM:
    def __init__(self, settings: Settings):
        self._client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key or "missing")
        self._model = settings.llm_model
        self._extra: dict[str, Any] = {"reasoning_effort": settings.llm_reasoning_effort} if settings.llm_reasoning_effort else {}
        self.usage = Usage()

    def reachable(self, timeout: float = 4.0) -> bool:
        """Can this server reach the model's address at all? (Liara's servers cannot reach some providers.) Asks for the model list: no tokens are used."""
        try:
            self._client.with_options(timeout=timeout, max_retries=0).models.list()
            return True
        except Exception as e:
            logging.getLogger("uvicorn.error").warning("the model's address could not be reached: %s%s", type(e).__name__, f" (HTTP {e.status_code})" if getattr(e, "status_code", None) else "")
            return False

    def _create(self, kwargs: dict[str, Any]):
        """One call. Some reasoning models refuse function tools unless reasoning is switched off, and some providers do not know `stream_options`: retry once for each and remember it."""
        for _ in range(3):
            try:
                return self._client.chat.completions.create(**kwargs, **self._extra)
            except BadRequestError as e:
                if "stream_options" in kwargs and "stream_options" in str(e):
                    kwargs = {k: v for k, v in kwargs.items() if k != "stream_options"}
                elif "reasoning_effort" in str(e) and "reasoning_effort" not in self._extra:
                    self._extra["reasoning_effort"] = "none"
                else:
                    raise
        return self._client.chat.completions.create(**kwargs, **self._extra)

    def chat(self, messages, tools=None, on_text=None):
        try:
            return self._chat(messages, tools, on_text)
        except (APIConnectionError, APIStatusError, APIError) as e:   # the message may quote the request: only the kind of failure goes on
            raise LLMError(_why(e)) from None

    def _chat(self, messages, tools=None, on_text=None):
        kwargs: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        if on_text is not None:
            return self._stream(kwargs, on_text)
        resp = self._create(kwargs)
        if resp.usage:
            self.usage.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)
        msg = resp.choices[0].message
        return {
            "content": msg.content,
            "tool_calls": [
                {"id": t.id, "type": "function", "function": {"name": t.function.name, "arguments": t.function.arguments}}
                for t in (msg.tool_calls or [])
            ]
            or None,
        }

    def _stream(self, kwargs: dict[str, Any], on_text: Callable[[str], None]) -> dict[str, Any]:
        content: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        for chunk in self._create({**kwargs, "stream": True, "stream_options": {"include_usage": True}}):
            if getattr(chunk, "usage", None):
                self.usage.add(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                content.append(delta.content)
                on_text(delta.content)
            for t in delta.tool_calls or []:   # a tool call arrives in pieces, told apart by index
                c = calls.setdefault(t.index, {"id": "", "name": "", "args": ""})
                c["id"] = t.id or c["id"]
                if t.function:
                    c["name"] += t.function.name or ""
                    c["args"] += t.function.arguments or ""
        return {"content": "".join(content) or None,
                "tool_calls": [{"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}} for _, c in sorted(calls.items())] or None}


@dataclass
class MockLLM:
    """Replays scripted assistant messages; records what it was sent. No network, no credits."""

    script: list[dict[str, Any]]
    usage: Usage = field(default_factory=Usage)
    seen: list[list[dict[str, Any]]] = field(default_factory=list)

    def reachable(self, timeout: float = 4.0) -> bool:
        return True

    def chat(self, messages, tools=None, on_text=None):
        self.seen.append(list(messages))
        self.usage.calls += 1
        reply = self.script.pop(0) if self.script else {"content": "(mock exhausted)", "tool_calls": None}
        if on_text and reply.get("content"):
            text = reply["content"]
            for i in range(0, len(text), 7):   # like a stream: a few characters at a time
                on_text(text[i:i + 7])
        return reply
