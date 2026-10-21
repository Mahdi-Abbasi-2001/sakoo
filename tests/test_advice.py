import json

from liara_agent.deploy.advice import add_advice, vm_signals
from liara_agent.detect.fingerprints import fingerprint
from liara_agent.liara.oneclick import catalog
from liara_agent.plan.model import DeployPlan


def codes(plan):
    return {n.code: n.params for n in plan.notes}


def test_openai_sdk_gets_the_liara_ai_note(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "4", "openai": "4"}}))
    plan = DeployPlan()
    add_advice(plan, tmp_path, {(h.kind, h.value) for h in fingerprint(tmp_path).hints})
    assert "liara_ai_available" in codes(plan) and "ai.liara.ir" in codes(plan)["liara_ai_available"]["base_url"]


def test_python_openai_is_recognised_too(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\nopenai==1.2\n")
    assert ("service", "openai") in {(h.kind, h.value) for h in fingerprint(tmp_path).hints}


def test_same_upstream_repo_suggests_the_ready_made_app_but_a_name_alone_does_not(tmp_path):
    app = next(n for n, a in catalog().items() if a["github"] and a["spec"])
    (tmp_path / "package.json").write_text(json.dumps({"name": "unrelated", "repository": f"github:{catalog()[app]['github']}"}))
    plan = DeployPlan()
    add_advice(plan, tmp_path, set())
    assert codes(plan)["oneclick_available"]["app"] == app
    (tmp_path / "package.json").write_text(json.dumps({"name": app}))
    plan = DeployPlan()
    add_advice(plan, tmp_path, set())
    assert "oneclick_available" not in codes(plan)


def test_vm_signals(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  vpn:\n    image: x\n    network_mode: host\n    privileged: true\n    ports: ['1194:1194/udp']\n")
    assert vm_signals(tmp_path) == ["host_network", "privileged", "udp_ports"]
    plan = DeployPlan()
    add_advice(plan, tmp_path, set())
    assert codes(plan)["vm_may_be_needed"]["signals"] == ["host_network", "privileged", "udp_ports"]


def test_an_ordinary_project_gets_no_advice(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services:\n  web:\n    build: .\n    ports: ['3000:3000']\n")
    plan = DeployPlan()
    add_advice(plan, tmp_path, set())
    assert plan.notes == []
