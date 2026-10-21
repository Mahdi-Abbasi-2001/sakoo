import json

from liara_agent.detect.readiness import check_readiness


def codes(root):
    return [(f.code, f.file, f.line, f.params) for f in check_readiness(root).findings]


def find(root, code):
    return [f for f in check_readiness(root).findings if f.code == code]


def test_detects_committed_secrets_by_location_and_never_includes_the_value(tmp_path):
    (tmp_path / "config.py").write_text("import os\nAWS = 'AKIAABCDEFGHIJKLMNOP'\nOK = os.environ['X']\nDB_PASSWORD = 'hunter2-super-secret'\n")
    (tmp_path / "key.js").write_text("const k = '-----BEGIN RSA PRIVATE KEY-----'\n")
    fs = find(tmp_path, "hardcoded_secret")
    assert {(f.file, f.line, f.params["kind"]) for f in fs} == {("config.py", 2, "aws_access_key"), ("config.py", 4, "assigned_secret"), ("key.js", 1, "private_key")}
    assert "AKIA" not in json.dumps(check_readiness(tmp_path).as_dict()) and "hunter2" not in json.dumps(check_readiness(tmp_path).as_dict())


def test_placeholders_tests_and_examples_are_not_flagged(tmp_path):
    (tmp_path / "settings.py").write_text("SECRET_KEY = 'changeme-in-production-please'\nAPI_KEY = os.environ['API_KEY']\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("PASSWORD = 'a-real-looking-test-password'\n")
    (tmp_path / "README.md").write_text("password = 'abcdefghijklmnop'\n")
    assert find(tmp_path, "hardcoded_secret") == []


def test_env_file_is_reported_by_variable_count_only(tmp_path):
    (tmp_path / ".env").write_text("JWT_SECRET=abcdef123456\nEMPTY=\n# comment=1\nPORT=3000\n")
    (tmp_path / ".env.example").write_text("JWT_SECRET=\n")
    (f,) = find(tmp_path, "env_file_present")
    assert f.file == ".env" and f.params == {"count": 2} and "abcdef" not in json.dumps(f.params)


def test_debug_sqlite_localhost_and_uploads(tmp_path):
    (tmp_path / "settings.py").write_text("DEBUG = True\nALLOWED_HOSTS = ['*']\nDATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3'}}\nMEDIA_ROOT = '/x'\n")
    (tmp_path / "app.py").write_text("app.run(host='127.0.0.1', port=5000, debug=True)\n")
    (tmp_path / "server.js").write_text("app.listen(3000, '127.0.0.1');\nconst up = multer({ dest: 'uploads/' });\n")
    got = {c for c, *_ in codes(tmp_path)}
    assert {"debug_enabled", "allowed_hosts_wildcard", "sqlite_data_loss", "binds_localhost", "writes_local_files"} <= got
    assert next(f for f in find(tmp_path, "debug_enabled") if f.file == "settings.py").line == 1


def test_foreign_service_dependencies_in_code_and_manifests(tmp_path):
    (tmp_path / "package.json").write_text('{"dependencies": {"telegraf": "^4", "openai": "^4", "express": "^4"}}')
    (tmp_path / "bot.py").write_text("import telegram\nfrom openai import OpenAI\n")
    services = {f.params["service"] for f in find(tmp_path, "foreign_service_dependency")}
    assert services == {"telegram", "openai"}


def test_a_clean_project_has_no_findings_and_noise_is_capped(tmp_path):
    (tmp_path / "app.js").write_text("const port = process.env.PORT;\napp.listen(port, '0.0.0.0');\n")
    assert check_readiness(tmp_path).findings == []
    (tmp_path / "many.py").write_text("\n".join("DEBUG = True" for _ in range(50)))
    assert len(find(tmp_path, "debug_enabled")) == 5  # capped, so one bad file cannot drown the report


def test_skips_dependency_folders(tmp_path):
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "i.js").write_text("const a = 'AKIAABCDEFGHIJKLMNOP'\nDEBUG = True\n")
    assert check_readiness(tmp_path).findings == []
