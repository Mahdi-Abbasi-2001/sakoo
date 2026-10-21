from datetime import datetime, timezone

from liara_agent.liara.live import snapshot_catalog
from liara_agent.limits import KNOWN, cap_of, daily_deployments

CAT = snapshot_catalog()
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def test_the_cap_follows_the_feature_tier():
    assert cap_of(CAT, "small-g2", "basic") == 20 and cap_of(CAT, "small-g2", "standard") == 50
    assert cap_of(CAT, "small-g2", "pro") is None            # "infinite"
    assert cap_of(CAT, "nope", "basic") is None and cap_of(None, "small-g2", "basic") is None


def test_only_the_releases_of_the_last_24_hours_count():
    rows = [{"created_at": "2026-10-21T11:00:00.000Z"}, {"created_at": "2026-10-21T00:30:00.000Z"}, {"created_at": "2026-10-20T11:59:00.000Z"}, {"created_at": "nonsense"}]
    q = daily_deployments(rows, 20, NOW)
    assert q == {"used": 2, "cap": 20, "remaining": 18, "unlimited": False}
    assert daily_deployments(rows, 2, NOW)["remaining"] == 0 and daily_deployments(rows, None, NOW) == {"used": 2, "cap": None, "remaining": None, "unlimited": True}


def test_the_known_limits_are_listed_once():
    assert len(KNOWN) == len(set(KNOWN)) and "daily_deployments" in KNOWN and "networks_not_deletable" in KNOWN


# ---- the endpoint, the card's warning and the refusal, against a fake Liara -----------------------------------------------------------------------------
import json  # noqa: E402
from datetime import datetime as _dt  # noqa: E402

import httpx  # noqa: E402
from test_chat_engine import call, say  # noqa: E402
from test_heal import READY  # noqa: E402
from test_history import Releases  # noqa: E402
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: E402,F401

from liara_agent.llm.client import MockLLM  # noqa: E402


def made_today(n):
    stamp = _dt.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return [{"_id": f"r{i}", "state": "READY", "port": 3000, "projectType": "docker", "imageName": f"img{i}", "createdAt": stamp, "gitInfo": {"message": "m"}} for i in range(n, 0, -1)]


def test_the_limits_endpoint_counts_todays_releases_against_the_tier(make):
    c, app, deps = make(Releases(made_today(18), "r18", [READY]))
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    out = c.get(f"/api/projects/{pid}/limits").json()
    assert out["daily_deployments"] == {"used": 18, "cap": 20, "remaining": 2, "unlimited": False, "tier": "basic"} and "networks_not_deletable" in out["known"]


def test_the_card_warns_when_only_a_few_releases_are_left_and_a_used_up_day_is_refused_in_words(make):
    llm = MockLLM([call("deploy_edits"), say("ok")])
    c, app, deps = make(Releases(made_today(18), "r18", [READY]), llm=llm)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "release"}).json()
    card = out["messages"][-1]["actions"][0]
    assert any(w["code"] == "deploy_quota" and w["params"] == {"used": 18, "cap": 20} for w in card["warnings"])
    # the day is used up: the release is refused before Liara answers 428, and the assistant is told whose limit it is
    full = Releases(made_today(20), "r20", [READY])
    deps.fake.listed = full.listed
    done = c.post(f"/api/chat/actions/{card['id']}", json={"project_id": pid, "decision": "approve"}).json()
    failed = next(a for m in done["messages"] for a in (m.get("actions") or []) if a["id"] == card["id"])
    assert failed["status"] == "failed" and "limit of Liara's API" in json.dumps(deps.fake.listed and failed["error"])


def test_a_428_from_liara_is_its_own_outcome():
    from liara_agent.deploy.pipeline import LIARA_STATUS_CODES
    assert LIARA_STATUS_CODES[428] == "deploy_limit_reached"
