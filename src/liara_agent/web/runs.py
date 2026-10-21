"""Runs the deployment pipeline in a background thread per run and keeps the live handles (event log + answer channel)."""
from __future__ import annotations

import json
import logging
import traceback
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from liara_agent.config import Settings
from liara_agent.deploy.pipeline import run_deployment
from liara_agent.events import Event, Run, RunCancelled
from liara_agent.interact import QueueChannel
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import LiaraAPI
from liara_agent.llm.client import LLM, LLMError, OpenAICompatLLM
import httpx

from liara_agent.deploy.pipeline import LIARA_STATUS_CODES
from liara_agent.web.config import WebSettings
from liara_agent.web.db import DB


class Deps(Protocol):
    """Everything a run needs from the outside world; replaced in tests."""

    def api(self, token: str, team_id: str): ...
    def client(self, token: str, team_id: str, dry_run: bool, journal: Journal) -> LiaraClient: ...
    def llm(self, settings: Settings) -> LLM | None: ...


class RealDeps:
    def api(self, token: str, team_id: str) -> LiaraAPI:
        return LiaraAPI(token, team_id=team_id)

    def client(self, token: str, team_id: str, dry_run: bool, journal: Journal) -> LiaraClient:
        return LiaraClient(token, team_id=team_id, dry_run=dry_run, journal=journal)

    def llm(self, settings: Settings) -> LLM | None:
        return OpenAICompatLLM(settings) if settings.llm_api_key else None


@dataclass
class LiveRun:
    run_id: str
    user_id: int
    run: Run
    channel: QueueChannel
    thread: threading.Thread | None = None
    journal_path: Path | None = None


def status_of(run: Run, channel: QueueChannel | None) -> str:
    """Derived from the event log, so a replayed run reports the same status as a live one."""
    last = next((e for e in reversed(run.events) if e.kind == "run_finished"), None)
    if last:
        d = last.data
        return "succeeded" if d["ok"] else ("cancelled" if d.get("stopped") == "cancelled" else "failed")
    if channel and channel.pending_ids():
        return "waiting"
    return "running"


def read_events(path: Path) -> list[Event]:
    return Run.replay(path).events if path.exists() else []


class RunManager:
    def __init__(self, settings: WebSettings, db: DB, deps: Deps, agent_settings: Settings):
        self.settings, self.db, self.deps, self.agent_settings = settings, db, deps, agent_settings
        self.live: dict[str, LiveRun] = {}
        self._lock = threading.Lock()
        # a restart kills worker threads and their pending approvals: mark such runs honestly
        db.run("UPDATE runs SET status='interrupted', finished_at=? WHERE status IN ('running','waiting')", (db.now(),))

    # -- paths ----------------------------------------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        return self.settings.data_dir / "runs" / run_id

    def log_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def active_count(self, user_id: int) -> int:
        with self._lock:
            return sum(1 for l in self.live.values() if l.user_id == user_id and l.thread and l.thread.is_alive())

    # -- start ----------------------------------------------------------------------------------------------
    def start(self, *, user_id: int, project_id: int, project_name: str, source: Path, token: str, team_id: str, language: str,
              mode: str, network: str = "") -> str:
        import uuid

        run_id = uuid.uuid4().hex[:16]
        self.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        run = Run(run_id=run_id, log_path=self.log_path(run_id))
        channel = QueueChannel(run, timeout=self.settings.request_timeout_seconds)
        live = LiveRun(run_id, user_id, run, channel, journal_path=self.run_dir(run_id) / "created.json")
        self.db.run("INSERT INTO runs(id,user_id,project_id,mode,status,created_at) VALUES (?,?,?,?,?,?)",
                    (run_id, user_id, project_id, mode, "running", self.db.now()))
        t = threading.Thread(target=self._work, args=(live, project_name, source, token, team_id, language, mode, network), daemon=True)
        live.thread = t
        with self._lock:
            self.live[run_id] = live
        t.start()
        return run_id

    def start_task(self, *, user_id: int, project_id: int, mode: str, work) -> str:
        """A run whose steps are `work(run)` (a release of new code on an existing app): the same event log, page and stop button as a deployment."""
        import uuid

        run_id = uuid.uuid4().hex[:16]
        self.run_dir(run_id).mkdir(parents=True, exist_ok=True)
        run = Run(run_id=run_id, log_path=self.log_path(run_id))
        live = LiveRun(run_id, user_id, run, QueueChannel(run, timeout=self.settings.request_timeout_seconds), journal_path=self.run_dir(run_id) / "created.json")
        self.db.run("INSERT INTO runs(id,user_id,project_id,mode,status,created_at) VALUES (?,?,?,?,?,?)", (run_id, user_id, project_id, mode, "running", self.db.now()))
        nothing = {"apps": [], "databases": [], "database_ids": [], "buckets": [], "mail_servers": []}

        def target() -> None:
            try:
                work(run)
            except RunCancelled:
                run._cancelled = False
                run.emit("run_finished", {"ok": False, "dry_run": False, "stopped": "cancelled", "reason_code": "run_cancelled", "reason": "you stopped the run", "created": nothing})
            except Exception as e:  # never leave a run hanging; the message is generic on purpose (it may contain request details)
                logging.getLogger("liara_agent.runs").error("release %s failed unexpectedly: %s\n%s", run_id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
                run.emit("run_finished", {"ok": False, "dry_run": False, "stopped": "error", "reason_code": "internal_error", "reason": f"unexpected {type(e).__name__}", "created": nothing})
            finally:
                self._finish(live)

        live.thread = threading.Thread(target=target, daemon=True)
        with self._lock:
            self.live[run_id] = live
        live.thread.start()
        return run_id

    def _work(self, live: LiveRun, project: str, source: Path, token: str, team_id: str, language: str, mode: str, network: str) -> None:
        run = live.run
        try:
            api = self.deps.api(token, team_id)
            teams = api.list_teams()
            if teams and not team_id:
                run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "error", "reason_code": "team_required",
                                          "reason": "this Liara account belongs to a team; choose it in settings",
                                          "created": {"apps": [], "databases": [], "database_ids": []}})
                return
            cat = api.live_catalog()
            nets = api.list_networks()   # the agent chooses (or makes) the project's network while it plans: see deploy/network.py
            journal = Journal.load(live.journal_path)
            execute = mode == "deploy"
            client = self.deps.client(token, team_id, not execute, journal)
            cfg = self.agent_settings.model_copy(update={"language": language})
            run_deployment(source, project, cat, cfg, client, live.channel, run, network_id=None, networks=nets, network_name=network,
                           execute_=execute, llm=self.deps.llm(cfg), confirm_plan=True, max_heal_attempts=self.settings.max_heal_attempts)
        except RunCancelled:   # the person pressed "stop": what was already created stays (the page offers to delete it), a release already building finishes on Liara
            run._cancelled = False
            j = Journal.load(live.journal_path)
            run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "cancelled", "reason_code": "run_cancelled",
                                      "reason": "you stopped the run", "created": {"apps": list(j.apps), "databases": [j.database_names.get(i, i) for i in j.databases],
                                                                                  "database_ids": list(j.databases), "buckets": list(j.buckets), "mail_servers": [m["domain"] for m in j.mail_servers]}})
        except httpx.HTTPStatusError as e:  # Liara answered with a refusal (for example a token that was revoked after it was saved)
            status = e.response.status_code
            run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "error", "reason_code": LIARA_STATUS_CODES.get(status, "liara_rejected"),
                                      "reason_params": {"status": status}, "reason": f"Liara answered HTTP {status}",
                                      "created": {"apps": [], "databases": [], "database_ids": []}})
        except LLMError as e:   # the model failed after planning began (a fix, an answer): the same plain outcome
            run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "error", "reason_code": "agent_unavailable", "reason_params": {"why": e.why},
                                      "reason": f"the AI model could not be used ({e.why})", "created": {"apps": [], "databases": [], "database_ids": []}})
        except httpx.TransportError:  # no answer from Liara at all
            run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "error", "reason_code": "liara_unreachable",
                                      "reason": "could not reach Liara", "created": {"apps": [], "databases": [], "database_ids": []}})
        except Exception as e:  # never leave a run hanging; the message is generic on purpose (it may contain request details)
            logging.getLogger("liara_agent.runs").error("run %s failed unexpectedly: %s\n%s", live.run_id, type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))  # frames only: the message may hold a token
            run.emit("run_finished", {"ok": False, "dry_run": mode != "deploy", "stopped": "error", "reason_code": "internal_error",
                                      "reason": f"unexpected {type(e).__name__}", "created": {"apps": [], "databases": [], "database_ids": []}})
        finally:
            self._finish(live)

    def cancel(self, run_id: str) -> bool:
        """Stop a run that is going. False when it is not running (finished, or the server restarted since)."""
        live = self.live.get(run_id)
        if not live or not live.thread or not live.thread.is_alive():
            return False
        live.run.cancel()
        live.channel.wake_all()
        return True

    def _finish(self, live: LiveRun) -> None:
        last = next((e for e in reversed(live.run.events) if e.kind == "run_finished"), None)
        status = status_of(live.run, None)
        summary = {k: v for k, v in (last.data if last else {}).items() if k != "would_send"}
        self.db.run("UPDATE runs SET status=?, finished_at=?, summary=? WHERE id=?", (status, self.db.now(), json.dumps(summary), live.run_id))

    # -- reading ---------------------------------------------------------------------------------------------
    def get_live(self, run_id: str) -> LiveRun | None:
        return self.live.get(run_id)

    def events(self, run_id: str) -> list[Event]:
        live = self.live.get(run_id)
        return list(live.run.events) if live else read_events(self.log_path(run_id))

    def current_status(self, run_id: str, stored: str) -> str:
        live = self.live.get(run_id)
        if live and live.thread and live.thread.is_alive():
            return status_of(live.run, live.channel)
        return stored

    def open_requests(self, run_id: str) -> list[dict]:
        """Requests without a response yet, derived from the log (so they survive a page reload and a replay)."""
        asked: dict[str, Event] = {}
        for e in self.events(run_id):
            if e.kind in ("question", "confirm_request", "secret_request"):
                asked[e.data["id"]] = e
            elif e.kind in ("answered", "confirm_response", "secret_provided", "request_expired"):
                asked.pop(e.data.get("id"), None)
        return [e.as_dict() for e in asked.values()]  # same envelope as every other event: {id, ts, kind, data}
