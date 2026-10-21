#!/usr/bin/env python3
"""Run the live-app diagnosis from the command line on one of your Liara apps (read-only): python scripts/diagnose_live.py APP [--json]"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from liara_agent import live_diagnosis as ld  # noqa: E402
from liara_agent.config import get_settings  # noqa: E402
from liara_agent.deploy import history  # noqa: E402
from liara_agent.liara import metrics  # noqa: E402
from liara_agent.liara.client import Journal, LiaraClient  # noqa: E402
from liara_agent.liara.live import LiveCatalog  # noqa: E402

B = "https://api.iran.liara.ir"


def diagnose(app: str) -> dict:
    s = get_settings()
    h = {"Authorization": f"Bearer {s.liara_api_token}"}
    team = {"teamID": s.liara_team_id} if s.liara_team_id else None
    j = Journal()
    j.apps = [app]
    c = LiaraClient(s.liara_api_token, team_id=s.liara_team_id or "", dry_run=False, journal=j)
    cat = LiveCatalog.from_me(httpx.get(B + "/v1/me", headers=h, timeout=30).json())
    p = httpx.get(f"{B}/v1/projects/{app}", headers=h, params=team, timeout=30).json()
    p = p.get("project", p)
    plan = cat.project_plans[p["planID"]]
    url = f"https://{app}.liara.run/"
    status, ms, err = ld.probe(httpx.get, url)
    rel = history.list_releases(c, app, Path("/nonexistent"), 12)["releases"]
    idx = next((i for i, x in enumerate(rel) if x["current"]), None)
    cur = rel[idx] if idx is not None else None
    prev = next((x for x in rel[(idx or 0) + 1:] if str(x["state"]).upper() == "READY" and x.get("rollback")), None) if idx is not None else None
    series = metrics.fetch(c, app, 6)
    mem, cpu = [x["value"] for x in series["memory"]], [x["value"] for x in series["cpu"]]
    snap = ld.Snapshot(app=app, url=url, http_status=status, http_ms=ms, http_error=err, logs=c.runtime_logs(app, (cur or {}).get("id"))[-150:], platform=(cur or {}).get("type") or "docker",
                       plan_id=p["planID"], tier=p["bundlePlanID"], port=(cur or {}).get("port"), ram_gb=plan.ram.amount, cpu_cores=plan.cpu.amount,
                       mem_now=mem[-1] if mem else None, mem_max=max(mem) if mem else None, cpu_now=cpu[-1] if cpu else None, cpu_max=max(cpu) if cpu else None,
                       current_release=None if not cur else {"id": cur["id"], "state": cur["state"], "age_minutes": ld.age_minutes(cur.get("created_at"))},
                       previous_release=None if not prev else {"id": prev["id"], "age_minutes": ld.age_minutes(prev.get("created_at"))}, env_names=set(c.app_envs(app)), cat=cat)
    return ld.rank(snap)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    out = diagnose(sys.argv[1])
    if "--json" in sys.argv:
        print(json.dumps(out, indent=1, default=str))
    else:
        sg = out["signals"]
        print(f"state: {out['state']}   http: {sg['http']}   memory: {sg['memory']}   cpu: {sg['cpu']}")
        for i, cz in enumerate(out["causes"], 1):
            print(f"{i}. {cz['code']} (score {cz['score']}) {cz['params']}")
            for e in cz["evidence"][:3]:
                print("     evidence:", e)
            for f in cz["fixes"]:
                print("     fix:", f.get("text"), f"[{f['tool']} {f['args']}]" if f.get("tool") else "")
        for a in out["advice"]:
            print("advice:", a["code"], a["params"])
