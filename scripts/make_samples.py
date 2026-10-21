#!/usr/bin/env python
"""Regenerate docs/samples/*.json from the real pipeline (scripted model and fake Liara, so no network or credits).
Run from the project root: .venv/bin/python scripts/make_samples.py"""
import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "src")]

import test_agent as ta  # noqa: E402
import test_db as td  # noqa: E402
import test_pipeline as tp  # noqa: E402
from test_heal import READY, TIMEOUT, FakeLiara  # noqa: E402

from liara_agent.config import Settings  # noqa: E402
from liara_agent.deploy import executor  # noqa: E402
from liara_agent.deploy.pipeline import run_deployment  # noqa: E402
from liara_agent.events import Run  # noqa: E402
from liara_agent.interact import QueueChannel, ScriptedChannel  # noqa: E402
from liara_agent.liara.live import snapshot_catalog  # noqa: E402
from liara_agent.llm.client import MockLLM  # noqa: E402

OUT = ROOT / "docs" / "samples"
cat = snapshot_catalog()
executor.verify_http = lambda url, **kw: (True, "HTTP 200")


def dump(run, name):
    (OUT / name).write_text(json.dumps([e.as_dict() for e in run.events], ensure_ascii=False, indent=1))
    print(f"{name}: {len(run.events)} events")


def compose_project():
    tmp = Path(tempfile.mkdtemp())
    shutil.copytree(ROOT / "tests/fixtures/compose_blog", tmp / "p")
    (tmp / "p" / "web").mkdir()
    (tmp / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    return tmp / "p"


# 1) a real run paused at the approval card: nothing has been created yet
p = compose_project()
run = Run()
ch = QueueChannel(run, timeout=30)
t = threading.Thread(target=lambda: run_deployment(p, "blog", cat, Settings(), tp.client_for(FakeLiara([READY]), False), ch, run,
                                                   network_id="net1", execute_=True), daemon=True)
t.start()
answered = set()
for _ in range(400):
    for e in list(run.events):  # answer agent/plan questions, but never the approval request
        if e.kind == "question" and e.data["id"] in ch.pending_ids() and e.data["id"] not in answered:
            answered.add(e.data["id"])
            ch.respond(e.data["id"], "worker")
    if any(e.kind == "confirm_request" for e in run.events):
        break
    time.sleep(0.05)
time.sleep(0.2)
dump(run, "events_waiting_for_approval.json")
pr = next(e for e in run.events if e.kind == "plan_ready").data
(OUT / "plan_with_cost.json").write_text(json.dumps(pr, ensure_ascii=False, indent=1))
ch.respond(ch.pending_ids()[0], False)
t.join(5)

# 2) dry run (CLI preview mode)
p = compose_project()
run = Run()
run_deployment(p, "blog", cat, Settings(), tp.client_for(FakeLiara([READY]), True), ScriptedChannel(run, answers=["worker"]), run,
               network_id="net1", execute_=False)
dump(run, "events_dry_run_compose.json")

# 3) a coherent single-app real run: Postgres + JWT secret, build times out, priced fix approved, success
plan = {**ta.GOOD_PLAN, "notes": [{"level": "info", "message": "ioredis is a dependency but no Redis connection was found in the code, so no Redis database was planned",
                                   "evidence": [{"file": "package.json", "line": 1, "note": "dependency ioredis"}]}]}
tmp = Path(tempfile.mkdtemp())
shutil.copytree(ROOT / "tests/fixtures/express_pg", tmp / "api")
(tmp / "api" / "Dockerfile").write_text("FROM node:22\n")
run = Run()
fake = td.DbFake([TIMEOUT, READY])
fake.pw = "generated-by-liara"
fake_client = tp.client_for(fake, False)
run_deployment(tmp / "api", "api", cat, Settings(), fake_client, ScriptedChannel(run, secrets={"JWT_SECRET": "typed-by-user-123456"}), run,
               network_id="net1", execute_=True, llm=MockLLM([tp.call("submit_plan", **plan)]))
dump(run, "events_build_timeout_then_fixed.json")
blob = (OUT / "events_build_timeout_then_fixed.json").read_text()
assert "typed-by-user-123456" not in blob and "generated-by-liara" not in blob, "a secret leaked into a sample"
