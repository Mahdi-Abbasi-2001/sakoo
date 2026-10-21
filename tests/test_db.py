import json

import httpx
import pytest
from test_heal import READY, FakeLiara

from liara_agent.deploy import executor
from liara_agent.deploy.dbvars import substitute_db_vars
from liara_agent.deploy.executor import execute
from liara_agent.heal.diagnose import Failure, HealContext, diagnose
from liara_agent.heal.loop import HealIO
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan

CAT = snapshot_catalog()
URL = "postgres://$DB_t-db_USER:$DB_t-db_PASSWORD@$DB_t-db_HOST:$DB_t-db_PORT/$DB_t-db_NAME"


class DbFake(FakeLiara):
    """FakeLiara plus a database whose credentials can change over time, like the real service."""

    def __init__(self, *a, db_reads=None, **kw):
        super().__init__(*a, **kw)
        self.db_reads, self.pw = list(db_reads or []), "pw0"

    def db(self):
        base = {"hostname": "t-db", "type": "postgres", "internalPort": 5432, "username": "root", "status": "RUNNING", "root_password": self.pw}
        return {**base, **(self.db_reads.pop(0) if self.db_reads else {})}

    def __call__(self, req):
        if req.url.path == "/v1/databases" and req.method == "POST":
            self.calls.append((req.method, req.url.path, json.loads(req.content)))
            return httpx.Response(200, json={"databaseID": "db1"})
        if req.url.path.startswith("/v1/databases/") and req.method == "GET":
            return httpx.Response(200, json={"database": self.db()})
        if req.url.path.endswith("/logs") and getattr(self, "rotate_on_logs", None):
            self.pw = self.rotate_on_logs
        return super().__call__(req)


def run(tmp_path, fake, io=None):
    (tmp_path / "Dockerfile").write_text("FROM python\n")
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake))
    client = LiaraClient("tok", dry_run=False, journal=Journal(), http=http)
    plan = DeployPlan(apps=[AppSpec(name="t-web", platform="docker", port=3000, env={"DATABASE_URL": URL})],
                      databases=[DatabaseSpec(kind="postgres", id="t-db")])
    out = execute(plan, CAT, client, tmp_path, network_id="n1", db_poll_seconds=0, heal=io)
    return out, client


@pytest.fixture(autouse=True)
def ok_http(monkeypatch):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))


def env_sent(fake):
    return [c[2] for c in fake.calls if c[1].endswith("update-envs")]


def test_waits_until_the_credentials_have_settled_before_using_them(tmp_path):
    reads = [{"root_password": ""}, {"status": "CREATING", "root_password": "early"}, {"root_password": "early"}, {"root_password": "final"}, {"root_password": "final"}]
    fake = DbFake([READY], db_reads=reads)
    out, _ = run(tmp_path, fake)
    assert out.ok
    assert env_sent(fake)[0]["variables"][0]["value"] == "postgres://root:final@t-db:5432/postgres"  # not "early"; name defaults to postgres


def test_database_never_ready_fails_clearly(tmp_path):
    fake = DbFake([READY], db_reads=[{"root_password": ""}] * 20)
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake))
    client = LiaraClient("tok", dry_run=False, journal=Journal(), http=http)
    plan = DeployPlan(apps=[AppSpec(name="t-web", platform="docker", port=3000)], databases=[DatabaseSpec(kind="postgres", id="t-db")])
    out = execute(plan, CAT, client, tmp_path, network_id="n1", db_wait_seconds=0, db_poll_seconds=0)
    assert not out.ok and out.failed_step == "database:t-db" and "credentials" in out.reason


def test_login_failure_is_healed_by_re_reading_the_credentials_and_needs_no_approval(tmp_path, monkeypatch):
    fake = DbFake([READY], runtime=[['password authentication failed for user "root"']])
    fake.rotate_on_logs = "rotated-pw"  # the database's password changed after the app first read it
    results = iter([(False, "HTTP 500"), (True, "HTTP 200")])
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: next(results))
    asked = []
    out, client = run(tmp_path, fake, io=HealIO(confirm=lambda q, **kw: asked.append(q) or False))
    assert out.ok and asked == []  # free fix: nobody was asked to approve anything
    sent = env_sent(fake)
    assert sent[0]["variables"][0]["value"].startswith("postgres://root:pw0@") and sent[1]["variables"][0]["value"].startswith("postgres://root:rotated-pw@")
    assert len(fake.uploads) == 1  # an env change redeploys by itself: no rebuild
    assert "rotated-pw" not in json.dumps([a.body for a in client.actions]) and "pw0" not in json.dumps([a.body for a in client.actions])


@pytest.mark.parametrize("line", [
    'FATAL: password authentication failed for user "root"',
    "pymysql.err.OperationalError: (1045, \"Access denied for user 'root'@'10.0.0.5' (using password: YES)\")",
    "MongoServerError: Authentication failed.",
    "redis.exceptions.AuthenticationError: WRONGPASS invalid username-password pair",
])
def test_auth_failures_from_each_database_are_recognised(line):
    ctx = HealContext(app="a", platform="docker", port=1, plan_id="small-g2", tier="basic", cat=CAT, db_api_ids={"d": "x"})
    d = diagnose(Failure("a", "READY", runtime_lines=[line], http_detail="HTTP 500"), ctx)
    assert d.code == "db_auth_failed" and d.fixes[0].kind == "refresh_db_credentials"


def test_auth_failure_without_a_planned_database_is_not_claimed():
    ctx = HealContext(app="a", platform="docker", port=1, plan_id="small-g2", tier="basic", cat=CAT)
    assert diagnose(Failure("a", runtime_lines=["Access denied for user 'x'"]), ctx).code != "db_auth_failed"


def test_name_default_and_encoding():
    db = {"t": {"hostname": "t", "type": "postgres", "internalPort": 5432, "username": "root", "root_password": "a/b@c"}}
    assert substitute_db_vars({"U": "postgres://$DB_t_USER:$DB_t_PASSWORD@$DB_t_HOST:$DB_t_PORT/$DB_t_NAME"}, db)["U"] == "postgres://root:a%2Fb%40c@t:5432/postgres"
    assert substitute_db_vars({"N": "$DB_t_NAME"}, {"t": {**db["t"], "type": "mariadb", "dbName": "shop"}})["N"] == "shop"
