"""The run event log: the single source of truth the CLI prints and the web UI streams or replays.

Guarantee: secret values never enter the log. Every event passes through scrub() before it is stored, and any value a user typed
in as a secret is registered so that it is masked even if a build log or error message echoes it.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Every kind a run can emit. The web layer should treat unknown kinds as ignorable (forward compatible).
EVENT_KINDS = {
    # lifecycle
    "run_started", "phase_started", "phase_finished", "plan_ready", "run_finished",
    # agent investigation
    "agent_message", "tool_call", "plan_rejected", "plan_updated",
    # deploy
    "step_started", "step_finished", "resource_created", "resource_updated", "build", "build_error", "warning", "test_result",
    # self-heal
    "heal_attempt", "diagnosis", "fix_applied", "fix_declined", "fix_auto_applied", "patch_ready",
    # a code problem handed to the developer's own agent through the MCP server, and what that agent did
    "handoff_waiting", "external_agent_call", "handoff_received",
    # interaction (request -> response pairs; secret values never appear in responses)
    "question", "answered", "confirm_request", "confirm_response", "secret_request", "secret_provided", "request_expired",
}

SECRET_KEY = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API[_-]?KEY|PRIVATE|CREDENTIAL|AUTH)", re.I)
URL_CREDS = re.compile(r"(://[^/\s:@]+:)([^@\s/]+)(@)")
ASSIGN = re.compile(r"""(?ix)((?:secret|password|passwd|token|api[_-]?key|private[_-]?key)\w*\s*[=:]\s*['"]?)([A-Za-z0-9_\-./+=]{8,})""")
MASK = "***"


def scrub(obj: Any, secrets: set[str] | None = None, key: str | None = None) -> Any:
    """Return a copy of obj that is safe to store and send. $DB_x_PASSWORD style templates are references, not secrets, and are kept."""
    secrets = secrets or set()
    if isinstance(obj, dict):
        return {k: scrub(v, secrets, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v, secrets, key) for v in obj]
    if isinstance(obj, str):
        if key and SECRET_KEY.search(key) and obj and not obj.startswith("$"):
            return MASK
        out = obj
        for s in secrets:
            out = out.replace(s, MASK)
        out = URL_CREDS.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).startswith("$") else MASK) + m.group(3), out)
        return ASSIGN.sub(lambda m: m.group(1) + MASK, out)
    return obj


@dataclass
class Event:
    id: int
    ts: float
    kind: str
    data: dict

    def as_dict(self) -> dict:
        """Envelope {id, ts, kind, data}. The payload is nested because payloads legitimately contain their own `id` and `kind`."""
        return {"id": self.id, "ts": self.ts, "kind": self.kind, "data": self.data}


class RunCancelled(BaseException):
    """The person stopped the run. A BaseException on purpose: the pipeline's own `except Exception` handlers (healing, retries) must not swallow it."""


@dataclass
class Run:
    """An append-only, thread-safe event log with live subscribers and replay from any id."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    log_path: Path | None = None
    events: list[Event] = field(default_factory=list)
    _secrets: set[str] = field(default_factory=set)
    _subs: list[Callable[[Event], None]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _cancelled: bool = False

    def cancel(self) -> None:
        """Stop the run at its next event: every step and every build line is an event, so that is within moments."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def secrets(self) -> set[str]:
        with self._lock:
            return set(self._secrets)

    def register_secret(self, value: str) -> None:
        if value and len(value) >= 4:
            with self._lock:
                self._secrets.add(value)

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        self._subs.append(fn)

    def emit(self, kind: str, data: dict | None = None) -> Event:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        if self._cancelled and kind != "run_finished":
            raise RunCancelled()
        with self._lock:
            ev = Event(len(self.events) + 1, time.time(), kind, scrub(data or {}, self._secrets))
            self.events.append(ev)
            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a") as fh:
                    fh.write(json.dumps(ev.as_dict(), ensure_ascii=False) + "\n")
            subs = list(self._subs)
        for fn in subs:
            fn(ev)
        return ev

    def __call__(self, kind: str, data: dict | None = None) -> None:  # lets a Run be passed wherever an Emit callback is expected
        self.emit(kind, data)

    def since(self, last_id: int = 0) -> list[Event]:
        with self._lock:
            return [e for e in self.events if e.id > last_id]

    @classmethod
    def replay(cls, path: str | Path) -> "Run":
        run = cls(run_id=Path(path).stem)
        for line in Path(path).read_text().splitlines():
            d = json.loads(line)
            run.events.append(Event(d["id"], d["ts"], d["kind"], d["data"]))
        return run
