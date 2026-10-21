import pytest

from liara_agent.deploy.randvars import substitute_random
from liara_agent.liara.oneclick import catalog, compose_spec
from liara_agent.plan.oneclick import OneClickError, plan_from_spec
from test_web import connect, make, register, wait_for  # noqa: F401
from test_web import fast  # noqa: F401


def with_db():
    return next(n for n, a in catalog().items() if a["spec"] and compose_spec(n).get("databases") and len(compose_spec(n)["apps"]) == 1)


def test_a_spec_becomes_apps_disks_databases_and_generated_secrets():
    plan = plan_from_spec("n8n", "my-flows", ram_of=lambda pid: 1.0)
    (app,) = plan.apps
    assert app.name == "my-flows" and app.platform == "docker" and app.image.startswith("registry.c2.liara.ir/one-click-apps/n8n:") and app.port == 5678
    assert app.disks[0].mount_to == "/home/node/.n8n" and app.min_ram_gb == 1.0
    assert app.env["WEBHOOK_URL"] == "https://my-flows.liara.run" and "$APP_NAME" not in str(app.env)


def test_database_references_and_random_secrets_stay_placeholders_until_deploy():
    name = next(n for n, a in catalog().items() if a["spec"] and "RANDOM_STRING" in a["spec"] and compose_spec(n).get("databases") and len(compose_spec(n)["apps"]) == 1)
    plan = plan_from_spec(name, "x")
    assert plan.databases and any("$DB_" in v for v in plan.apps[0].env.values())
    assert plan.env and all(v.secret and v.source == "generated" for v in plan.env)
    env = substitute_random({"A": "$RANDOM_STRING", "B": "$RANDOM_STRING"})
    assert env["A"] != env["B"] and len(env["A"]) >= 24 and "$" not in env["A"]


def test_multi_app_specs_name_each_app_and_resolve_cross_references():
    name = next(n for n, a in catalog().items() if a["spec"] and len(compose_spec(n)["apps"]) > 1)
    plan = plan_from_spec(name, "proj")
    assert len({a.name for a in plan.apps}) == len(plan.apps) and all(a.name.startswith("proj-") for a in plan.apps)
    assert "$APP_" not in str([a.env for a in plan.apps]).replace("$APP_NAME.", "")


def test_vm_only_and_unknown_apps_are_refused():
    vm = next(n for n, a in catalog().items() if not a["spec"])
    with pytest.raises(OneClickError) as e:
        plan_from_spec(vm, "x")
    assert e.value.code == "oneclick_needs_vm"
    with pytest.raises(OneClickError):
        plan_from_spec("no-such-app", "x")


def test_a_fixed_secret_in_a_spec_is_flagged():
    flagged = [n for n, a in catalog().items() if a["spec"] and any(x.code == "oneclick_fixed_secret" for x in plan_from_spec(n, "x").notes)]
    assert flagged  # the infisical spec ships the same ENCRYPTION_KEY to everyone


def test_endpoints_list_the_catalog_and_a_run_plans_from_the_spec(make):
    c, app, _ = make()
    register(c)
    connect(c)
    apps = c.get("/api/oneclick").json()["apps"]
    assert any(a["app"] == "n8n" and not a["needs_vm"] for a in apps) and any(a["needs_vm"] for a in apps)
    assert c.post("/api/projects/oneclick", json={"app": "nope"}).json()["detail"]["code"] == "oneclick_unknown"
    vm = next(a["app"] for a in apps if a["needs_vm"])
    assert c.post("/api/projects/oneclick", json={"app": vm}).json()["detail"]["code"] == "oneclick_needs_vm"
    pid = c.post("/api/projects/oneclick", json={"app": "n8n", "name": "flows"}).json()["id"]
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "preview"}).json()["id"]
    wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed", "cancelled"))
    plan = next(e for e in c.get(f"/api/runs/{rid}/events").json()["events"] if e["kind"] == "plan_ready")["data"]
    assert plan["how_code"] == "from_oneclick" and plan["how_params"] == {"app": "n8n"} and plan["plan"]["apps"][0]["image"].endswith(tuple("0123456789"))


def test_a_real_run_deploys_the_image_with_fresh_secrets(make):
    from test_web import FakeLiara  # noqa: F401
    c, app, deps = make()
    register(c)
    connect(c)
    pid = c.post("/api/projects/oneclick", json={"app": "n8n", "name": "wiki"}).json()["id"]
    rid = c.post(f"/api/projects/{pid}/runs", json={"mode": "deploy"}).json()["id"]
    req = wait_for(c, rid, lambda r: r["status"] == "waiting")["open_requests"][0]
    c.post(f"/api/runs/{rid}/respond", json={"request_id": req["data"]["id"], "value": True})
    done = wait_for(c, rid, lambda r: r["status"] in ("succeeded", "failed"))
    evs = c.get(f"/api/runs/{rid}/events").json()["events"]
    fin = next(e for e in evs if e["kind"] == "run_finished")["data"]
    assert done["status"] == "succeeded", (fin.get("reason"), fin)
