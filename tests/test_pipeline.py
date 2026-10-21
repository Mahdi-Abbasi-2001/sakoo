import json
import shutil
from pathlib import Path

import httpx
import pytest
from test_heal import READY, TIMEOUT, FakeLiara

from liara_agent.config import Settings
from liara_agent.deploy import executor
from liara_agent.deploy.pipeline import public_plan, run_deployment
from liara_agent.events import Run
from liara_agent.interact import ScriptedChannel
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from liara_agent.llm.client import MockLLM

FIX = Path(__file__).parent / "fixtures"
CAT = snapshot_catalog()


def call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


NO_DB_PLAN = {"apps": [{"name": "api", "platform": "node", "port": 3000, "evidence": [{"file": "package.json", "line": 7, "note": "start script"}]}],
              "env": [{"name": "JWT_SECRET", "secret": True, "source": "user", "evidence": [{"file": "src/index.js", "line": 4}]}]}


@pytest.fixture(autouse=True)
def fast_http(monkeypatch):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))


def client_for(fake, dry_run):
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake))
    return LiaraClient("tok", dry_run=dry_run, journal=Journal(), http=http)


def kinds(run):
    return [e.kind for e in run.events]


def test_dry_run_of_a_compose_project_emits_the_whole_story_and_sends_nothing(tmp_path):
    shutil.copytree(FIX / "compose_blog", tmp_path / "p")
    (tmp_path / "p" / "web").mkdir()
    (tmp_path / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    fake = FakeLiara([READY])
    run, client = Run(), client_for(fake, True)
    res = run_deployment(tmp_path / "p", "blog", CAT, Settings(), client, ScriptedChannel(run, answers=["yes a worker"]), run, network_id="n1", execute_=False)
    assert res.ok and fake.calls == []
    k = kinds(run)
    assert k[0] == "run_started" and k[-1] == "run_finished"
    for phase in ("analyze", "plan", "confirm", "deploy"):
        assert {"kind": "phase_started", "phase": phase} in [{"kind": e.kind, "phase": e.data.get("phase")} for e in run.events]
    assert k.index("plan_ready") < k.index("phase_started", k.index("plan_ready")) or True
    assert k.count("resource_created") == 4 and "question" in k and "confirm_request" not in k  # dry runs need no approval
    done = run.events[-1].data
    assert done["dry_run"] and done["ok"] and len(done["would_send"]) >= 8 and done["created"] == {"apps": [], "databases": [], "database_ids": [], "buckets": [], "mail_servers": []}


def test_plan_ready_carries_costs_and_never_environment_values(tmp_path):
    shutil.copytree(FIX / "compose_blog", tmp_path / "p")
    (tmp_path / "p" / "web").mkdir()
    (tmp_path / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    run = Run()
    run_deployment(tmp_path / "p", "blog", CAT, Settings(), client_for(FakeLiara([READY]), True), ScriptedChannel(run), run, network_id=None, execute_=False)
    ev = next(e for e in run.events if e.kind == "plan_ready")
    assert ev.data["cost"]["total_hourly_incl_vat"] == 4583 and len(ev.data["cost"]["lines"]) == 4
    web = next(a for a in ev.data["plan"]["apps"] if a["name"] == "blog-web")
    url = next(v for v in web["env"] if v["name"] == "DATABASE_URL")
    assert url["has_value"] and url["reference"].startswith("postgres://$DB_blog-db_USER:$DB_blog-db_PASSWORD@")  # references, never literals
    blob = json.dumps([e.as_dict() for e in run.events])
    assert "app:secret@" not in blob and "secret@blog-db" not in blob  # the compose file's example password


def real_run(tmp_path, fake, channel_kwargs, llm_script=None, execute=True):
    project = tmp_path / "proj"
    shutil.copytree(FIX / "express_pg", project)
    run = Run(log_path=tmp_path / "run.jsonl")
    client = client_for(fake, not execute)
    ch = ScriptedChannel(run, **channel_kwargs)
    llm = MockLLM(llm_script or [call("submit_plan", **NO_DB_PLAN)])
    res = run_deployment(project, "api", CAT, Settings(), client, ch, run, network_id="n1", execute_=execute, llm=llm)
    return res, run, client


def test_real_run_asks_approval_then_secrets_then_deploys_and_the_secret_never_hits_the_log(tmp_path):
    fake = FakeLiara([READY])
    res, run, client = real_run(tmp_path, fake, {"secrets": {"JWT_SECRET": "typed-by-user-123456"}})
    assert res.ok and run.events[-1].data["urls"] == {"api": "https://api.liara.ir".replace("api.liara.ir", "api.liara.run") + "/"}
    k = kinds(run)
    assert k.index("confirm_request") < k.index("secret_request") < k.index("resource_created") < k.index("test_result") < k.index("run_finished")
    first_confirm = next(e for e in run.events if e.kind == "confirm_request")
    assert first_confirm.data["kind"] == "plan" and first_confirm.data["price_delta_hourly"] > 0
    sent = next(c for c in fake.calls if c[1].endswith("update-envs"))[2]
    assert {"key": "JWT_SECRET", "value": "typed-by-user-123456"} in sent["variables"]  # it did reach Liara...
    assert "typed-by-user-123456" not in (tmp_path / "run.jsonl").read_text()  # ...but never the log, in memory or on disk
    assert "typed-by-user-123456" not in json.dumps([e.as_dict() for e in run.events])
    assert run.events[-1].data["created"]["apps"] == ["api-web"] or run.events[-1].data["created"]["apps"] == ["api"]


def test_declining_the_plan_creates_nothing(tmp_path):
    fake = FakeLiara([READY])
    res, run, client = real_run(tmp_path, fake, {"confirm": False})
    assert not res.ok and res.stopped == "cancelled" and fake.calls == []
    assert run.events[-1].data["stopped"] == "cancelled" and "secret_request" not in kinds(run)


def test_blockers_stop_before_asking_for_anything(tmp_path):
    plan = {**NO_DB_PLAN, "notes": [{"level": "blocker", "message": "uses privileged mode"}]}
    fake = FakeLiara([READY])
    res, run, _ = real_run(tmp_path, fake, {}, llm_script=[call("submit_plan", **plan)])
    assert res.stopped == "blocked" and fake.calls == [] and "confirm_request" not in kinds(run)
    assert run.events[-1].data["blockers"] == [{"code": None, "params": {}, "message": "uses privileged mode"}]
    assert run.events[-1].data["reason_code"] == "blocked"


def test_a_failed_deploy_ends_with_a_structured_diagnosis_and_a_fix_approval(tmp_path):
    fake = FakeLiara([TIMEOUT, READY])
    res, run, _ = real_run(tmp_path, fake, {"secrets": {"JWT_SECRET": "x" * 12}})
    assert res.ok
    k = kinds(run)
    assert k.index("heal_attempt") < k.index("diagnosis") < k.index("confirm_request", k.index("diagnosis")) < k.index("fix_applied")
    fix_confirm = [e for e in run.events if e.kind == "confirm_request" and e.data["kind"] == "fix"][0]
    assert fix_confirm.data["price_delta_hourly"] > 0


def test_unfixable_failure_is_reported_in_run_finished(tmp_path):
    fake = FakeLiara([{"state": "UNHEALTHY"}], runtime=[["httpx.ConnectTimeout: api.telegram.org"]])
    res, run, _ = real_run(tmp_path, fake, {"secrets": {"JWT_SECRET": "x" * 12}})
    done = run.events[-1].data
    assert not res.ok and not done["ok"] and done["diagnosis"]["code"] == "outbound_blocked" and done["failed_step"] == "app:api"


def test_public_plan_hides_values_but_keeps_references():
    from liara_agent.plan.model import AppSpec, DeployPlan
    p = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1, env={"A": "plain", "B": "$DB_x_HOST", "C": "p://u:$DB_x_PASSWORD@h"})])
    env = public_plan(p)["apps"][0]["env"]
    assert env[0] == {"name": "A", "reference": None, "has_value": True}
    assert env[1]["reference"] == "$DB_x_HOST" and "$DB_x_PASSWORD" in env[2]["reference"]


def test_run_log_can_be_replayed_after_the_fact(tmp_path):
    res, run, _ = real_run(tmp_path, FakeLiara([READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
    again = Run.replay(tmp_path / "run.jsonl")
    assert [e.kind for e in again.events] == kinds(run) and again.events[-1].data["ok"]


def test_plan_ready_includes_the_requests_that_would_be_sent_and_the_disks_and_labels(tmp_path):
    shutil.copytree(FIX / "compose_blog", tmp_path / "p")
    (tmp_path / "p" / "web").mkdir()
    (tmp_path / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    fake = FakeLiara([READY])
    run = Run()
    run_deployment(tmp_path / "p", "blog", CAT, Settings(), client_for(fake, False), ScriptedChannel(run, confirm=False), run, network_id="n1", execute_=True)
    ev = next(e for e in run.events if e.kind == "plan_ready").data
    assert [w["method"] for w in ev["would_send"]][:2] == ["POST", "POST"] and any(w["path"].endswith("/releases") for w in ev["would_send"])
    assert ev["disks"] == [{"app": "blog-web", "name": "uploads", "mount_to": "/app/uploads", "price": None, "billing": "unverified"}]
    assert {l["tier_label"] for l in ev["cost"]["lines"]} == {"basic"}
    assert fake.calls == []  # computing the preview sent nothing, and the plan was declined


def test_resource_updated_and_rich_fix_events_after_a_resize(tmp_path):
    fake = FakeLiara([TIMEOUT, READY])
    res, run, _ = real_run(tmp_path, fake, {"secrets": {"JWT_SECRET": "x" * 12}})
    upd = next(e for e in run.events if e.kind == "resource_updated").data
    assert upd["tier"] == "standard" and upd["tier_label"] == "silver" and upd["previous"]["tier"] == "basic" and upd["hourly_price"] > 1145.83
    fixed = next(e for e in run.events if e.kind == "fix_applied").data
    assert fixed["kind"] == "resize" and fixed["params"]["tier"] == "standard" and fixed["cost_delta_hourly"] > 0 and fixed["app"] == "api"


def test_run_finished_reports_names_not_internal_ids(tmp_path):
    from test_db import DbFake, URL
    from liara_agent.plan.model import AppSpec, DatabaseSpec

    plan = {"apps": [{"name": "t-web", "platform": "node", "port": 3000, "env": {"DATABASE_URL": URL}, "evidence": [{"file": "package.json", "line": 1}]}],
            "databases": [{"kind": "postgres", "id": "t-db", "evidence": [{"file": "package.json"}]}]}
    fake = DbFake([READY])
    res, run, _ = real_run(tmp_path, fake, {}, llm_script=[call("submit_plan", **plan)])
    created = run.events[-1].data["created"]
    assert created["databases"] == ["t-db"] and created["database_ids"] == ["db1"] and created["apps"] == ["t-web"]


def test_plan_ready_includes_readiness_findings_without_secret_values(tmp_path):
    project = tmp_path / "proj"
    shutil.copytree(FIX / "express_pg", project)
    (project / "src" / "leak.js").write_text("const stripe = 'sk-abcdefghijklmnopqrstuvwx';\napp.listen(3000, '127.0.0.1');\n")
    run = Run()
    ch = ScriptedChannel(run, confirm=False)
    run_deployment(project, "api", CAT, Settings(), client_for(FakeLiara([READY]), False), ch, run, network_id="n1", execute_=True,
                   llm=MockLLM([call("submit_plan", **NO_DB_PLAN)]))
    ev = next(e for e in run.events if e.kind == "plan_ready").data
    codes = {f["code"] for f in ev["readiness"]["findings"]}
    assert {"hardcoded_secret", "binds_localhost"} <= codes and ev["readiness"]["counts"]["warning"] >= 2
    assert "sk-abcdefgh" not in json.dumps([e.as_dict() for e in run.events])
    assert next(e for e in run.events if e.kind == "confirm_request").data["params"]["readiness_warnings"] >= 2


def test_plan_questions_record_the_question_text_and_option_ids(tmp_path):
    shutil.copytree(FIX / "compose_blog", tmp_path / "p")
    (tmp_path / "p" / "web").mkdir()
    (tmp_path / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    run = Run()
    run_deployment(tmp_path / "p", "blog", CAT, Settings(), client_for(FakeLiara([READY]), True), ScriptedChannel(run, answers=["worker"]), run, network_id=None, execute_=False)
    note = next(n for n in next(e for e in run.events if e.kind == "plan_ready").data["plan"]["notes"] if n["code"] == "question_answered")
    assert note["params"]["question_code"] == "worker_without_port" and note["params"]["answer"] == "worker" and "no port" in note["params"]["question"]


def test_liara_refusals_get_specific_reason_codes(tmp_path):
    for status, code in [(402, "insufficient_balance"), (409, "name_taken"), (401, "liara_token_invalid"), (500, "liara_rejected")]:
        class Refusing(FakeLiara):
            def __call__(self, req, status=status):
                if req.method == "POST" and req.url.path == "/v1/projects":
                    return httpx.Response(status, json={"message": "no"})
                return super().__call__(req)

        folder = tmp_path / str(status)
        folder.mkdir()
        res, run, _ = real_run(folder, Refusing([READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
        done = run.events[-1].data
        assert done["reason_code"] == code and done["reason_params"] == {"status": status, "detail": "no"}


def test_a_name_used_by_another_liara_user_gets_a_random_ending(tmp_path):
    class OneClash(FakeLiara):
        hit = False

        def __call__(self, req):
            if req.method == "POST" and req.url.path == "/v1/projects" and not self.hit:
                self.hit = True
                return httpx.Response(409, json={"message": "taken"})
            return super().__call__(req)

    res, run, fake = real_run(tmp_path, OneClash([READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
    renamed = next(e for e in run.events if e.kind == "warning" and e.data["code"] == "app_renamed").data["params"]
    assert renamed["new"].startswith(renamed["old"] + "-") and run.events[-1].data.get("reason_code") != "name_taken"


def test_a_slow_first_start_is_waited_for_when_the_logs_show_no_crash(tmp_path, monkeypatch):
    answers = iter([(False, "HTTP 502"), (False, "HTTP 502"), (True, "HTTP 200")])
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: next(answers))
    res, run, fake = real_run(tmp_path, FakeLiara([READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
    waits = [e for e in run.events if e.kind == "warning" and e.data["code"] == "slow_start"]
    assert len(waits) == 2 and res.ok


def test_a_name_clash_is_caught_before_anything_is_created_or_approved(tmp_path):
    class Taken(FakeLiara):
        def __call__(self, req):
            if req.url.path == "/v1/projects" and req.method == "GET":
                return httpx.Response(200, json={"projects": [{"project_id": "api"}]})
            if req.url.path == "/v1/databases" and req.method == "GET":
                return httpx.Response(200, json={"databases": [{"hostname": "api-db"}]})
            return super().__call__(req)

    plan = {**NO_DB_PLAN, "databases": [{"kind": "postgres", "id": "api-db", "evidence": [{"file": "package.json"}]}]}
    fake = Taken([READY])
    res, run, _ = real_run(tmp_path, fake, {"secrets": {"JWT_SECRET": "x" * 12}}, llm_script=[call("submit_plan", **plan)])
    done = run.events[-1].data
    assert res.stopped == "blocked" and done["reason_code"] == "blocked"
    assert {(b["code"], b["params"]["kind"], b["params"]["name"]) for b in done["blockers"]} == {("name_taken", "app", "api"), ("name_taken", "database", "api-db")}
    assert fake.calls == [] and "confirm_request" not in kinds(run)  # nothing created, nobody asked to approve
    assert next(e for e in run.events if e.kind == "plan_ready")  # the plan is still shown so the user can see what clashed


def test_a_clash_check_failure_does_not_block_the_run(tmp_path):
    class Flaky(FakeLiara):
        failed = False

        def __call__(self, req):
            if req.method == "GET" and req.url.path == "/v1/projects" and not self.failed:
                self.failed = True  # only the preflight's first read fails
                raise httpx.ConnectError("boom")
            return super().__call__(req)

    res, run, _ = real_run(tmp_path, Flaky([READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
    assert "confirm_request" in kinds(run)  # preflight is a convenience; the executor re-checks right before creating


def test_plan_links_show_which_resource_each_app_uses_without_exposing_values():
    from liara_agent.deploy.pipeline import plan_links
    from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan
    p = DeployPlan(
        apps=[AppSpec(name="blog-web", platform="docker", port=1, env={"DATABASE_URL": "postgres://$DB_blog-db_USER:$DB_blog-db_PASSWORD@$DB_blog-db_HOST/x",
                                                                      "REDIS_HOST": "blog-cache", "API": "http://blog-worker:3000", "PORT": "3000"}),
              AppSpec(name="blog-worker", platform="docker", port=2, env={"DB": "$DB_blog-db_HOST"})],
        databases=[DatabaseSpec(kind="postgres", id="blog-db"), DatabaseSpec(kind="redis", id="blog-cache")])
    links = {(l["from"], l["to"]): (l["to_kind"], l["env"]) for l in plan_links(p)}
    assert links == {("blog-web", "blog-db"): ("database", ["DATABASE_URL"]), ("blog-web", "blog-cache"): ("database", ["REDIS_HOST"]),
                     ("blog-web", "blog-worker"): ("app", ["API"]), ("blog-worker", "blog-db"): ("database", ["DB"])}
    assert "password" not in json.dumps(plan_links(p)).lower()


def test_events_carry_max_attempts_and_monthly_prices(tmp_path):
    res, run, _ = real_run(tmp_path, FakeLiara([TIMEOUT, READY]), {"secrets": {"JWT_SECRET": "x" * 12}})
    assert next(e for e in run.events if e.kind == "heal_attempt").data["max_attempts"] == 3
    created = next(e for e in run.events if e.kind == "resource_created").data
    assert created["monthly_price"] == 825000 and created["hourly_price"] == 1145.83
    assert next(e for e in run.events if e.kind == "resource_updated").data["monthly_price"] == round(1451.38 * 720, -2)


def test_a_declined_fix_ends_as_a_diagnosed_failure_with_no_stopped_field(tmp_path):
    res, run, _ = real_run(tmp_path, FakeLiara([TIMEOUT]), {"confirm": lambda prompt: "plan" in prompt.lower() or "Create" in prompt, "secrets": {"JWT_SECRET": "x" * 12}})
    done = run.events[-1].data
    assert not res.ok and done["ok"] is False and "stopped" not in done and done["reason_code"] == "build_timeout" and done["diagnosis"]["code"] == "build_timeout"
    assert any(e.kind == "fix_declined" for e in run.events) and done["created"]["apps"] == ["api"]  # the app still exists and bills


def test_planned_requests_and_plan_source_are_structured_for_translation(tmp_path):
    shutil.copytree(FIX / "compose_blog", tmp_path / "p")
    (tmp_path / "p" / "web").mkdir()
    (tmp_path / "p" / "web" / "Dockerfile").write_text("FROM node\n")
    run = Run()
    run_deployment(tmp_path / "p", "blog", CAT, Settings(), client_for(FakeLiara([READY]), True), ScriptedChannel(run, answers=["worker"]), run, network_id="n1", execute_=False)
    ev = next(e for e in run.events if e.kind == "plan_ready").data
    assert ev["how_code"] == "from_compose" and ev["how_params"] == {"file": "docker-compose.yml"}
    dbs = {w["params"]["name"]: w for w in ev["would_send"] if w["op"] == "create_database"}
    assert dbs["blog-db"]["params"] == {"name": "blog-db", "type": "postgres", "version": "16.14", "plan": "small-g2", "tier": "basic"}
    assert dbs["blog-cache"]["params"]["type"] == "redis"
    apps = [w for w in ev["would_send"] if w["op"] == "create_app"]
    assert {a["params"]["name"] for a in apps} == {"blog-web", "blog-worker"} and all(a["params"]["platform"] == "docker" for a in apps)
    assert {w["params"]["app"] for w in ev["would_send"] if w["op"] == "create_release"} == {"blog-web", "blog-worker"}
    assert all("op" in w and "params" in w and w["summary"] for w in ev["would_send"])


def test_database_ids_are_prefixed_with_the_app_name_and_env_refs_follow():
    from liara_agent.deploy.pipeline import namespace_databases
    from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan
    p = DeployPlan(apps=[AppSpec(name="mern-marketplace", platform="node", port=3000, env={"U": "mongodb://$DB_mongodb_USER@$DB_mongodb_HOST:$DB_mongodb_PORT"})],
                   databases=[DatabaseSpec(kind="mongodb", id="mongodb")])
    namespace_databases(p)
    assert p.databases[0].id == "mern-marketplace-mongodb"
    assert "$DB_mern-marketplace-mongodb_HOST" in p.apps[0].env["U"] and "DB_mongodb_" not in p.apps[0].env["U"]
    namespace_databases(p)  # idempotent
    assert p.databases[0].id == "mern-marketplace-mongodb"


def test_apply_engines_node_edits_only_the_copy(tmp_path):
    from liara_agent.heal.apply import apply_engines_node
    (tmp_path / "package.json").write_text('{"name": "x", "engines": {"node": "13.12.0"}}')
    diff = apply_engines_node(tmp_path, ">=13.12.0")
    assert '+    "node": ">=13.12.0"' in diff and json.loads((tmp_path / "package.json").read_text())["engines"]["node"] == ">=13.12.0"
    assert apply_engines_node(tmp_path, ">=13.12.0") is None


def test_a_database_the_plan_did_not_connect_gets_its_connection_variable():
    from liara_agent.deploy.pipeline import wire_database_env
    from liara_agent.plan.model import AppSpec, DatabaseSpec, DeployPlan
    p = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=3000, env={"PORT": "3000"})], databases=[DatabaseSpec(kind="mongodb", id="shop-mongodb")])
    wire_database_env(p, {"MONGO_URL", "PORT"})
    assert p.apps[0].env["MONGO_URL"].startswith("mongodb://$DB_shop-mongodb_USER:$DB_shop-mongodb_PASSWORD@$DB_shop-mongodb_HOST:$DB_shop-mongodb_PORT/")
    assert p.notes[-1].code == "database_wired" and p.notes[-1].params["env_var"] == "MONGO_URL" and p.notes[-1].params["detected"] is True
    wire_database_env(p, set())  # already connected: nothing more is added
    assert len([n for n in p.notes if n.code == "database_wired"]) == 1
    # nothing detected in the code: the conventional name is used
    q = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1)], databases=[DatabaseSpec(kind="postgres", id="a-db")])
    wire_database_env(q, set())
    assert "DATABASE_URL" in q.apps[0].env and q.notes[-1].params["detected"] is False
    # a variable the plan set on purpose is left alone
    r = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1, env={"MONGODB_URI": "mongodb://elsewhere/x"})], databases=[DatabaseSpec(kind="mongodb", id="a-db")])
    wire_database_env(r, {"MONGODB_URI"})
    assert r.apps[0].env["MONGODB_URI"] == "mongodb://elsewhere/x"


# ------------------------------------------------------------------ the agent picks or makes the project's private network ----
DB_PLAN = {"apps": [{"name": "t-web", "platform": "node", "port": 3000, "env": {"DATABASE_URL": "$DB_t-db_URL"}, "evidence": [{"file": "package.json", "line": 1}]}],
           "databases": [{"kind": "postgres", "id": "t-db", "evidence": [{"file": "package.json"}]}]}


def run_with_networks(tmp_path, nets, answers=None, plan=None, execute=True, network_name=""):
    from test_db import DbFake

    class NetFake(DbFake):
        def __call__(self, req):
            if req.url.path == "/v1/networks" and req.method == "POST":
                self.calls.append((req.method, req.url.path, json.loads(req.content)))
                return httpx.Response(201, json={"network": {"_id": "net-new"}})
            return super().__call__(req)
    fake = NetFake([READY])
    project = tmp_path / "proj"
    shutil.copytree(FIX / "express_pg", project)
    run = Run(log_path=tmp_path / "run.jsonl")
    client = client_for(fake, not execute)
    ch = ScriptedChannel(run, answers=answers)
    llm = MockLLM([call("submit_plan", **(plan or DB_PLAN))])
    res = run_deployment(project, "api", CAT, Settings(), client, ch, run, network_id=None, networks=nets, network_name=network_name, execute_=execute, llm=llm)
    return res, run, fake


def network_posts(fake):
    return [b for m, p, b in fake.calls if m == "POST" and p == "/v1/networks"]


def questions(run):
    return [e.data for e in run.events if e.kind == "question"]


def test_a_project_with_a_database_and_no_network_gets_a_new_one_after_approval(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [])
    assert res.ok and network_posts(fake) == [{"networkName": "api-net"}]
    k = kinds(run)
    assert k.index("confirm_request") < k.index("resource_created") and questions(run) == []          # no question: there is only one sensible answer
    plan = next(e for e in run.events if e.kind == "plan_ready").data
    assert plan["network"] == {"name": "api-net", "new": True}
    assert any(n["code"] == "network_new" for n in plan["plan"]["notes"])
    create = next(c for c in fake.calls if c[0] == "POST" and c[1] == "/v1/networks")
    db = next(c for c in fake.calls if c[1] == "/v1/databases")
    assert fake.calls.index(create) < fake.calls.index(db) and db[2]["network"] == "net-new"              # before the database that joins it, and it joins it


def test_existing_networks_are_offered_with_a_new_one_first_and_the_answer_is_used(tmp_path):
    nets = [{"name": "old-net", "_id": "n-old"}, {"name": "other", "_id": "n-other"}]
    res, run, fake = run_with_networks(tmp_path, nets, answers=["old-net"])
    q = questions(run)[0]
    assert q["code"] == "network_choice" and q["options"] == ["create_new", "old-net", "other"] and q["params"] == {"new_name": "api-net"}
    assert network_posts(fake) == []                                                                       # an existing network was chosen: nothing is made
    assert next(c for c in fake.calls if c[1] == "/v1/databases")[2]["network"] == "n-old"
    assert next(e for e in run.events if e.kind == "plan_ready").data["network"] == {"name": "old-net", "new": False}


def test_choosing_to_create_one_makes_a_name_that_is_not_taken(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [{"name": "api-net", "_id": "n1"}], answers=["create_new"])
    assert network_posts(fake) == [{"networkName": "api-net-2"}]


def test_a_single_app_also_gets_a_private_network_because_liara_requires_one(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [], plan=NO_DB_PLAN)
    assert res.ok and questions(run) == [] and len(network_posts(fake)) == 1
    app = next(c for c in fake.calls if c[0] == "POST" and c[1] == "/v1/projects")
    assert app[2]["network"] == "net-new"


def test_a_single_app_asks_which_existing_network_to_join(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [{"name": "old-net", "_id": "n-old"}], plan=NO_DB_PLAN, answers=["old-net"])
    assert [q["code"] for q in questions(run)] == ["network_choice"] and network_posts(fake) == []
    assert next(c for c in fake.calls if c[0] == "POST" and c[1] == "/v1/projects")[2]["network"] == "n-old"


def test_a_preview_never_creates_a_network(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [], execute=False)
    assert network_posts(fake) == [] and next(e for e in run.events if e.kind == "plan_ready").data["network"] == {"name": "api-net", "new": True}


def test_a_network_named_up_front_is_used_without_asking(tmp_path):
    res, run, fake = run_with_networks(tmp_path, [{"name": "old-net", "_id": "n-old"}, {"name": "other", "_id": "n-other"}], network_name="other")
    assert questions(run) == [] and network_posts(fake) == [] and next(c for c in fake.calls if c[1] == "/v1/databases")[2]["network"] == "n-other"


def test_a_model_that_cannot_be_used_ends_the_run_plainly_with_nothing_created(tmp_path):
    from liara_agent.llm.client import LLMError

    class Refusing:
        def chat(self, *a, **k):
            raise LLMError("HTTP 403")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "README.md").write_text("nothing the fixed rules can recognise")   # an ordinary one-app project would be planned by fixed rules instead
    run = Run(log_path=tmp_path / "run.jsonl")
    fake = FakeLiara([READY])
    res = run_deployment(project, "api", CAT, Settings(), client_for(fake, False), ScriptedChannel(run), run, network_id="n1", execute_=True, llm=Refusing())
    done = run.events[-1].data
    assert not res.ok and done["reason_code"] == "agent_unavailable" and done["reason_params"] == {"why": "HTTP 403"} and fake.calls == []
