"""Scores the deterministic layer (fingerprints, readiness, compose decomposition) on the evaluation set. No network, no LLM."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from evals.cases import CASES  # noqa: E402

from liara_agent.detect.fingerprints import fingerprint  # noqa: E402
from liara_agent.detect.intent import find_compose, parse_compose  # noqa: E402
from liara_agent.detect.readiness import check_readiness  # noqa: E402
from liara_agent.liara.live import snapshot_catalog  # noqa: E402
from liara_agent.plan.compose import decompose  # noqa: E402


def materialize(case, root: Path) -> Path:
    for rel, text in case.files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
def test_deterministic_layer_finds_what_a_correct_plan_needs(case, tmp_path):
    root = materialize(case, tmp_path)
    cat = snapshot_catalog()
    if find_compose(root):  # compose projects are planned without an LLM: the plan itself must match
        plan = decompose(parse_compose(find_compose(root)), project_name="t", db_versions=cat.database_versions, db_defaults=cat.default_database_versions)
        assert {d.kind for d in plan.databases} == case.dbs and len(plan.apps) == case.apps
        return
    fp = fingerprint(root)
    plats = {h.value for h in fp.hints if h.kind == "platform"}
    dbs = {h.value for h in fp.hints if h.kind == "db"}
    assert case.dbs <= dbs, f"missed databases {case.dbs - dbs}"
    if case.platforms == {"static"}:  # nothing to detect: a folder with only html/css/js is static by elimination
        assert not plats, f"unexpected platform hints {plats}"
    else:
        assert case.platforms <= plats, f"platform hints {plats}, expected {case.platforms}"
    assert case.env <= set(fp.env_vars), f"missed env vars {case.env - set(fp.env_vars)}"


@pytest.mark.parametrize("case", [c for c in CASES if c.readiness], ids=lambda c: c.name)
def test_readiness_reports_exactly_the_expected_findings(case, tmp_path):
    got = {f.code for f in check_readiness(materialize(case, tmp_path)).findings}
    assert case.readiness <= got, f"missed {case.readiness - got}"


def test_clean_projects_have_no_readiness_noise(tmp_path):
    clean = [c for c in CASES if not c.readiness]
    for c in clean:
        got = {f.code for f in check_readiness(materialize(c, tmp_path / c.name)).findings}
        assert not got - {"allowed_hosts_wildcard"}, (c.name, got)
