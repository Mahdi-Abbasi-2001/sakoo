"""Uptime monitoring: a small background loop asks each running app's public address for its front page and records the answer.
Only addresses this agent put in front of its own apps are asked (the *.liara.run address of the run, and domains the agent attached);
nothing is sent to them but one GET. An answer below 500 counts as up: an app whose front page is a 404 is still running."""
from __future__ import annotations

import threading
import time
from typing import Callable

import httpx

from liara_agent.web.alerts import transition

KEEP_SECONDS = 7 * 86400
SCHEMA = "CREATE TABLE IF NOT EXISTS checks (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, url TEXT NOT NULL, ts REAL NOT NULL, ok INTEGER NOT NULL, status INTEGER, ms INTEGER);" \
         "CREATE INDEX IF NOT EXISTS checks_project ON checks(project_id, ts);"

Ping = Callable[[str], tuple[bool, int | None, int]]


def ping(url: str) -> tuple[bool, int | None, int]:
    t = time.monotonic()
    try:
        r = httpx.get(url, timeout=10, follow_redirects=True, headers={"User-Agent": "liara-launchpad-uptime"})
        return r.status_code < 500, r.status_code, round((time.monotonic() - t) * 1000)
    except httpx.HTTPError:
        return False, None, round((time.monotonic() - t) * 1000)


def record(db, project_id: int, url: str, pinger: Ping = ping, notify: Callable[[int, str, dict], None] | None = None) -> dict:
    """Ask the address, store the answer, and tell `notify(project_id, "down" | "up", info)` when the app has just gone down or come back."""
    ok, status, ms = pinger(url)
    now = time.time()
    db.run("INSERT INTO checks(project_id,url,ts,ok,status,ms) VALUES (?,?,?,?,?,?)", (project_id, url, now, int(ok), status, ms))
    db.run("DELETE FROM checks WHERE project_id=? AND ts<?", (project_id, now - KEEP_SECONDS))
    if notify:
        recent = [bool(r["ok"]) for r in db.q("SELECT ok FROM checks WHERE project_id=? AND url=? ORDER BY ts DESC, id DESC LIMIT 3", (project_id, url))]
        if event := transition(recent):
            notify(project_id, event, {"url": url, "status": status, "ts": now})
    return {"url": url, "ts": now, "ok": ok, "status": status, "ms": ms}


def is_down(db, project_id: int) -> bool:
    """The two latest checks of an address both failed (one failed check may be a blip)."""
    rows = db.q("SELECT ok FROM checks WHERE project_id=? ORDER BY ts DESC, id DESC LIMIT 2", (project_id,))
    return len(rows) == 2 and not any(r["ok"] for r in rows)


def summary(db, project_id: int, now: float | None = None) -> dict:
    """Last 24 hours: share of checks that were up, the current state, and the outages (a run of failed checks with the time it began and, if over, ended)."""
    now = now or time.time()
    rows = db.q("SELECT ts, ok, status, ms, url FROM checks WHERE project_id=? AND ts>=? ORDER BY ts", (project_id, now - 86400))
    incidents: list[dict] = []
    for r in rows:
        if not r["ok"] and (not incidents or incidents[-1]["up_at"] is not None):
            incidents.append({"down_at": r["ts"], "up_at": None})
        elif r["ok"] and incidents and incidents[-1]["up_at"] is None:
            incidents[-1]["up_at"] = r["ts"]
    ups = sum(r["ok"] for r in rows)
    return {"checks": len(rows), "up_percent": round(100 * ups / len(rows), 1) if rows else None, "current": None if not rows else bool(rows[-1]["ok"]),
            "last": None if not rows else {"ts": rows[-1]["ts"], "status": rows[-1]["status"], "ms": rows[-1]["ms"], "url": rows[-1]["url"]},
            "avg_ms": round(sum(r["ms"] for r in rows if r["ok"]) / ups) if ups else None, "incidents": incidents[-10:],
            "series": [{"ts": r["ts"], "ok": bool(r["ok"]), "ms": r["ms"]} for r in rows[-96:]]}


def start_loop(db, interval: float, targets: Callable[[], list[tuple[int, str]]], pinger: Ping = ping, notify: Callable[[int, str, dict], None] | None = None) -> threading.Event:
    """Run the checks every `interval` seconds in a daemon thread. Set the returned event to stop it."""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval):
            try:
                for pid, url in targets():
                    record(db, pid, url, pinger, notify)
            except Exception:  # monitoring must never take the server down
                pass

    threading.Thread(target=loop, name="uptime", daemon=True).start()
    return stop
