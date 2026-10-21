import json
from pathlib import Path

import httpx
import pytest

from liara_agent.liara.archive import build_archive
from liara_agent.liara.client import Journal, LiaraClient, NotOwnedError, redact


def make(dry_run=True, journal=None):
    sent = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.method, req.url.path, req.content))
        if req.url.path == "/v1/databases":
            return httpx.Response(200, json={"databaseID": "db-123"})
        return httpx.Response(200, json={})

    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(handler))
    return LiaraClient("tok", dry_run=dry_run, journal=journal or Journal(), http=http), sent


def test_dry_run_sends_nothing_and_records_everything():
    c, sent = make()
    c.create_app("blog-web", "docker", "small-g2", "basic", "net1")
    c.create_database("blog-db", "postgres", "16.14", "small-g2", "basic", "net1")
    c.set_env("blog-web", {"DATABASE_URL": "x", "JWT_SECRET": "supersecret"})
    assert sent == []
    assert [a.op for a in c.actions] == ["create_app", "create_database", "set_env"]
    assert not any(a.sent for a in c.actions)
    assert "supersecret" not in json.dumps([a.body for a in c.actions])


def test_real_create_sends_the_bodies_the_cli_sends_and_tracks_ownership():
    c, sent = make(dry_run=False)
    c.create_app("blog-web", "docker", "small-g2", "basic", "net1")
    db = c.create_database("blog-db", "postgres", "16.14", "small-g2", "basic", "net1")
    body = json.loads(sent[0][2])
    assert body == {"name": "blog-web", "planID": "small-g2", "platform": "docker", "bundlePlanID": "basic", "network": "net1"}
    assert json.loads(sent[1][2])["publicNetwork"] is False  # private by default
    assert db == "db-123" and c.journal.apps == ["blog-web"] and c.journal.databases == ["db-123"]


def test_refuses_to_touch_resources_it_did_not_create():
    c, sent = make(dry_run=False)
    for call in (lambda: c.delete_app("botyar"), lambda: c.delete_database("someone-elses"),
                 lambda: c.set_env("botyar", {"A": "b"})):
        with pytest.raises(NotOwnedError):
            call()
    assert sent == []


def test_env_redaction():
    assert redact({"API_KEY": "k", "DB_PASSWORD": "p", "PORT": "80"}) == {"API_KEY": "***", "DB_PASSWORD": "***", "PORT": "80"}


def test_journal_persists(tmp_path):
    j = Journal.load(tmp_path / "state.json")
    c, _ = make(dry_run=False, journal=j)
    c.create_app("a1", "node", "small-g2", "basic", None)
    assert Journal.load(tmp_path / "state.json").apps == ["a1"]


def test_archive_excludes_secrets_and_junk(tmp_path):
    (tmp_path / "app.js").write_text("x")
    (tmp_path / ".env").write_text("SECRET=1")
    (tmp_path / ".env.example").write_text("SECRET=")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "m.js").write_text("x")
    (tmp_path / ".gitignore").write_text("dist/\n")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "o.js").write_text("x")
    import tarfile
    a = build_archive(tmp_path, dest_dir=tmp_path.parent)
    names = tarfile.open(a.path).getnames()
    assert sorted(names) == [".env.example", ".gitignore", "app.js"] and a.excluded_secrets == [".env"]
    with pytest.raises(ValueError):
        build_archive(tmp_path, max_mb=0.000001, dest_dir=tmp_path.parent)


def test_runtime_log_lines_are_unwrapped_and_sorted():
    payload = {"data": [{"metaData": {"releaseId": "r"}, "values": [
        ["20", '{"type":"stdout","_entry":"second line"}'], ["10", '{"type":"stdout","_entry":"Serving HTTP on 0.0.0.0 port 8080"}'], ["30", "plain text"]]}]}
    http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    c = LiaraClient("tok", http=http)
    assert c.runtime_logs("a") == ["Serving HTTP on 0.0.0.0 port 8080", "second line", "plain text"]


def test_journal_remembers_database_names_for_the_ui(tmp_path):
    j = Journal.load(tmp_path / "s.json")
    c, _ = make(dry_run=False, journal=j)
    db = c.create_database("blog-db", "postgres", "16.14", "small-g2", "basic", None)
    again = Journal.load(tmp_path / "s.json")
    assert again.databases == [db] and again.database_names == {db: "blog-db"}


def test_an_app_without_a_private_network_leaves_the_field_out():
    c, sent = make(dry_run=False)
    c.create_app("solo", "docker", "small-g2", "basic", None)
    assert "network" not in json.loads(sent[0][2])   # Liara answers 400 to `"network": null`
