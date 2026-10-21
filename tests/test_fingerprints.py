import json
from pathlib import Path

from liara_agent.detect.fingerprints import fingerprint
from liara_agent.plan.model import AppSpec, DeployPlan
import pytest

FIX = Path(__file__).parent / "fixtures"


def values(fp, kind):
    return {h.value for h in fp.hints if h.kind == kind}


def test_express_pg_redis():
    fp = fingerprint(FIX / "express_pg")
    assert "node" in values(fp, "platform")
    assert {"postgres", "redis"} <= values(fp, "db")
    assert {"DATABASE_URL", "JWT_SECRET", "PORT"} <= set(fp.env_vars)
    assert fp.env_vars["JWT_SECRET"].line == 4


def test_django():
    fp = fingerprint(FIX / "django_app")
    assert "django" in values(fp, "platform")
    assert "postgres" in values(fp, "db")


def test_plan_rejects_unknown_platform():
    with pytest.raises(ValueError):
        DeployPlan(apps=[AppSpec(name="x", platform="cobol")])


def test_storage_and_mail_libraries_are_recognised_in_node_python_and_php(tmp_path):
    import json
    from liara_agent.detect.fingerprints import fingerprint
    (tmp_path / "n").mkdir()
    (tmp_path / "n" / "package.json").write_text(json.dumps({"dependencies": {"multer-s3": "1", "@nestjs-modules/mailer": "1", "aws-sdk": "2"}}))
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "requirements.txt").write_text("django-storages==1.14\nflask-mail\n")
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / "composer.json").write_text(json.dumps({"require": {"league/flysystem-aws-s3-v3": "^3", "phpmailer/phpmailer": "^6"}}))
    got = {(h.path, h.value) for h in fingerprint(tmp_path).hints if h.kind == "service"}
    assert got == {("n", "object-storage"), ("n", "mail"), ("p", "object-storage"), ("p", "mail"), ("h", "object-storage"), ("h", "mail")}
    # the generic aws-sdk alone says nothing about S3
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "package.json").write_text(json.dumps({"dependencies": {"aws-sdk": "2"}}))
    assert not [h for h in fingerprint(tmp_path / "x").hints if h.kind == "service"]


def test_the_agents_own_patched_copies_are_not_part_of_the_project(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    copy = tmp_path / ".agent-state" / "patched" / "app"
    copy.mkdir(parents=True)
    (copy / "package.json").write_text('{"name": "copy"}')
    (copy / "Dockerfile").write_text("FROM node:20\n")
    found = fingerprint(tmp_path)
    assert "package.json" not in json.dumps(found, default=str) and "Dockerfile" not in json.dumps(found, default=str)
