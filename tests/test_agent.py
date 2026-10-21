import json
from pathlib import Path

from liara_agent.agent.loop import run_agent
from liara_agent.agent.prompts import digest, system_prompt
from liara_agent.llm.client import MockLLM
from liara_agent.tools.project import ProjectTools

FIX = Path(__file__).parent / "fixtures"


def call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


GOOD_PLAN = {
    "apps": [{"name": "api", "platform": "node", "port": 3000, "start_command": "npm start",
              "env": {"DATABASE_URL": "postgres://$DB_api-db_USER:$DB_api-db_PASSWORD@$DB_api-db_HOST:$DB_api-db_PORT/$DB_api-db_NAME"},
              "evidence": [{"file": "package.json", "line": 1, "note": "start script"}]}],
    "databases": [{"kind": "postgres", "id": "api-db", "evidence": [{"file": "package.json", "note": "dependency pg"}]}],
    "env": [{"name": "JWT_SECRET", "secret": True, "source": "user", "evidence": [{"file": "src/index.js", "line": 4}]}],
}


def test_agent_investigates_then_submits():
    llm = MockLLM([call("list_dir"), call("read_file", path="src/index.js"), call("submit_plan", **GOOD_PLAN)])
    events = []
    res = run_agent(llm, FIX / "express_pg", emit=lambda k, d: events.append(k))
    assert res.stopped_because == "submitted" and res.steps == 3
    assert res.plan.apps[0].port == 3000 and res.plan.databases[0].kind == "postgres"
    assert events.count("tool_call") == 3 and "plan_updated" in events
    # the model saw the file listing and the digest, never an unbounded dump
    assert "src/index.js" in llm.seen[1][-1]["content"]


def test_rejected_plan_is_returned_to_the_model_to_fix():
    bad = {"apps": [{"name": "api", "platform": "cobol", "port": 3000, "evidence": [{"file": "x"}]}]}
    llm = MockLLM([call("submit_plan", **bad), call("submit_plan", **GOOD_PLAN)])
    res = run_agent(llm, FIX / "express_pg")
    assert res.stopped_because == "submitted"
    assert "unknown Liara platform" in llm.seen[1][-1]["content"]


def test_claims_without_evidence_are_rejected():
    t = ProjectTools(FIX / "express_pg")
    out = t.call("submit_plan", json.dumps({"apps": [{"name": "api", "platform": "node", "port": 3000}]}))
    assert "no evidence" in out and t.submitted is None


def test_gives_up_after_repeated_rejections():
    llm = MockLLM([call("submit_plan", apps=[])] * 5)
    assert run_agent(llm, FIX / "express_pg").stopped_because == "plan_rejected"


def test_step_limit():
    llm = MockLLM([call("list_dir")] * 10)
    assert run_agent(llm, FIX / "express_pg", max_steps=4).stopped_because == "max_steps"


def test_tools_are_sandboxed(tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "secret.txt").write_text("top")
    t = ProjectTools(tmp_path / "proj")
    assert "outside the project" in t.call("read_file", {"path": "../secret.txt"})
    assert "outside the project" in t.call("list_dir", {"path": "/etc"})


def test_secrets_never_reach_the_model(tmp_path):
    (tmp_path / ".env").write_text("JWT_SECRET=abcdefghijklmnop1234\nDATABASE_URL=postgres://u:pw@h/db\n")
    (tmp_path / "server.js").write_text("const apiKey = 'sk-abcdefghijklmnop1234567890';\nconst port = 3000;\n")
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    t = ProjectTools(tmp_path)
    env = t.call("read_file", {"path": ".env"})
    assert "JWT_SECRET" in env and "abcdefghijklmnop1234" not in env and "pw@h" not in env
    code = t.call("read_file", {"path": "server.js"})
    assert "sk-abcdefghijklmnop" not in code and "<redacted>" in code and "port = 3000" in code
    assert "abcdefghijklmnop1234" not in t.call("grep", {"pattern": "JWT"})
    assert "JWT_SECRET=" in t.call("read_file", {"path": ".env.example"})  # examples are safe


def test_ask_user_without_a_human():
    assert "cannot be asked" in ProjectTools(FIX / "express_pg").call("ask_user", {"question": "which db?"})
    assert ProjectTools(FIX / "express_pg", ask=lambda q: "mysql").call("ask_user", {"question": "q"}) == "mysql"


def test_prompt_and_digest_content():
    assert "default port 8080" in system_prompt() and "$DB_<databaseId>_HOST" in system_prompt()
    d = digest(FIX / "express_pg")
    assert "hint db=postgres" in d and "JWT_SECRET [secret?]" in d


def test_single_app_end_to_end_with_scripted_model():
    import httpx

    from liara_agent.agent.planner import apply_env, make_plan
    from liara_agent.config import Settings
    from liara_agent.deploy.executor import execute
    from liara_agent.liara.client import Journal, LiaraClient
    from liara_agent.liara.live import snapshot_catalog

    cat = snapshot_catalog()
    llm = MockLLM([call("read_file", path="src/index.js"), call("submit_plan", **GOOD_PLAN)])
    plan, how, res = make_plan(FIX / "express_pg", "api", cat, Settings(), llm=llm)
    assert how == "investigated by the agent" and res.stopped_because == "submitted"

    asked = []
    missing = apply_env(plan, lambda name: asked.append(name) or "s3cret-value")
    assert asked == ["JWT_SECRET"] and missing == [] and plan.apps[0].env["JWT_SECRET"] == "s3cret-value"

    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    client = LiaraClient("tok", dry_run=True, journal=Journal(), http=http)
    out = execute(plan, cat, client, FIX / "express_pg", network_id="net1")
    assert out.ok
    assert [a.op for a in client.actions] == ["create_database", "create_app", "set_env", "upload_source", "create_release"]
    shown = json.dumps([a.body for a in client.actions])
    assert "s3cret-value" not in shown  # secret values never appear in the recorded actions
    assert plan.apps[0].env["DATABASE_URL"].count("$DB_api-db_") == 5  # resolved only after the database exists


def test_generated_env_values_are_random_and_unprompted():
    from liara_agent.agent.planner import apply_env
    from liara_agent.plan.model import AppSpec, DeployPlan, EnvVar

    p = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1)], env=[EnvVar(name="SECRET_KEY", secret=True, source="generated")])
    assert apply_env(p, None) == [] and len(p.apps[0].env["SECRET_KEY"]) >= 40


def test_ask_user_passes_options_through_and_language_reaches_the_prompt():
    seen = []
    t = ProjectTools(FIX / "express_pg", ask=lambda q, options=None: seen.append((q, options)) or "mysql")
    assert t.call("ask_user", {"question": "which db?", "options": ["postgres", "mysql"]}) == "mysql"
    assert seen == [("which db?", ["postgres", "mysql"])]
    assert "in Persian (Farsi)" in system_prompt("fa") and "in English" in system_prompt("en")


def test_generated_values_are_random_only_for_secrets_and_never_break_ports_or_urls():
    from liara_agent.agent.planner import apply_env
    from liara_agent.plan.model import AppSpec, DeployPlan, EnvVar

    p = DeployPlan(apps=[AppSpec(name="a", platform="node", port=3000)],
                   env=[EnvVar(name="SESSION_SECRET", secret=True, source="generated"), EnvVar(name="PORT", source="generated"),
                        EnvVar(name="API_BASE_URL", source="generated")])
    missing = apply_env(p, None)
    env = p.apps[0].env
    assert len(env["SESSION_SECRET"]) >= 40 and env["PORT"] == "3000"  # a port is the app's port, not a random string
    assert "API_BASE_URL" not in env and missing == ["API_BASE_URL"]  # unknown meaning: ask, never invent


def test_a_secret_the_model_invented_is_never_deployed():
    from liara_agent.agent.planner import apply_env
    from liara_agent.plan.model import AppSpec, DeployPlan, EnvVar

    def plan(source):
        return DeployPlan(apps=[AppSpec(name="a", platform="node", port=3000, env={"SIGNING_SECRET": "change-me-to-a-random-value", "GREETING": "hello", "DATABASE_URL": "postgres://$DB_x_USER@$DB_x_HOST"})],
                          env=[EnvVar(name="SIGNING_SECRET", secret=True, source=source), EnvVar(name="GREETING", source="user"), EnvVar(name="DATABASE_URL", secret=True, source="database")])

    p = plan("generated")
    apply_env(p, None)
    assert p.apps[0].env["SIGNING_SECRET"] != "change-me-to-a-random-value" and len(p.apps[0].env["SIGNING_SECRET"]) >= 40  # random, not the model's text
    assert p.apps[0].env["GREETING"] == "hello"  # a non-secret default from the code stays
    assert p.apps[0].env["DATABASE_URL"].startswith("postgres://$DB_")  # database references are untouched

    p = plan("user")
    assert apply_env(p, lambda name: "typed-by-the-user") == [] and p.apps[0].env["SIGNING_SECRET"] == "typed-by-the-user"

    p = plan("user")
    assert apply_env(p, None) == ["SIGNING_SECRET"] and "SIGNING_SECRET" not in p.apps[0].env  # nothing deployed rather than a guess


def test_the_port_is_never_asked_of_the_user_whatever_the_model_classified_it_as():
    from liara_agent.agent.planner import apply_env
    from liara_agent.plan.model import AppSpec, DeployPlan, EnvVar

    for source in ("user", "generated"):
        p = DeployPlan(apps=[AppSpec(name="a", platform="django", port=5006)], env=[EnvVar(name="PORT", source=source)])
        asked = []
        assert apply_env(p, lambda n: asked.append(n) or "x") == [] and asked == [] and p.apps[0].env["PORT"] == "5006"


def test_a_monorepo_part_missing_from_the_plan_is_pushed_back_once(tmp_path):
    import json
    from liara_agent.tools.project import ProjectTools, ToolError
    (tmp_path / "client").mkdir()
    (tmp_path / "server").mkdir()
    (tmp_path / "client" / "package.json").write_text(json.dumps({"dependencies": {"react": "18", "react-scripts": "5"}}))
    (tmp_path / "server" / "package.json").write_text(json.dumps({"dependencies": {"express": "4"}}))
    ev = [{"file": "server/package.json", "line": 1}]
    server_only = {"apps": [{"name": "api", "path": "server", "platform": "node", "port": 3000, "evidence": ev}]}
    tools = ProjectTools(tmp_path)
    out = tools.call("submit_plan", server_only)
    assert "client" in out and "react" in out and tools.submitted is None      # pushed back, and told which folder
    assert tools.call("submit_plan", server_only) == "plan accepted"        # the second, unchanged submission is accepted (the folder may not be part of it)
    both = {"apps": [*server_only["apps"], {"name": "web", "path": "client", "platform": "react", "port": 80, "evidence": [{"file": "client/package.json", "line": 1}]}]}
    assert ProjectTools(tmp_path).call("submit_plan", both) == "plan accepted"   # covered: no push back
    root_app = {"apps": [{"name": "all", "path": ".", "platform": "node", "port": 3000, "evidence": ev}]}
    assert ProjectTools(tmp_path).call("submit_plan", root_app) == "plan accepted"
