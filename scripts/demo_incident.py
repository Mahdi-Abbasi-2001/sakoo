#!/usr/bin/env python3
"""Cause (and undo) the HTTP 500 of the demo shop on a real Liara app, so the assistant has something real to find.

    python scripts/demo_incident.py status  APP      # what the site answers and the last log lines
    python scripts/demo_incident.py break   APP      # remove PAYMENT_API_KEY: the next request answers 500 (a variable lost in a console edit)
    python scripts/demo_incident.py restore APP      # put a harmless key back (to run the demo again)

Only the app you name is touched, and only the variable PAYMENT_API_KEY. The token comes from LIARA_API_TOKEN (and LIARA_TEAM_ID) in .env or the environment.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from liara_agent.config import get_settings  # noqa: E402
from liara_agent.liara.client import Journal, LiaraClient  # noqa: E402

KEY = "PAYMENT_API_KEY"
DEMO_VALUE = "demo-key-0000"


def client_for(app: str) -> LiaraClient:
    s = get_settings()
    if not s.liara_api_token:
        raise SystemExit("set LIARA_API_TOKEN (in .env or the environment)")
    j = Journal()
    j.apps = [app]   # this script is run by the owner, on the app they name
    return LiaraClient(s.liara_api_token, team_id=s.liara_team_id or "", dry_run=False, journal=j)


def probe(app: str) -> str:
    try:
        r = httpx.get(f"https://{app}.liara.run/", timeout=15, follow_redirects=True)
        return f"HTTP {r.status_code}"
    except Exception as e:
        return type(e).__name__


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("status", "break", "restore"):
        print(__doc__)
        return 2
    cmd, app = argv[1], argv[2]
    c = client_for(app)
    print(f"{app} now: {probe(app)}")
    if cmd == "status":
        for line in c.runtime_logs(app)[-8:]:
            print("  ", line[:160])
        return 0
    envs = c.app_envs(app)
    if cmd == "break":
        if KEY not in envs:
            print(f"{KEY} is not set already: nothing to remove")
            return 0
        c.merge_envs(app, {KEY: None})
    else:
        c.merge_envs(app, {KEY: DEMO_VALUE})
    print("variable changed; the app restarts. Waiting for it to answer...")
    for _ in range(30):
        time.sleep(6)
        print("  ", probe(app))
        if cmd == "restore" and probe(app) == "HTTP 200" or cmd == "break" and probe(app) == "HTTP 500":
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
