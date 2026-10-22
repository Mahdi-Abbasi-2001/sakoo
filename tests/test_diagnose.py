import pytest

from liara_agent.heal.diagnose import Failure, HealContext, diagnose
from liara_agent.liara.live import snapshot_catalog

CAT = snapshot_catalog()


def ctx(**kw):
    base = dict(app="a", platform="docker", port=3000, plan_id="small-g2", tier="basic", cat=CAT)
    return HealContext(**{**base, **kw})


def fx(d, kind):
    return [f for f in d.fixes if f.kind == kind]


def test_build_timeout_is_a_plan_problem_with_a_priced_fix():
    # the real message from a failed release of a basic-tier app
    d = diagnose(Failure("app:a", "FAILED", reason="build took more than 5 minutes"), ctx())
    assert d.code == "build_timeout" and d.retryable
    (fix,) = fx(d, "resize")
    assert fix.code == "resize_tier" and fix.params["plan"] == "small-g2" and fix.params["tier"] == "standard" and fix.params["from_tier"] == "basic" and fix.cost_delta_hourly > 0
    assert fix.params["tier_label"] == "silver" and fix.params["build_minutes"] == 15 and fix.params["from_build_minutes"] == 5
    assert d.params == {"tier_label": "basic", "limit_minutes": 5}
    assert "silver" in fix.description and "15" in fix.description  # the UI calls the standard tier silver


def test_no_higher_tier_left_means_only_manual_advice():
    d = diagnose(Failure("app:a", "FAILED", reason="build took more than 60 minutes"), ctx(tier="pro"))
    assert d.code == "build_timeout" and not fx(d, "resize") and fx(d, "manual")


def test_out_of_memory_suggests_a_bigger_plan():
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=["FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory"]), ctx())
    assert d.code == "out_of_memory"
    (fix,) = fx(d, "resize")
    assert CAT.project_plans[fix.params["plan"]].ram.amount > CAT.project_plans["small-g2"].ram.amount and fix.cost_delta_hourly > 0


def test_missing_python_module_patches_requirements_only_when_it_exists():
    log = ["Traceback (most recent call last):", '  File "app.py", line 1, in <module>', "ModuleNotFoundError: No module named 'flask_cors'"]
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx(project_files={"requirements.txt"}))
    assert d.code == "missing_python_module"
    assert fx(d, "code_patch")[0].params == {"file": "requirements.txt", "append": "flask_cors"}
    assert not fx(diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx()), "code_patch")


def test_missing_node_module_patches_package_json_when_it_exists():
    log = ["Error: Cannot find module 'express-rate-limit'"]
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx(project_files={"package.json"}))
    assert d.code == "missing_node_module" and d.category == "code"
    assert fx(d, "code_patch")[0].params == {"file": "package.json", "add_dependency": "express-rate-limit"}
    # without a package.json there is nothing to patch: honest manual advice
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx())
    assert not fx(d, "code_patch") and "npm install express-rate-limit" in d.fixes[0].description
    assert diagnose(Failure("a", runtime_lines=["Error: Cannot find module './config'"]), ctx()).code != "missing_node_module"
    assert diagnose(Failure("a", runtime_lines=["Error: Cannot find module '@scope/pkg'"]), ctx(project_files={"package.json"})).fixes[0].params["add_dependency"] == "@scope/pkg"


def test_categories_say_who_has_to_change_what():
    cases = {"missing_python_module": "code", "db_localhost": "code", "code_error": "code", "missing_env": "config", "wrong_port": "config",
             "build_timeout": "resources", "out_of_memory": "resources", "outbound_blocked": "outside"}
    from liara_agent.heal.diagnose import Diagnosis
    for code, cat in cases.items():
        assert Diagnosis(code, "x").category == cat, code


def test_a_startup_error_in_the_users_code_is_a_code_error_with_its_location():
    log = ["/app/src/server.js:12", "  const port = config.port.toString();", "TypeError: Cannot read properties of undefined (reading 'toString')",
           "    at Object.<anonymous> (/app/src/server.js:12:28)", "    at node:internal/modules/cjs/loader:1105:14"]
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx())
    assert d.code == "code_error" and d.category == "code" and d.params["location"] == "src/server.js:12"
    assert any("TypeError" in e for e in d.evidence)


def test_python_traceback_location_skips_library_frames():
    log = ["Traceback (most recent call last):", '  File "/usr/lib/python3.11/site-packages/flask/app.py", line 10, in run', '  File "app.py", line 7, in <module>', "SyntaxError: invalid syntax"]
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=log), ctx())
    assert d.code == "code_error" and d.params["location"] == "app.py:7"


def test_a_known_cause_wins_over_the_generic_code_error():
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=["Error: unable to connect to database: mongodb://localhost:27017/x", "TypeError: foo"]), ctx(db_kinds=set()))
    assert d.code == "db_localhost"


@pytest.mark.parametrize("line,name", [
    ("KeyError: 'SECRET_KEY'", "SECRET_KEY"),
    ("django.core.exceptions.ImproperlyConfigured: Set the DATABASE_URL environment variable", "DATABASE_URL"),
    ("Error: JWT_SECRET is not defined", "JWT_SECRET"),
    ("Missing required environment variable: STRIPE_KEY", "STRIPE_KEY"),
])
def test_missing_env_variable_names(line, name):
    d = diagnose(Failure("app:a", "UNHEALTHY", runtime_lines=[line]), ctx())
    assert d.code == "missing_env" and fx(d, "set_env")[0].params["names"] == [name]


def test_already_set_env_names_are_not_asked_for_again():
    d = diagnose(Failure("a", runtime_lines=["Error: JWT_SECRET is not defined"]), ctx(env_names={"JWT_SECRET"}))
    assert d.code != "missing_env"


def test_localhost_database_is_recognised_with_and_without_a_planned_database():
    log = ["Error: connect ECONNREFUSED 127.0.0.1:5432"]
    assert "hostname" in diagnose(Failure("a", runtime_lines=log), ctx(db_kinds={"postgres"})).fixes[0].description
    d = diagnose(Failure("a", runtime_lines=log), ctx())
    assert d.code == "db_localhost" and "create a Liara postgres database" in d.fixes[0].description
    assert diagnose(Failure("a", runtime_lines=["redis.exceptions.ConnectionError: Error 111 connecting to localhost:6379. Connection refused."]), ctx()).code == "db_localhost"


def test_foreign_service_blocked_is_not_retryable_and_not_a_code_bug():
    # taken from a real app's log on Liara
    d = diagnose(Failure("a", runtime_lines=["webhook refresh failed on tg: network error (RemoteProtocolError)", "httpx.ConnectTimeout: api.telegram.org"]), ctx())
    assert d.code == "outbound_blocked" and not d.retryable and "api.telegram.org" in d.summary


def test_wrong_port_is_fixed_from_the_logs():
    d = diagnose(Failure("a", "READY", runtime_lines=["Server listening on port 8080"], http_detail="HTTP 502"), ctx())
    assert d.code == "wrong_port" and fx(d, "set_port")[0].params["port"] == 8080 and d.params == {"actual": 8080, "expected": 3000}


def test_same_port_is_not_reported_as_wrong():
    assert diagnose(Failure("a", "READY", runtime_lines=["listening on port 3000"], http_detail="HTTP 502"), ctx()).code == "not_serving"


def test_bound_to_localhost():
    d = diagnose(Failure("a", "READY", runtime_lines=["Running on http://127.0.0.1:3000"], http_detail="HTTP 502"), ctx())
    assert d.code == "bound_to_localhost" and "0.0.0.0" in d.fixes[0].description


def test_unknown_failures_are_reported_honestly():
    d = diagnose(Failure("a", "FAILED", reason="something odd"), ctx())
    assert d.code == "unknown" and not d.retryable


@pytest.mark.parametrize("line", ["Set the table first", "required fields are missing from the form", "Missing semicolon at line 3", "please set the debug flag"])
def test_ordinary_sentences_are_not_env_variables(line):
    assert diagnose(Failure("a", runtime_lines=[line]), ctx()).code != "missing_env"


def test_every_diagnosis_and_fix_has_a_stable_code_for_translation():
    cases = [
        (Failure("a", "FAILED", reason="build took more than 5 minutes"), {}),
        (Failure("a", runtime_lines=["JavaScript heap out of memory"]), {}),
        (Failure("a", runtime_lines=["ModuleNotFoundError: No module named 'x'"]), {"project_files": {"requirements.txt"}}),
        (Failure("a", runtime_lines=["Cannot find module 'x'"]), {}),
        (Failure("a", runtime_lines=["Error: JWT_SECRET is not defined"]), {}),
        (Failure("a", runtime_lines=["connect ECONNREFUSED 127.0.0.1:5432"]), {}),
        (Failure("a", runtime_lines=["connect ECONNREFUSED 127.0.0.1:5432"]), {"db_kinds": {"postgres"}}),
        (Failure("a", runtime_lines=["httpx.ConnectTimeout: api.telegram.org"]), {}),
        (Failure("a", "READY", runtime_lines=["listening on port 8080"], http_detail="HTTP 502"), {}),
        (Failure("a", "READY", runtime_lines=["Running on http://127.0.0.1:3000"], http_detail="HTTP 502"), {}),
        (Failure("a", runtime_lines=["Dockerfile not found"]), {}),
        (Failure("a", "READY", http_detail="HTTP 502"), {}),
        (Failure("a", "FAILED", reason="something odd"), {}),
        (Failure("a", runtime_lines=["password authentication failed"]), {"db_api_ids": {"d": "x"}}),
    ]
    for failure, kw in cases:
        d = diagnose(failure, ctx(**kw))
        assert d.code and isinstance(d.params, dict)
        assert d.fixes and all(f.code for f in d.fixes), (d.code, [f.description for f in d.fixes])
        ev = d.fixes[0].as_event()
        assert set(ev) == {"code", "kind", "text", "params", "cost_delta_hourly"}


def test_node_engine_pin_is_patched_in_a_copy_and_a_supported_node_is_chosen():
    # the real yarn error from deploying shamahoque/mern-marketplace (engines.node pinned to 13.12.0)
    log = ['error mern-marketplace@2.0.0: The engine "node" is incompatible with this module. Expected version "13.12.0". Got "22.23.3"', "error Found incompatible module."]
    d = diagnose(Failure("app:a", "FAILED", build_lines=log), ctx(platform="node", project_files={"package.json"}))
    assert d.code == "node_engine_mismatch" and d.retryable
    (fix,) = fx(d, "code_patch")
    assert fix.code == "patch_node_engine" and fix.params == {"file": "package.json", "engines_node": ">=13.12.0", "platform_version": "14", "expected": "13.12.0"}
    # without a package.json to patch, or on another platform, this rule does not apply
    assert diagnose(Failure("app:a", "FAILED", build_lines=log), ctx(platform="node")).code != "node_engine_mismatch"


def test_node_for_engine_picks_the_nearest_supported_version():
    from liara_agent.heal.diagnose import node_for_engine
    assert node_for_engine("13.12.0") == ("14", ">=13.12.0")
    assert node_for_engine("^16.13.0") == ("16", ">=16.13.0")
    assert node_for_engine("8.11.1")[0] == "14" and node_for_engine("30")[0] == "24"


def test_a_release_that_ended_as_timeout_is_a_build_timeout():
    # Liara reported the state TIMEOUT for a build that ran past the basic tier's 5 minutes (real mern-marketplace deploy)
    d = diagnose(Failure("app:a", "FAILED", reason="release ended as TIMEOUT"), ctx(platform="node"))
    assert d.code == "build_timeout" and fx(d, "resize")[0].params["tier"] == "standard"


def test_a_crash_that_prints_the_localhost_database_url_is_diagnosed():
    # the real mern-marketplace crash: the app threw "unable to connect to database: mongodb://localhost:27017/mernproject"
    d = diagnose(Failure("app:a", "READY", runtime_lines=["Error: unable to connect to database: mongodb://localhost:27017/mernproject", "npm ERR! code ELIFECYCLE"],
                         http_detail="HTTP 502"), ctx(platform="node", db_kinds={"mongodb"}))
    assert d.code == "db_localhost" and d.params == {"kind": "mongodb"} and fx(d, "manual")[0].code == "db_point_host"
