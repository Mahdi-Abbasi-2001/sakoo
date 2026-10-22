import json
from pathlib import Path

from liara_agent.deploy.executor import execute
from liara_agent.heal.diagnose import Diagnosis, Failure, HealContext
from liara_agent.heal.loop import HealIO
from liara_agent.heal.repair import clean_lines, propose_repair, repair_fix
from liara_agent.llm.client import LLMError, MockLLM
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.plan.model import AppSpec, DeployPlan
from tests.test_heal import CAT, READY, FakeLiara, no_real_http, run  # noqa: F401  (the autouse fixture that fakes the HTTP check)


def call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def project(tmp_path: Path, **files) -> Path:
    for n, t in (files or {"app.js": "const x = ;\n"}).items():
        (tmp_path / n).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / n).write_text(t)
    return tmp_path


def test_a_proposed_change_is_checked_and_turned_into_a_diff(tmp_path):
    root = project(tmp_path, **{"app.js": "const x = ;\nconsole.log(x)\n"})
    llm = MockLLM([call("read_file", path="app.js"),
                   call("propose_fix", summary="a stray semicolon", changes=[{"path": "app.js", "replace": [{"find": "const x = ;", "with": "const x = 1;"}]}])])
    r = propose_repair(llm, root, "SyntaxError in app.js:1", ["SyntaxError: Unexpected token ';'"], ["SyntaxError: Unexpected token ';'"])
    assert r.ok and r.steps == 2 and r.summary == "a stray semicolon"
    assert "+const x = 1;" in r.plan["diff"] and r.plan["files"][0]["path"] == "app.js"
    assert (root / "app.js").read_text().startswith("const x = ;")   # nothing is written by the proposal


def test_a_path_outside_the_project_and_a_secret_are_refused_and_the_model_can_correct_itself(tmp_path):
    root = project(tmp_path)
    llm = MockLLM([call("propose_fix", summary="x", changes=[{"path": "../evil.js", "content": "1"}]),
                   call("propose_fix", summary="x", changes=[{"path": "app.js", "content": "const k = 'sk-abcdefghijklmnopqrstuvwxyz123456';\n"}]),
                   call("propose_fix", summary="fixed", changes=[{"path": "app.js", "content": "const x = 1;\n"}])])
    r = propose_repair(llm, root, "s", [], [])
    assert r.ok and r.steps == 3 and r.plan["new"] == {"app.js": "const x = 1;\n"}
    errors = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert "not a path inside the project" in errors[0] and "looks like a secret" in errors[1]


def test_give_up_and_a_missing_model_are_said_in_words(tmp_path):
    root = project(tmp_path)
    r = propose_repair(MockLLM([call("give_up", reason="the cause is not in the code")]), root, "s", [], [])
    assert not r.ok and r.gave_up == "the cause is not in the code"

    class Down:
        def chat(self, *a, **k):
            raise LLMError("timeout")
    assert "could not be reached" in propose_repair(Down(), root, "s", [], []).gave_up
    assert repair_fix(None, Diagnosis("code_error", "x"), Failure("app:a"), HealContext(app="a", platform="node", port=3000, plan_id="small-g2", tier="basic", cat=CAT), root) is None


def test_env_templates_only_wire_databases_the_agent_created(tmp_path):
    root = project(tmp_path)
    tpl = "mongodb://$DB_shop-mongo_USER:$DB_shop-mongo_PASSWORD@$DB_shop-mongo_HOST:$DB_shop-mongo_PORT/shop"
    good = propose_repair(MockLLM([call("propose_fix", summary="use the database", env=[{"name": "MONGODB_URI", "template": tpl}])]), root, "s", [], [], databases={"shop-mongo": "mongodb"})
    assert good.ok and good.env == {"MONGODB_URI": tpl} and good.plan is None
    llm = MockLLM([call("propose_fix", summary="x", env=[{"name": "MONGODB_URI", "template": "mongodb://admin:hunter22@10.0.0.1/db"}]),
                   call("propose_fix", summary="x", env=[{"name": "MONGODB_URI", "template": "mongodb://$DB_other_HOST/db"}]),
                   call("propose_fix", summary="x", env=[{"name": "NODE_OPTIONS", "template": "$DB_shop-mongo_HOST"}]),
                   call("give_up", reason="no")])
    r = propose_repair(llm, root, "s", [], [], databases={"shop-mongo": "mongodb"})
    msgs = [m["content"] for m in llm.seen[-1] if m["role"] == "tool"]
    assert not r.ok and "cannot set literal values" in msgs[0] and "no database with id 'other'" in msgs[1] and "repair may set" in msgs[2]


def test_logs_and_secrets_are_filtered_before_the_model_sees_them(tmp_path):
    root = project(tmp_path)
    llm = MockLLM([call("give_up", reason="x")])
    propose_repair(llm, root, "s", [], ["connecting to mongodb://admin:s3cretpass@host/db", "token=abcdef123456", "my typed value ZZtopsecret99"], secrets={"ZZtopsecret99"})
    seen = llm.seen[0][1]["content"]
    assert "s3cretpass" not in seen and "abcdef123456" not in seen and "ZZtopsecret99" not in seen and "[hidden]" in seen
    assert "untrusted" in llm.seen[0][0]["content"]
    assert clean_lines(["a"] * 200, n=5) == ["a"] * 5


# -- through the self-heal loop ------------------------------------------------------------------------------------------------
def heal_io(llm, **kw):
    io = HealIO(repair=lambda d, f, c, root: repair_fix(llm, d, f, c, root, emit=lambda k, data: io.seen.append((k, data))), emit=lambda k, data: io.seen.append((k, data)), **kw)
    io.seen = []   # what the loop emitted (the executor's own events are the third value `run` returns)
    return io


CRASH = {"state": "UNHEALTHY"}
SYNTAX = ["/app/server.js:3", "const port = ;", "SyntaxError: Unexpected token ';'"]


def test_a_code_error_no_rule_knows_is_fixed_in_the_copy_after_approval_and_redeployed(tmp_path):
    fake = FakeLiara([CRASH, READY], runtime=[SYNTAX])
    llm = MockLLM([call("propose_fix", summary="a stray semicolon", changes=[{"path": "server.js", "content": "const port = 3000;\n"}])])
    asked = []
    io = heal_io(llm, confirm=lambda q, **kw: asked.append((q, kw["kind"], kw["params"])) or True)
    out, _, _ = run(tmp_path, fake, io=io, files={"Dockerfile": "FROM node\n", "server.js": "const port = ;\n"})
    events = io.seen
    assert out.ok and len(fake.uploads) == 2
    assert asked and asked[0][1] == "patch" and "copy of your project" in asked[0][0]
    assert "+const port = 3000;" in asked[0][2]["diff"] and asked[0][2]["summary"] == "a stray semicolon"   # the person decides on the diff itself
    assert (tmp_path / "server.js").read_text() == "const port = ;\n"           # the developer's file is untouched
    assert (tmp_path / ".agent-state" / "patched" / "t-web" / "server.js").read_text() == "const port = 3000;\n"
    assert "+const port = 3000;" in (tmp_path / ".agent-state" / "fixes.patch").read_text()
    kinds = [k for k, _ in events]
    assert "diagnosis" in kinds and "patch_ready" in kinds
    diag = next(d for k, d in events if k == "diagnosis")
    assert diag["code"] == "code_error" and diag["category"] == "code"


def test_declining_the_repair_stops_and_auto_fix_skips_the_question(tmp_path):
    files = {"Dockerfile": "FROM node\n", "server.js": "const port = ;\n"}
    fix = lambda: MockLLM([call("propose_fix", summary="s", changes=[{"path": "server.js", "content": "const port = 3000;\n"}])])   # noqa: E731
    out, _, _ = run(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, FakeLiara([CRASH], runtime=[SYNTAX]), io=heal_io(fix(), confirm=lambda q, **kw: False), files=files)
    assert not out.ok and out.diagnosis.code == "code_error"
    (tmp_path / "b").mkdir()
    asked = []
    io = heal_io(fix(), auto_fix=True, confirm=lambda q, **kw: asked.append(q) or True)
    out, _, _ = run(tmp_path / "b", FakeLiara([CRASH, READY], runtime=[SYNTAX]), io=io, files=files)
    assert out.ok and not asked and any(k == "fix_auto_applied" for k, _ in io.seen)


def test_the_same_failure_after_a_repair_is_reported_not_looped(tmp_path):
    fake = FakeLiara([CRASH, CRASH, CRASH, CRASH], runtime=[SYNTAX, SYNTAX, SYNTAX])
    change = lambda: call("propose_fix", summary="s", changes=[{"path": "server.js", "content": "const port = 3000;\n"}])   # noqa: E731
    llm = MockLLM([change(), change(), change()])
    out, _, _ = run(tmp_path, fake, io=heal_io(llm, confirm=lambda q, **kw: True), files={"Dockerfile": "FROM node\n", "server.js": "const port = ;\n"})
    assert not out.ok and out.diagnosis.code == "code_error"
    assert len(fake.uploads) <= 3   # one repair, then the identical proposal is refused


def test_no_model_means_the_old_honest_answer(tmp_path):
    out, _, _ = run(tmp_path, FakeLiara([CRASH], runtime=[SYNTAX]), io=heal_io(None, confirm=lambda q, **kw: True))
    assert not out.ok and out.diagnosis.code == "code_error"


def test_resizing_still_asks_even_with_auto_fix(tmp_path):
    asked = []
    out, _, _ = run(tmp_path, FakeLiara([{"state": "FAILED", "failReason": "build took more than 5 minutes"}, READY]), io=HealIO(auto_fix=True, confirm=lambda q, **kw: asked.append(q) or True))
    assert out.ok and asked and "toman/hour" in asked[0]


def test_localhost_database_is_wired_by_an_env_variable_the_model_never_sees_the_value_of(tmp_path):
    tpl = "mongodb://$DB_t-db_USER:$DB_t-db_PASSWORD@$DB_t-db_HOST:$DB_t-db_PORT/shop"
    llm = MockLLM([call("propose_fix", summary="read the URI from MONGODB_URI", env=[{"name": "MONGODB_URI", "template": tpl}])])
    fake = FakeLiara([CRASH, READY], runtime=[["Error: unable to connect to database: mongodb://localhost:27017/shop"]])
    http = __import__("httpx").Client(base_url="https://api.liara.ir", transport=__import__("httpx").MockTransport(fake))
    (tmp_path / "Dockerfile").write_text("FROM node\n")
    client = LiaraClient("tok", dry_run=False, journal=Journal(), http=http)
    plan = DeployPlan(apps=[AppSpec(name="t-web", platform="docker", port=3000)])
    ctx_kinds = {}
    import liara_agent.deploy.executor as ex
    orig = ex.HealContext

    def spy(**kw):
        kw["db_kinds_by_id"] = {"t-db": "mongodb"}
        kw["db_api_ids"] = {"t-db": "db-1"}
        ctx_kinds.update(kw)
        return orig(**kw)
    ex.HealContext = spy
    try:
        client.database = lambda i: {"hostname": "t-db.liara", "internalPort": 27017, "username": "root", "root_password": "p@ss/w", "type": "mongodb"}
        out = execute(plan, CAT, client, tmp_path, network_id="n1", emit=lambda k, d: None, heal=heal_io(llm, confirm=lambda q, **kw: True))
    finally:
        ex.HealContext = orig
    assert out.ok
    sent = [c[2] for c in fake.calls if c[1].endswith("update-envs")]
    assert {"key": "MONGODB_URI", "value": "mongodb://root:p%40ss%2Fw@t-db.liara:27017/shop"} in sent[-1]["variables"]
    assert "p@ss" not in json.dumps(llm.seen)
