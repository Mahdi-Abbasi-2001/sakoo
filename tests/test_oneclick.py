from pathlib import Path

from liara_agent.liara import oneclick

FIX = Path(__file__).parent / "fixtures"


def test_catalog_snapshot_shape():
    cat = oneclick.catalog()
    assert len(cat) >= 90 and "n8n" in cat and "ghost" in cat
    assert oneclick.compose_spec("n8n")["apps"][0]["port"] == 5678
    ghost = oneclick.compose_spec("ghost")
    assert ghost["databases"][0]["type"] == "mariadb"  # compose specs reference DB credentials as $DB_<id>_HOST etc.


def test_vm_apps_have_no_spec():
    vm = [n for n, a in oneclick.catalog().items() if "VM" in a["categories"]]
    assert vm and all(oneclick.compose_spec(n) is None for n in vm)


def test_repo_url_forms():
    for url in ("https://github.com/n8n-io/n8n.git", "git@github.com:n8n-io/n8n.git", "git+https://github.com/N8N-io/n8n"):
        assert oneclick.github_slug(url) == "n8n-io/n8n"


def test_same_upstream_repo_is_high_confidence():
    (m,) = oneclick.find_matches(FIX / "ghost_fork")
    assert (m.app, m.confidence, m.is_vm) == ("ghost", 0.9, False)


def test_name_only_is_low_confidence():
    (m,) = oneclick.find_matches(FIX / "plain_n8n_name")
    assert m.app == "n8n" and m.confidence < 0.5


def test_unrelated_project_matches_nothing():
    assert oneclick.find_matches(FIX / "express_pg") == []
