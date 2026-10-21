import json
from pathlib import Path

import pytest

from liara_agent.liara.live import LiveCatalog
from liara_agent.liara.pricing import choose_project_plan, database_cost, project_cost

FIX = Path(__file__).parent / "fixtures" / "liara_me_catalog.json"


@pytest.fixture(scope="module")
def cat():
    return LiveCatalog.from_me(json.loads(FIX.read_text()))


def test_matches_console_prices_for_mars_gold(cat):
    # Console review screen: 825,000 + 1,475,000 per month, +10% VAT = 2,530,000/month, ~3,514/hour.
    c = project_cost(cat, "medium-g2", "pro")
    assert round(c.monthly) == 2_300_000
    assert round(c.monthly_with_vat) == 2_530_000
    assert round(c.hourly_with_vat) == 3_514


def test_matches_console_prices_for_earth_basic(cat):
    c = project_cost(cat, "small-g2", "basic")
    assert round(c.monthly) == 750_000
    assert round(c.hourly) == 1_042  # console shows ~1,040


def test_cheapest_default_is_earth_basic(cat):
    assert choose_project_plan(cat) == ("small-g2", "basic")


def test_long_builds_force_a_higher_tier(cat):
    # basic bundles time builds out after 5 minutes; 12-minute builds need silver (15 min)
    plan, tier = choose_project_plan(cat, min_build_minutes=12)
    assert tier == "standard"
    assert cat.project_bundles[plan][tier].build_timeout >= 12


def test_memory_hungry_apps_get_bigger_plans(cat):
    plan, _ = choose_project_plan(cat, min_ram_gb=2)
    assert cat.project_plans[plan].ram.amount >= 2


def test_database_cost_and_types(cat):
    assert database_cost(cat, "small-g2", "basic").monthly > 0
    assert "postgres" in cat.database_versions and cat.default_database_versions["postgres"]


def test_database_plan_selection_respects_supported_types(cat):
    from liara_agent.liara.pricing import choose_database_plan

    assert choose_database_plan(cat, "postgres") == ("small-g2", "basic")
    assert choose_database_plan(cat, "elasticsearch")[0] == "medium-g2"  # not supported on 0.5 GB plans
    assert choose_database_plan(cat, "clickhouse")[0] == "standard-base-g2"
    _, tier = choose_database_plan(cat, "postgres", need_backups=True)
    assert tier != "basic"  # basic bundles have no backups
    with pytest.raises(LookupError):
        choose_database_plan(cat, "cassandra")


def test_mariadb_silver_mars_matches_console_review(cat):
    # Console review: 825,000 + 825,000 + 10% VAT = 1,815,000/month, ~2,521/hour.
    from liara_agent.liara.pricing import database_cost

    c = database_cost(cat, "medium-g2", "standard")
    assert round(c.monthly_with_vat) == 1_815_000
    assert round(c.hourly_with_vat) == 2_521


def test_estimate_compose_plan_costs(cat):
    from liara_agent.detect.intent import find_compose, parse_compose
    from liara_agent.liara.pricing import estimate_plan, total
    from liara_agent.plan.compose import decompose

    fix = Path(__file__).parent / "fixtures" / "compose_blog"
    p = decompose(parse_compose(find_compose(fix)), project_name="blog", db_versions=cat.database_versions,
                  db_defaults=cat.default_database_versions)
    lines = estimate_plan(cat, p)
    assert [l.kind for l in lines] == ["app", "app", "database", "database"]
    assert round(total(lines).hourly, 2) == round(sum(l.cost.hourly for l in lines), 2) > 0
