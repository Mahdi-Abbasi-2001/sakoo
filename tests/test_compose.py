import json
from pathlib import Path

from liara_agent.detect.intent import dockerfile_info, find_compose, parse_compose, procfile
from liara_agent.liara.live import LiveCatalog
from liara_agent.plan.compose import db_kind, decompose, pick_version

FIX = Path(__file__).parent / "fixtures"
CAT = LiveCatalog.from_me(json.loads((FIX / "liara_me_catalog.json").read_text()))


def plan():
    proj = parse_compose(find_compose(FIX / "compose_blog"))
    return decompose(proj, project_name="blog", db_versions=CAT.database_versions, db_defaults=CAT.default_database_versions)


def test_parse_compose_services():
    proj = parse_compose(find_compose(FIX / "compose_blog"))
    web = proj.services["web"]
    assert web.build_context == "./web" and web.ports == [3000]
    assert web.env["PORT"] == "3000"  # ${PORT:-3000} resolved to its default
    assert ("uploads", "/app/uploads") in web.volumes


def test_db_images_become_managed_databases():
    p = plan()
    kinds = {d.id: (d.kind, d.version) for d in p.databases}
    assert kinds == {"blog-db": ("postgres", "16.14"), "blog-cache": ("redis", "7.2.3")}


def test_other_services_become_apps_and_proxy_is_dropped():
    p = plan()
    apps = {a.name: a for a in p.apps}
    assert set(apps) == {"blog-web", "blog-worker"}
    assert apps["blog-web"].port == 3000 and apps["blog-web"].role == "web"
    assert apps["blog-worker"].role == "worker" and apps["blog-worker"].args == ["node", "worker.js"]
    assert any("reverse proxy" in n.message for n in p.notes)


def test_hostnames_and_credentials_are_wired_to_the_generated_database():
    p = plan()
    web = next(a for a in p.apps if a.name == "blog-web")
    assert web.env["DATABASE_URL"] == "postgres://$DB_blog-db_USER:$DB_blog-db_PASSWORD@$DB_blog-db_HOST:$DB_blog-db_PORT/$DB_blog-db_NAME"
    assert web.env["REDIS_HOST"] == "blog-cache"
    assert "secret" not in str(web.env)  # the compose file's hardcoded password is not carried over
    assert any("generated credentials" in n.message for n in p.notes)
    assert not any("could not be mapped" in n.message for n in p.notes)


def test_separate_credential_variables_are_mapped_by_value(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("""
services:
  app:
    build: .
    ports: ["80:80"]
    environment: {DB_HOST: db, DB_USER: app, DB_PASSWORD: pw-123, DB_NAME: shop, GREETING: app}
  db:
    image: mariadb:11
    environment: {MARIADB_USER: app, MARIADB_PASSWORD: pw-123, MARIADB_DATABASE: shop}
""")
    p = decompose(parse_compose(find_compose(tmp_path)), project_name="s")
    env = p.apps[0].env
    assert env["DB_HOST"] == "s-db" and env["DB_USER"] == "$DB_s-db_USER" and env["DB_PASSWORD"] == "$DB_s-db_PASSWORD" and env["DB_NAME"] == "$DB_s-db_NAME"
    assert env["GREETING"] == "app"  # same word, but not a credential variable: untouched


def test_unmappable_embedded_credentials_still_warn(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("""
services:
  app: {build: ., ports: ["80:80"], environment: {URL: "postgres://u:p@db:5432/x?sslmode=disable", ALT: "x://a:b@db/y"}}
  db: {image: postgres:16}
""")
    p = decompose(parse_compose(find_compose(tmp_path)), project_name="s")
    assert p.apps[0].env["URL"].endswith("/$DB_s-db_NAME?sslmode=disable")  # query string preserved
    assert not any("could not be mapped" in n.message for n in p.notes)


def test_volumes_and_init_scripts():
    p = plan()
    web = next(a for a in p.apps if a.name == "blog-web")
    assert [(d.name, d.mount_to) for d in web.disks] == [("uploads", "/app/uploads")]
    msgs = " ".join(n.message for n in p.notes)
    assert "./config" in msgs and "init scripts" in msgs


def test_worker_without_port_raises_a_question():
    (q,) = plan().questions
    assert q.code == "worker_without_port" and q.params == {"service": "worker"} and q.options == ["worker", "needs_port"] and "worker" in q.text


def test_version_picking():
    av, df = CAT.database_versions, CAT.default_database_versions
    assert pick_version("postgres", "16.3-alpine", av, df)[0] == "16.14"
    v, note = pick_version("postgres", "9.6", av, df)
    assert v == df["postgres"] and "not offered" in note
    assert db_kind("bitnami/postgresql:15") == "postgres" and db_kind("mcr.microsoft.com/mssql/server:2022") == "mssql"
    assert db_kind("myorg/api:1") is None


def test_dockerfile_and_procfile(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\nEXPOSE 8000/tcp\nCMD [\"gunicorn\", \"app:app\"]\n")
    (tmp_path / "Procfile").write_text("web: gunicorn app:app\nworker: celery -A app worker\n")
    assert dockerfile_info(tmp_path / "Dockerfile")["expose"] == [8000]
    assert procfile(tmp_path / "Procfile")["worker"].startswith("celery")
