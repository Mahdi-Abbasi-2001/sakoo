#!/usr/bin/env python
"""Score the planner on evals/cases.py. Compose projects are decomposed without an LLM; the rest are investigated by the real agent
(needs LLM_API_KEY; costs tokens: about a dozen small runs). Usage: .venv/bin/python scripts/run_evals.py [--only name] [--no-llm]"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from evals.cases import CASES  # noqa: E402

from liara_agent.agent.loop import run_agent  # noqa: E402
from liara_agent.config import get_settings  # noqa: E402
from liara_agent.detect.intent import find_compose, parse_compose  # noqa: E402
from liara_agent.liara.live import snapshot_catalog  # noqa: E402
from liara_agent.llm.client import OpenAICompatLLM  # noqa: E402
from liara_agent.plan.compose import decompose  # noqa: E402


def materialize(case, root: Path) -> Path:
    for rel, text in case.files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def score(case, plan) -> dict:
    platforms = {a.platform for a in plan.apps}
    dbs = {d.kind for d in plan.databases}
    env = {e.name for e in plan.env} | {k for a in plan.apps for k in a.env}
    evidence_ok = all(a.evidence for a in plan.apps) and all(d.evidence for d in plan.databases)
    return {
        "platform": case.platforms <= platforms or (case.platforms == {"static"} and "static" in platforms),
        "dbs": dbs == case.dbs,
        "env_recall": round(len(case.env & env) / len(case.env), 2) if case.env else 1.0,
        "apps": len(plan.apps) == case.apps,
        "evidence": evidence_ok,
        "got": {"platforms": sorted(platforms), "dbs": sorted(dbs), "apps": len(plan.apps)},
    }


def main() -> int:
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None
    cfg = get_settings()
    use_llm = "--no-llm" not in args and bool(cfg.llm_api_key)
    cat = snapshot_catalog()
    llm = OpenAICompatLLM(cfg) if use_llm else None
    rows, started = [], time.time()
    for case in CASES:
        if only and case.name != only:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            root = materialize(case, Path(tmp))
            if find_compose(root):
                plan, mode = decompose(parse_compose(find_compose(root)), project_name="t", db_versions=cat.database_versions, db_defaults=cat.default_database_versions), "compose"
            elif llm:
                res = run_agent(llm, root, max_steps=cfg.max_agent_steps)
                plan, mode = res.plan, f"agent ({res.steps} steps)"
            else:
                rows.append({"case": case.name, "mode": "skipped (no LLM_API_KEY)"})
                continue
        rows.append({"case": case.name, "mode": mode, **(score(case, plan) if plan else {"error": "no valid plan"})})
    print(f"{'case':32} {'mode':18} platform  dbs  env  apps  evidence")
    ok = n = 0
    for r in rows:
        if "platform" not in r:
            print(f"{r['case']:32} {r['mode']:18} {r.get('error', '')}")
            continue
        n += 1
        ok += all((r["platform"], r["dbs"], r["env_recall"] == 1.0, r["apps"], r["evidence"]))
        print(f"{r['case']:32} {r['mode']:18} {'ok' if r['platform'] else 'NO':8} {'ok' if r['dbs'] else 'NO':4} {r['env_recall']:<4} {'ok' if r['apps'] else 'NO':5} {'ok' if r['evidence'] else 'NO'}   {'' if r['dbs'] and r['platform'] else r['got']}")
    if llm:
        u = llm.usage
        print(f"\n{ok}/{n} fully correct | {u.calls} LLM calls, {u.prompt_tokens} prompt + {u.completion_tokens} completion tokens | {time.time() - started:.0f}s")
        out = ROOT / "evals" / "results"
        out.mkdir(exist_ok=True)
        (out / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(rows, indent=1))
    else:
        print(f"\n{ok}/{n} compose cases fully correct; the other cases need LLM_API_KEY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
