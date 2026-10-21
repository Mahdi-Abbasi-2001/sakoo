import json
from pathlib import Path

import httpx

from liara_agent.deploy.executor import execute, substitute_db_vars
from liara_agent.detect.intent import find_compose, parse_compose
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.plan.compose import decompose
from liara_agent.plan.model import AppSpec, DeployPlan, Note

FIX = Path(__file__).parent / "fixtures"
CAT = snapshot_catalog()


def client(dry_run=True):
    sent = []

    def handler(req):
        sent.append(req)
        return httpx.Response(200, json={})

    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(handler))
    return LiaraClient("tok", dry_run=dry_run, journal=Journal(), http=http), sent


def compose_plan():
    return decompose(parse_compose(find_compose(FIX / "compose_blog")), project_name="blog",
                     db_versions=CAT.database_versions, db_defaults=CAT.default_database_versions)


def test_dry_run_orders_databases_before_apps_and_sends_nothing(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "Dockerfile").write_text("FROM node\n")
    c, sent = client()
    events = []
    out = execute(compose_plan(), CAT, c, tmp_path, network_id="net1", emit=lambda k, d: events.append((k, d.get("step"))))
    assert out.ok and sent == []
    ops = [a.op for a in c.actions]
    assert ops.index("create_database") < ops.index("create_app")
    assert ops.count("create_database") == 2 and ops.count("create_app") == 2 and ops.count("create_release") == 2
    assert ("step_started", "database:blog-db") in events and ("step_finished", "app:blog-web") in events


def test_blockers_stop_before_anything_is_planned():
    plan = DeployPlan(apps=[AppSpec(name="x", platform="docker")], notes=[Note(level="blocker", message="uses privileged mode")])
    c, _ = client()
    out = execute(plan, CAT, c, ".", network_id=None)
    assert not out.ok and out.failed_step == "preflight" and c.actions == []


def test_db_variable_substitution():
    dbs = {"blog-db": {"hostname": "blog-db", "internalPort": 5432, "username": "u", "root_password": "p", "dbName": "d"}}
    env = {"URL": "postgres://$DB_blog-db_USER:$DB_blog-db_PASSWORD@$DB_blog-db_HOST:$DB_blog-db_PORT/$DB_blog-db_NAME", "X": "$DB_other_HOST"}
    out = substitute_db_vars(env, dbs)
    assert out["URL"] == "postgres://u:p@blog-db:5432/d" and out["X"] == "$DB_other_HOST"


def test_verify_http_retries_through_the_startup_502():
    from liara_agent.deploy.executor import verify_http

    codes = iter([502, 502, 200])
    sleeps = []
    ok, detail = verify_http("https://x.liara.run/", get=lambda u: httpx.Response(next(codes)), sleep=sleeps.append)
    assert ok and detail == "HTTP 200" and len(sleeps) == 2


def test_verify_http_gives_up_with_the_last_status():
    from liara_agent.deploy.executor import verify_http

    ok, detail = verify_http("https://x.liara.run/", attempts=3, get=lambda u: httpx.Response(502), sleep=lambda s: None)
    assert not ok and detail == "HTTP 502"


def test_release_body_never_contains_null_fields():
    # a real deploy was rejected with 400 when "args": null was sent
    c, sent = client(dry_run=False)
    c.journal.apps.append("a")
    c.create_release("a", "src", "docker", 3000, args=None, disks=[], image=None)
    body = json.loads(sent[0].content)
    assert body == {"sourceID": "src", "type": "docker", "port": 3000, "message": "deployed by liara-agent"}


def test_generated_credentials_are_percent_encoded_inside_urls_only():
    dbs = {"d": {"hostname": "d", "internalPort": 5432, "username": "u@x", "root_password": "p/w:@#", "dbName": "n"}}
    env = {"URL": "postgres://$DB_d_USER:$DB_d_PASSWORD@$DB_d_HOST:$DB_d_PORT/$DB_d_NAME", "PW": "$DB_d_PASSWORD"}
    out = substitute_db_vars(env, dbs)
    assert out["URL"] == "postgres://u%40x:p%2Fw%3A%40%23@d:5432/n"
    assert out["PW"] == "p/w:@#"  # a standalone variable gets the raw value
