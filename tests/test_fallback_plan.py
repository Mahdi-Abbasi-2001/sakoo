import json

from liara_agent.detect.fingerprints import fingerprint
from liara_agent.llm.client import LLMError
from liara_agent.plan.fallback import plan_without_model


def project(tmp_path, files):
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def plan(tmp_path, files, name="shop"):
    return plan_without_model(project(tmp_path, files), name, fingerprint(tmp_path))


def test_a_node_api_with_mongo_becomes_one_app_and_one_database(tmp_path):
    p = plan(tmp_path, {"package.json": json.dumps({"dependencies": {"express": "4", "mongoose": "8"}, "scripts": {"start": "node server.js"}}),
                        "server.js": "const k = process.env.JWT_SECRET; const s = process.env.STRIPE_KEY; const port = process.env.PORT; const u = process.env.MONGODB_URI;"})
    assert [(a.name, a.platform, a.port, a.path) for a in p.apps] == [("shop", "node", 3000, ".")]
    assert [(d.kind, d.id) for d in p.databases] == [("mongodb", "mongodb")]
    names = {e.name: (e.source, e.secret) for e in p.env}
    assert names == {"JWT_SECRET": ("generated", True), "STRIPE_KEY": ("user", True)}   # the database url and PORT are the pipeline's; a third party's key is asked, never invented
    assert p.apps[0].evidence and p.notes[-1].code == "planned_without_model" and p.notes[-1].level == "warning"


def test_a_full_stack_node_project_with_a_react_front_end_is_run_by_its_server(tmp_path):
    p = plan(tmp_path, {"package.json": json.dumps({"dependencies": {"express": "4", "react": "18", "mongoose": "8"}})})
    assert p.apps[0].platform == "node" and [d.kind for d in p.databases] == ["mongodb"]


def test_django_flask_and_a_plain_static_front_end(tmp_path):
    assert plan(tmp_path, {"manage.py": "", "requirements.txt": "django\npsycopg2\n"}).apps[0].platform == "django"
    other = tmp_path / "b"
    assert plan(other, {"requirements.txt": "flask\n"}).apps[0].platform == "flask"
    third = tmp_path / "c"
    p = plan(third, {"package.json": json.dumps({"dependencies": {"react": "18", "react-scripts": "5"}})})
    assert p.apps[0].platform == "react" and p.databases == []


def test_a_dockerfile_wins_and_its_exposed_port_is_used(tmp_path):
    p = plan(tmp_path, {"Dockerfile": "FROM python:3.12\nEXPOSE 8123\nCMD [\"python\", \"a.py\"]\n", "requirements.txt": "flask\n"})
    assert (p.apps[0].platform, p.apps[0].port) == ("docker", 8123)


def test_a_monorepo_or_an_unrecognised_project_is_left_to_the_model(tmp_path):
    assert plan(tmp_path, {"client/package.json": json.dumps({"dependencies": {"react": "18"}}),
                           "server/package.json": json.dumps({"dependencies": {"express": "4"}})}) is None
    assert plan(tmp_path / "x", {"README.md": "hello"}) is None


def test_make_plan_falls_back_when_the_model_cannot_be_used_and_says_so(tmp_path):
    from liara_agent.agent.planner import make_plan
    from liara_agent.config import Settings
    from liara_agent.liara.live import snapshot_catalog

    class Refusing:
        def chat(self, *a, **k):
            raise LLMError("HTTP 403")
    root = project(tmp_path, {"package.json": json.dumps({"dependencies": {"express": "4"}})})
    plan_, how, res = make_plan(root, "shop", snapshot_catalog(), Settings(), llm=Refusing())
    assert how == "from fixed rules" and res is None and plan_.apps[0].platform == "node"
    nothing = project(tmp_path / "n", {"README.md": "x"})
    try:
        make_plan(nothing, "shop", snapshot_catalog(), Settings(), llm=Refusing())
        raise AssertionError("an unrecognised project still needs the model")
    except LLMError:
        pass


def test_where_the_app_listens_is_set_not_asked(tmp_path):
    p = plan(tmp_path, {"package.json": json.dumps({"dependencies": {"express": "4"}}),
                        "server.js": "app.listen(process.env.PORT, process.env.IP || '0.0.0.0'); const h = process.env.HOST; const d = process.env.DEBUG; const k = process.env.OPENAI_API_KEY;"})
    assert p.apps[0].env == {"IP": "0.0.0.0", "HOST": "0.0.0.0"}
    assert [e.name for e in p.env] == ["OPENAI_API_KEY"]                      # only what the person really has to provide


def test_connection_settings_read_one_by_one_point_at_the_database_not_at_the_person(tmp_path):
    p = plan(tmp_path, {"package.json": json.dumps({"dependencies": {"express": "4", "mongoose": "8"}}),
                        "db.js": "const a = process.env.MONGO_HOST, b = process.env.MONGO_PORT, c = process.env.MONGO_USER, d = process.env.MONGO_PASSWORD, e = process.env.MONGO_DB; process.env.SMTP_HOST;"})
    assert p.apps[0].env == {"MONGO_HOST": "$DB_mongodb_HOST", "MONGO_PORT": "$DB_mongodb_PORT", "MONGO_USER": "$DB_mongodb_USER",
                             "MONGO_PASSWORD": "$DB_mongodb_PASSWORD", "MONGO_DB": "$DB_mongodb_NAME"}
    assert [e.name for e in p.env] == ["SMTP_HOST"]                                # someone else's server: still the person's to say
