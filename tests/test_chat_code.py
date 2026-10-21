import pytest

from liara_agent.chat.engine import ToolError
from liara_agent.web import chat_code as cc


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "src"
    (r / "app").mkdir(parents=True)
    (r / "app" / "main.py").write_text("import os\nprint('hi')\nPORT = 3000\n")
    (r / ".env").write_text("SECRET=1\n")
    (r / "node_modules").mkdir()
    (r / "node_modules" / "x.js").write_text("x")
    return r


def test_listing_skips_dependencies_and_secret_files(root):
    assert cc.list_files(root)["files"] == ["app/main.py"]


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", ".env", "app/../../x", ".git/config", ".agent-state/fixes.patch", "a/.env.local", "key.pem"])
def test_paths_outside_the_code_are_refused(root, bad):
    with pytest.raises(ToolError):
        cc.read_file(root, bad)
    with pytest.raises(ToolError):
        cc.plan_changes(root, [{"path": bad, "content": "x"}])


def test_a_link_is_never_followed(root, tmp_path):
    (tmp_path / "outside.txt").write_text("private")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ToolError):
        cc.read_file(root, "link.txt")


def test_read_is_chunked_and_search_finds_lines(root):
    (root / "big.txt").write_text("\n".join(f"line {i} " + "x" * 60 for i in range(400)))
    out = cc.read_file(root, "big.txt")
    assert out["next_start"] > 1 and len(out["text"]) <= cc.READ_CHARS + 100 and out["total_lines"] == 400
    assert cc.read_file(root, "big.txt", out["next_start"])["from_line"] == out["next_start"]
    assert cc.search(root, "PORT =")["hits"][0] == {"path": "app/main.py", "line": 3, "text": "PORT = 3000"}


def test_plan_replace_write_delete_and_the_diff(root):
    plan = cc.plan_changes(root, [{"path": "app/main.py", "replace": [{"find": "PORT = 3000", "with": "PORT = 8080"}]},
                                  {"path": "Dockerfile", "content": "FROM python:3.12\n"}])
    assert {f["path"]: f["status"] for f in plan["files"]} == {"app/main.py": "changed", "Dockerfile": "new"}
    assert "-PORT = 3000" in plan["diff"] and "+PORT = 8080" in plan["diff"]
    assert (root / "app" / "main.py").read_text().count("3000") == 1     # planning wrote nothing


def test_replace_must_match_exactly_once(root):
    with pytest.raises(ToolError, match="not found"):
        cc.plan_changes(root, [{"path": "app/main.py", "replace": [{"find": "nope", "with": "x"}]}])
    (root / "app" / "two.py").write_text("a\na\n")
    with pytest.raises(ToolError, match="2 times"):
        cc.plan_changes(root, [{"path": "app/two.py", "replace": [{"find": "a", "with": "b"}]}])


def test_a_key_in_new_code_is_refused_but_reading_an_env_var_is_fine(root):
    with pytest.raises(ToolError, match="secret"):
        cc.plan_changes(root, [{"path": "a.py", "content": "KEY = 'sk-abcdefghijklmnop123456'"}])
    cc.plan_changes(root, [{"path": "a.py", "content": "password = os.environ['DB_PASSWORD']\n"}])


def test_apply_then_undo_restores_everything(root, tmp_path):
    plan = cc.plan_changes(root, [{"path": "app/main.py", "content": "print('new')\n"}, {"path": "new.txt", "content": "n"}])
    cc.apply(root, plan, tmp_path / "bak")
    assert (root / "new.txt").is_file() and "new" in (root / "app" / "main.py").read_text()
    cc.undo(root, tmp_path / "bak")
    assert not (root / "new.txt").exists() and "PORT = 3000" in (root / "app" / "main.py").read_text()
    with pytest.raises(ToolError):
        cc.undo(root, tmp_path / "bak")


def test_limits(root):
    with pytest.raises(ToolError, match="at most"):
        cc.plan_changes(root, [{"path": f"f{i}.txt", "content": "x"} for i in range(31)])
    with pytest.raises(ToolError, match="larger"):
        cc.plan_changes(root, [{"path": "big.txt", "content": "x" * 300_000}])
    with pytest.raises(ToolError, match="not change anything"):
        cc.plan_changes(root, [{"path": "app/main.py", "content": (root / "app" / "main.py").read_text()}])
