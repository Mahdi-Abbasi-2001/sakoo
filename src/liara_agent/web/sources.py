"""Turn untrusted input (a zip upload or a Git URL) into a project folder, defensively.

Nothing here executes uploaded code: the agent only reads files and Liara builds them.
"""
from __future__ import annotations

import base64
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlparse


class SourceError(ValueError):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.params = code, params


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:40] or "project"


def _tree_size(root: Path) -> tuple[int, int]:
    total = files = 0
    for dirpath, _, names in os.walk(root):
        for n in names:
            p = Path(dirpath) / n
            if not p.is_symlink():
                total += p.stat().st_size
                files += 1
    return total, files


def safe_extract_zip(zip_path: Path, dest: Path, *, max_extracted_bytes: int, max_files: int) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise SourceError("bad_zip", "the file is not a valid zip archive")
    with zf:
        infos = [i for i in zf.infolist() if not i.filename.startswith("__MACOSX/")]
        if len(infos) > max_files:
            raise SourceError("too_many_files", f"the archive has more than {max_files} files", limit=max_files)
        if sum(i.file_size for i in infos) > max_extracted_bytes:
            raise SourceError("too_large", "the archive is too large once extracted", limit_mb=max_extracted_bytes // 1048576)
        names = [i.filename for i in infos if not i.is_dir()]
        # a single top-level folder (the usual "zip the project folder") is stripped
        tops = {n.split("/", 1)[0] for n in names}
        strip = next(iter(tops)) + "/" if len(tops) == 1 and all("/" in n for n in names) else ""
        written = 0
        for info in infos:
            name = info.filename.replace("\\", "/")
            if strip and name.startswith(strip):
                name = name[len(strip):]
            if not name or name.endswith("/"):
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise SourceError("symlink", f"{info.filename} is a symbolic link; links are not allowed")
            target = (root / name).resolve()
            if root != target and root not in target.parents:
                raise SourceError("path_traversal", f"{info.filename} would be written outside the project")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:  # count real bytes: the declared size can lie
                while chunk := src.read(65536):
                    written += len(chunk)
                    if written > max_extracted_bytes:
                        raise SourceError("too_large", "the archive is too large once extracted", limit_mb=max_extracted_bytes // 1048576)
                    out.write(chunk)
    if not any(root.iterdir()):
        raise SourceError("empty", "the archive contains no files")


def validate_git_url(url: str, allowed_hosts: set[str]) -> str:
    u = urlparse(url.strip())
    if u.scheme != "https":
        raise SourceError("git_scheme", "only https Git URLs are accepted")
    if u.username or u.password:
        raise SourceError("git_credentials", "do not put credentials in the URL; only public repositories are supported")
    if u.params or u.query or u.fragment:
        raise SourceError("git_path", "expected a plain repository URL without parameters, query or fragment")
    host = (u.hostname or "").lower()
    if host not in allowed_hosts:
        raise SourceError("git_host", f"Git host {host!r} is not allowed; allowed: {', '.join(sorted(allowed_hosts))}", host=host, allowed=sorted(allowed_hosts))
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2 or not all(re.fullmatch(r"[\w.\-]+", p) for p in parts[:2]):
        raise SourceError("git_path", "expected a URL like https://github.com/owner/repo")
    return f"https://{host}/{parts[0]}/{parts[1].removesuffix('.git')}.git"


BRANCH = re.compile(r"[A-Za-z0-9][\w./-]{0,99}")
TOKEN = re.compile(r"[\w.\-~+/=]{8,300}")
TOKEN_USER = {"github.com": "x-access-token", "gitlab.com": "oauth2", "bitbucket.org": "x-token-auth"}  # the user name each host expects with an access token


def validate_branch(branch: str) -> str:
    b = branch.strip()
    if not BRANCH.fullmatch(b) or ".." in b or b.endswith((".lock", "/", ".")):
        raise SourceError("git_branch", "that is not a valid branch name")
    return b


def clone_git(url: str, dest: Path, *, allowed_hosts: set[str], max_extracted_bytes: int, max_files: int, timeout: float = 90, token: str = "", branch: str = "") -> None:
    clean = validate_git_url(url, allowed_hosts)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(dest.parent), "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_ASKPASS": "true"}
    if token:  # sent as a header through the environment: it is in neither the command line (visible to `ps`) nor the stored URL
        if not TOKEN.fullmatch(token):
            raise SourceError("git_token_invalid", "that does not look like an access token")
        host = urlparse(clean).hostname or ""
        basic = base64.b64encode(f"{TOKEN_USER.get(host, 'x-access-token')}:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"http.https://{host}/.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}"})
    cmd = ["git", "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "core.symlinks=false", "-c", "core.hooksPath=/dev/null",
           "clone", "--depth", "1", "--single-branch", "--no-tags", "--no-recurse-submodules", *(["--branch", validate_branch(branch)] if branch else []), clean, str(dest)]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError("git_timeout", "cloning took too long")
    except FileNotFoundError:
        raise SourceError("git_missing", "git is not installed on the server")
    if r.returncode != 0:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError("git_failed", "could not clone the repository (is it public, or is the access token right?)", private=bool(token))
    shutil.rmtree(dest / ".git", ignore_errors=True)
    total, files = _tree_size(dest)
    if total > max_extracted_bytes or files > max_files:
        shutil.rmtree(dest, ignore_errors=True)
        raise SourceError("too_large", "the repository is too large", limit_mb=max_extracted_bytes // 1048576)
    for p in dest.rglob("*"):  # symlinks are removed outright: they could point outside the project
        if p.is_symlink():
            p.unlink()


def apply_saved_fixes(dest: Path, patch_file: Path) -> str | None:
    """Put the agent's own fixes (the patch it saved when it had to change the project to make it run) back onto a fresh download of the repository.
    Returns 'applied', 'already' (the repository has them: merged pull request), 'conflict' (the repository changed those lines: left as it is) or None (no patch)."""
    if not patch_file.is_file():
        return None

    def git(*args: str) -> bool:
        return subprocess.run(["git", "apply", *args, str(patch_file.resolve())], cwd=dest, capture_output=True, timeout=30).returncode == 0
    if git("--check", "--reverse"):
        return "already"
    if git("--check"):
        return "applied" if git() else "conflict"
    return "conflict"
