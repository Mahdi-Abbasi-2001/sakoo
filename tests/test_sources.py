import base64
import io
import stat
import zipfile
from pathlib import Path

import pytest

from liara_agent.web import sources
from liara_agent.web.sources import SourceError, clone_git, safe_extract_zip, validate_git_url

HOSTS = {"github.com"}


def make_zip(path: Path, files: dict, symlink: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
        for name, target in (symlink or {}).items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, target)
    return path


def extract(tmp_path, files, **kw):
    z = make_zip(tmp_path / "a.zip", files, kw.pop("symlink", None))
    dest = tmp_path / "out"
    safe_extract_zip(z, dest, max_extracted_bytes=kw.get("max_bytes", 10_000_000), max_files=kw.get("max_files", 100))
    return dest


def test_normal_zip_and_single_top_folder_is_stripped(tmp_path):
    dest = extract(tmp_path, {"proj/package.json": "{}", "proj/src/a.js": "x"})
    assert (dest / "package.json").exists() and (dest / "src" / "a.js").exists() and not (dest / "proj").exists()


def test_zip_slip_is_rejected(tmp_path):
    with pytest.raises(SourceError) as e:
        extract(tmp_path, {"ok.txt": "x", "../../evil.txt": "pwned"})
    assert e.value.code == "path_traversal" and not (tmp_path.parent / "evil.txt").exists()


def test_absolute_paths_cannot_escape(tmp_path):
    with pytest.raises(SourceError):
        extract(tmp_path, {"ok.txt": "x", "/etc/evil": "pwned"})
    assert not Path("/etc/evil").exists()


def test_symlinks_are_rejected(tmp_path):
    with pytest.raises(SourceError) as e:
        extract(tmp_path, {"a.txt": "x"}, symlink={"link": "/etc/passwd"})
    assert e.value.code == "symlink"


def test_zip_bomb_by_real_size_is_stopped(tmp_path):
    big = "0" * 5_000_000
    with pytest.raises(SourceError) as e:
        extract(tmp_path, {"a.bin": big, "b.bin": big}, max_bytes=6_000_000)
    assert e.value.code == "too_large"


def test_too_many_files_and_garbage_and_empty(tmp_path):
    with pytest.raises(SourceError) as e:
        extract(tmp_path, {f"f{i}.txt": "x" for i in range(30)}, max_files=10)
    assert e.value.code == "too_many_files"
    (tmp_path / "bad.zip").write_text("not a zip")
    with pytest.raises(SourceError) as e:
        safe_extract_zip(tmp_path / "bad.zip", tmp_path / "o2", max_extracted_bytes=10, max_files=10)
    assert e.value.code == "bad_zip"


@pytest.mark.parametrize("url,code", [
    ("http://github.com/a/b", "git_scheme"),
    ("git://github.com/a/b", "git_scheme"),
    ("file:///etc/passwd", "git_scheme"),
    ("https://user:pw@github.com/a/b", "git_credentials"),
    ("https://evil.example.com/a/b", "git_host"),
    ("https://localhost/a/b", "git_host"),
    ("https://169.254.169.254/latest", "git_host"),
    ("https://github.com/onlyowner", "git_path"),
    ("https://github.com/a/b;rm -rf", "git_path"),
])
def test_git_urls_that_could_be_abused_are_rejected(url, code):
    with pytest.raises(SourceError) as e:
        validate_git_url(url, HOSTS)
    assert e.value.code == code


def test_valid_git_url_is_normalised():
    assert validate_git_url("https://GitHub.com/owner/repo.git/", HOSTS) == "https://github.com/owner/repo.git"


def test_clone_uses_safe_flags_and_removes_git_dir_and_symlinks(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        dest = Path(cmd[-1])
        (dest / ".git").mkdir(parents=True)
        (dest / ".git" / "config").write_text("x")
        (dest / "app.py").write_text("print(1)")
        (dest / "evil").symlink_to("/etc/passwd")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(sources.subprocess, "run", fake_run)
    dest = tmp_path / "repo"
    clone_git("https://github.com/a/b", dest, allowed_hosts=HOSTS, max_extracted_bytes=1_000_000, max_files=100)
    assert (dest / "app.py").exists() and not (dest / ".git").exists() and not (dest / "evil").exists()
    cmd = " ".join(seen["cmd"])
    assert "--depth 1" in cmd and "--no-recurse-submodules" in cmd and "protocol.allow=never" in cmd and "core.hooksPath=/dev/null" in cmd
    assert seen["env"]["GIT_TERMINAL_PROMPT"] == "0" and "TOKEN" not in " ".join(seen["env"])  # no secrets from our environment leak to git


def test_clone_failure_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(sources.subprocess, "run", lambda cmd, **kw: type("R", (), {"returncode": 128, "stderr": "fatal"})())
    with pytest.raises(SourceError) as e:
        clone_git("https://github.com/a/b", tmp_path / "r", allowed_hosts=HOSTS, max_extracted_bytes=10, max_files=10)
    assert e.value.code == "git_failed" and not (tmp_path / "r").exists()


def test_private_clone_sends_the_token_by_environment_never_on_the_command_line(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        (Path(cmd[-1])).mkdir(parents=True)
        (Path(cmd[-1]) / "a.txt").write_text("x")
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(sources.subprocess, "run", fake_run)
    clone_git("https://github.com/a/b", tmp_path / "r", allowed_hosts=HOSTS, max_extracted_bytes=10_000, max_files=10, token="ghp_secret123456", branch="feature/x")
    assert "ghp_secret123456" not in " ".join(seen["cmd"]) and "--branch feature/x" in " ".join(seen["cmd"])
    assert seen["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraHeader"
    assert base64.b64decode(seen["env"]["GIT_CONFIG_VALUE_0"].split("Basic ")[1]) == b"x-access-token:ghp_secret123456"


@pytest.mark.parametrize("branch", ["-x", "a..b", "a b", "x;rm", "", "a.lock"])
def test_bad_branches_and_tokens_are_refused(tmp_path, branch):
    with pytest.raises(SourceError) as e:
        clone_git("https://github.com/a/b", tmp_path / "r", allowed_hosts=HOSTS, max_extracted_bytes=10, max_files=10, branch=branch or "a b")
    assert e.value.code == "git_branch"
    with pytest.raises(SourceError) as e:
        clone_git("https://github.com/a/b", tmp_path / "r", allowed_hosts=HOSTS, max_extracted_bytes=10, max_files=10, token="bad token;")
    assert e.value.code == "git_token_invalid"
