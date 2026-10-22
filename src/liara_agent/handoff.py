"""Hand a code problem to the developer's own coding agent (Claude Code, Cursor...) instead of fixing it in Sakoo.

A run that hit a code error waits here. The developer's agent connects to Sakoo's MCP server (web/mcp.py), reads the failure, and either sends the changed files
(`submit_fix`) or says it pushed the fix to the repository (`redeploy`); the run then continues with the same release -> check loop, in the same run, with nothing for
the person to copy between two tools. The person can also give up waiting and let Sakoo fix it. One Handoff belongs to one run.

Everything an outside agent sends is validated like the chat's own edits (confined paths, size limits, no secrets) before the run sees it.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from liara_agent.chat.engine import ToolError
from liara_agent.events import Run, RunCancelled
from liara_agent.web import chat_code

WAIT_SECONDS = 1800   # how long a run waits for the developer's agent before it reports the failure as it is


class HandoffError(Exception):
    """A refusal in plain words, said to the agent that called (it can correct itself and try again)."""


@dataclass
class HandoffResult:
    how: str                          # fix | redeploy | sakoo
    plan: dict | None = None          # chat_code.plan_changes() of the files the agent sent (how == "fix")
    source_dir: Path | None = None    # a fresh download of the repository (how == "redeploy")
    message: str = ""


@dataclass
class _Slot:
    id: str
    app: str
    info: dict                        # what get_failure shows the agent
    root: Path                        # the agent's copy of the project: file changes are checked against it
    event: threading.Event = field(default_factory=threading.Event)
    result: HandoffResult | None = None


class Handoff:
    def __init__(self, run: Run, *, mcp_url: str = "", timeout: float = WAIT_SECONDS, fetch_latest: Callable[[], Path] | None = None):
        self.run, self.mcp_url, self.timeout, self.fetch_latest = run, mcp_url, timeout, fetch_latest
        self._slot: _Slot | None = None
        self._lock = threading.Lock()

    # -- the run's side ---------------------------------------------------------------------------------------------------
    def wait(self, app: str, diagnosis: dict, info: dict, root: Path) -> HandoffResult | None:
        """Block until the developer's agent (or the person) answers. None: nobody did in time."""
        slot = _Slot(uuid.uuid4().hex[:8], app, {**info, "app": app, "diagnosis": diagnosis, "run_id": self.run.run_id}, root)
        with self._lock:
            self._slot = slot
        self.run.emit("handoff_waiting", {"id": slot.id, "app": app, "run_id": self.run.run_id, "mcp_url": self.mcp_url, "diagnosis": diagnosis,
                                          "expires_at": round(time.time() + self.timeout, 1)})
        waited = 0.0
        try:
            while not slot.event.wait(1.0):
                waited += 1.0
                if self.run.cancelled:
                    raise RunCancelled()
                if waited >= self.timeout:
                    self.run.emit("request_expired", {"id": slot.id, "request": "handoff"})
                    return None
            if self.run.cancelled:
                raise RunCancelled()
            return slot.result
        finally:
            with self._lock:
                self._slot = None

    # -- the other side (MCP tools, the page's button) -------------------------------------------------------------------------
    @property
    def waiting(self) -> dict | None:
        slot = self._slot
        return {"id": slot.id, **slot.info} if slot else None

    def _take(self) -> _Slot:
        slot = self._slot
        if not slot or slot.event.is_set():
            raise HandoffError("this run is not waiting for a fix (it may have finished, or timed out)")
        return slot

    def note_call(self, tool: str, ok: bool = True, detail: str = "") -> None:
        slot = self._slot
        self.run.emit("external_agent_call", {"id": slot.id if slot else "", "tool": tool, "ok": ok, **({"detail": detail[:200]} if detail else {})})

    def submit_fix(self, files: list[dict], message: str = "") -> dict:
        """The developer's agent sends changed files. They are checked now, so the agent hears about a problem at once and can send a corrected version."""
        slot = self._take()
        try:
            plan = chat_code.plan_changes(slot.root, files)
        except ToolError as e:
            raise HandoffError(str(e)) from e
        slot.result = HandoffResult("fix", plan=plan, message=message[:300])
        self.run.emit("handoff_received", {"id": slot.id, "how": "fix", "files": [f["path"] for f in plan["files"]], "message": message[:300]})
        slot.event.set()
        return {"accepted": True, "files": plan["files"], "next": "Sakoo applies the files to its copy, releases the app and checks it. Call wait_for_run to follow the result."}

    def redeploy(self) -> dict:
        """The developer's agent pushed the fix to the repository: take the newest code and release it."""
        slot = self._take()
        if not self.fetch_latest:
            raise HandoffError("this project did not come from a Git repository, so there is nothing to download: send the changed files with submit_fix")
        try:
            source = self.fetch_latest()
        except Exception as e:   # a clone problem, said without details (a message can hold a token)
            raise HandoffError(f"could not download the newest code of the repository ({getattr(e, 'code', type(e).__name__)}); is the fix pushed to the right branch?") from e
        slot.result = HandoffResult("redeploy", source_dir=source)
        self.run.emit("handoff_received", {"id": slot.id, "how": "redeploy", "files": []})
        slot.event.set()
        return {"accepted": True, "next": "Sakoo released the newest code of the repository and checks the app. Call wait_for_run to follow the result."}

    def choose_sakoo(self) -> bool:
        """The person stopped waiting: Sakoo fixes it itself. False when the run was not waiting."""
        try:
            slot = self._take()
        except HandoffError:
            return False
        slot.result = HandoffResult("sakoo")
        self.run.emit("handoff_received", {"id": slot.id, "how": "sakoo", "files": []})
        slot.event.set()
        return True


@dataclass
class CodeFixOptions:
    """What a run needs to offer "Sakoo fixes it" or "your own agent fixes it": the project's remembered choice, how to remember a new one, and the hand-off itself."""
    handoff: Handoff
    remembered: str | None = None                           # "sakoo" | "own_agent" | None
    remember: Callable[[str], None] | None = None


CHOICES = ("sakoo", "own_agent")


def make_chooser(channel, opts: CodeFixOptions, *, model_available: bool):
    """The heal loop's `choose_code_fix`: the remembered choice, or one question with two answers (and "remember this" variants of both)."""
    def choose(d, ctx) -> str:
        if opts.remembered in CHOICES:
            return opts.remembered
        can_fix = model_available or any(f.kind != "manual" for f in d.fixes)
        answer = channel.question(
            "The deploy stopped because of an error in your code. Who should fix it?", ["sakoo", "own_agent", "sakoo_remember", "own_agent_remember"],
            code="code_fix_choice", params={"diagnosis": d.code, "summary": d.summary, "evidence": d.evidence[:3], "location": d.params.get("location", ""),
                                            "sakoo_can_fix": can_fix, "mcp_url": opts.handoff.mcp_url, "app": ctx.app})
        pick = answer.removesuffix("_remember")
        if pick not in CHOICES:
            return "sakoo"   # no answer: the careful default (every change to the code is still asked about, or declined when nobody answers)
        if answer.endswith("_remember") and opts.remember:
            opts.remember(pick)
        return pick
    return choose
