"""How a run talks to a human. The CLI and the web UI implement the same Channel; the core never prompts directly."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Protocol

from liara_agent.events import Run, RunCancelled


class Channel(Protocol):
    def confirm(self, prompt: str, *, price_delta_hourly: float | None = None, kind: str = "fix", code: str | None = None, params: dict | None = None) -> bool: ...
    def question(self, text: str, options: list[str] | None = None, *, code: str | None = None, params: dict | None = None) -> str: ...
    def secret(self, name: str) -> str | None: ...


class QueueChannel:
    """Web channel: emits a request event, blocks the worker thread until respond() is called, then emits the response.
    Timeouts decline safely: no approval, no secret, a conservative answer."""

    def __init__(self, run: Run, timeout: float = 900):
        self.run, self.timeout = run, timeout
        self._pending: dict[str, tuple[threading.Event, list]] = {}
        self._options: dict[str, list[str]] = {}

    def check(self, request_id: str, value) -> str | None:
        """None if `value` is acceptable for that request, else an error code (unknown_request / bad_option)."""
        if request_id not in self._pending:
            return "unknown_request"
        opts = self._options.get(request_id)
        if opts and value not in opts:
            return "bad_option"
        return None

    def respond(self, request_id: str, value) -> bool:
        item = self._pending.get(request_id)
        if not item or self.check(request_id, value):
            return False
        item[1].append(value)
        item[0].set()
        return True

    def _expires(self) -> float:
        """Unix seconds when an unanswered request is declined automatically (the server's clock, not the browser's)."""
        return round(time.time() + self.timeout, 1)

    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def wake_all(self) -> None:
        """The run was cancelled: let every waiting question go (the worker then sees the cancellation and stops)."""
        for ev, _ in list(self._pending.values()):
            ev.set()

    def _wait(self, rid: str, kind: str):
        ev, box = threading.Event(), []
        self._pending[rid] = (ev, box)
        try:
            if not ev.wait(self.timeout):
                self.run.emit("request_expired", {"id": rid, "request": kind})
                return None
            if self.run.cancelled:
                raise RunCancelled()
            return box[0]
        finally:
            self._pending.pop(rid, None)

    def confirm(self, prompt: str, *, price_delta_hourly: float | None = None, kind: str = "fix", code: str | None = None, params: dict | None = None) -> bool:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("confirm_request", {"id": rid, "prompt": prompt, "kind": kind, "price_delta_hourly": price_delta_hourly, "code": code, "params": params or {},
                                              "expires_at": self._expires()})
        accepted = bool(self._wait(rid, "confirm"))
        self.run.emit("confirm_response", {"id": rid, "accepted": accepted})
        return accepted

    def question(self, text: str, options: list[str] | None = None, *, code: str | None = None, params: dict | None = None) -> str:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("question", {"id": rid, "text": text, "options": options or [], "code": code, "params": params or {}, "expires_at": self._expires()})
        self._options[rid] = list(options or [])
        answer = self._wait(rid, "question")
        self._options.pop(rid, None)
        self.run.emit("answered", {"id": rid, "value": answer if answer is not None else None})
        return answer if answer is not None else "No answer was given. Choose the most conservative default and note the assumption."

    def secret(self, name: str) -> str | None:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("secret_request", {"id": rid, "name": name, "expires_at": self._expires()})
        value = self._wait(rid, "secret")
        if value:
            self.run.register_secret(value)  # from now on this value is masked everywhere in the log
        self.run.emit("secret_provided", {"id": rid, "name": name, "provided": bool(value)})  # never the value itself
        return value or None


class ScriptedChannel:
    """For tests: answers from preset values and records what was asked. Emits the same events as the web channel."""

    def __init__(self, run: Run, *, confirm=True, answers=None, secrets=None):
        self.run, self._confirm = run, confirm
        self.answers, self.secrets = list(answers or []), dict(secrets or {})

    def confirm(self, prompt, *, price_delta_hourly=None, kind="fix", code=None, params=None) -> bool:
        ok = self._confirm(prompt) if callable(self._confirm) else bool(self._confirm)
        self.run.emit("confirm_request", {"id": "s", "prompt": prompt, "kind": kind, "price_delta_hourly": price_delta_hourly, "code": code, "params": params or {}})
        self.run.emit("confirm_response", {"id": "s", "accepted": ok})
        return ok

    def question(self, text, options=None, *, code=None, params=None) -> str:
        self.run.emit("question", {"id": "s", "text": text, "options": options or [], "code": code, "params": params or {}})
        a = self.answers.pop(0) if self.answers else "use defaults"
        self.run.emit("answered", {"id": "s", "value": a})
        return a

    def secret(self, name) -> str | None:
        self.run.emit("secret_request", {"id": "s", "name": name})
        v = self.secrets.get(name)
        if v:
            self.run.register_secret(v)
        self.run.emit("secret_provided", {"id": "s", "name": name, "provided": bool(v)})
        return v


class CallbackChannel:
    """Wraps plain blocking functions (terminal prompts, tests) so they emit the same request/response events as the web channel."""

    def __init__(self, run: Run, confirm_fn, question_fn, secret_fn):
        self.run, self._confirm, self._question, self._secret = run, confirm_fn, question_fn, secret_fn

    def confirm(self, prompt, *, price_delta_hourly=None, kind="fix", code=None, params=None) -> bool:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("confirm_request", {"id": rid, "prompt": prompt, "kind": kind, "price_delta_hourly": price_delta_hourly, "code": code, "params": params or {}})
        ok = bool(self._confirm(prompt, kind))
        self.run.emit("confirm_response", {"id": rid, "accepted": ok})
        return ok

    def question(self, text, options=None, *, code=None, params=None) -> str:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("question", {"id": rid, "text": text, "options": options or [], "code": code, "params": params or {}})
        answer = self._question(text, options)
        self.run.emit("answered", {"id": rid, "value": answer})
        return answer

    def secret(self, name) -> str | None:
        rid = uuid.uuid4().hex[:8]
        self.run.emit("secret_request", {"id": rid, "name": name})
        value = self._secret(name) or None
        if value:
            self.run.register_secret(value)
        self.run.emit("secret_provided", {"id": rid, "name": name, "provided": bool(value)})
        return value
