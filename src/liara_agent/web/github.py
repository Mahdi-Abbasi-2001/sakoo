"""GitHub, through its REST API and a token the person gave: reading their repositories and opening a pull request with the changes the agent had to make
to get the project to build (so the next deploy from their own repository does not bring the problem back).

Writes go to a NEW branch and end in a pull request: the person's branches are never changed, and nothing is merged."""
from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

API = "https://api.github.com"
SLUG = re.compile(r"[\w.\-]+")


class GitHubError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, **params):
        super().__init__(message)
        self.code, self.status, self.params = code, status, params


def repo_of(git_url: str | None) -> tuple[str, str]:
    u = urlparse(git_url or "")
    parts = [p for p in u.path.split("/") if p]
    if (u.hostname or "").lower() != "github.com" or len(parts) < 2 or not all(SLUG.fullmatch(p) for p in parts[:2]):
        raise GitHubError("pr_not_github", "pull requests can only be opened on GitHub repositories")
    return parts[0], parts[1].removesuffix(".git")


def changed_files(patch: str, patched_root: Path) -> list[dict]:
    """Files named in a unified diff, with their new content taken from the patched copy. Paths are checked: a patch must not name a path outside the project."""
    out: list[dict] = []
    for m in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.M):
        rel = m.group(1).strip()
        parts = Path(rel).parts
        if rel.startswith("/") or ".." in parts or ".git" in parts:
            raise GitHubError("pr_bad_path", "the patch names a path outside the project", path=rel)
        f = patched_root / rel
        if f.is_file() and not any(x["path"] == rel for x in out):
            out.append({"path": rel, "content": f.read_text(errors="ignore")})
    return out


class GitHub:
    def __init__(self, token: str, http: httpx.Client | None = None):
        self.http = http or httpx.Client(base_url=API, timeout=20)
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, path, headers=self.headers, **kw)
        if r.status_code == 401:
            raise GitHubError("github_token_rejected", "GitHub did not accept the access token", 400)
        if r.status_code in (403, 404) and method != "GET":
            raise GitHubError("github_no_write", "the access token cannot write to this repository (it needs the repo scope, or Contents and Pull requests write access)", 403)
        if r.status_code == 404:
            raise GitHubError("github_not_found", "GitHub does not show that repository to this token", 404)
        if r.status_code >= 400:
            raise GitHubError("github_rejected", f"GitHub answered HTTP {r.status_code}", 502, status=r.status_code)
        return r.json() if r.content else {}

    def default_branch(self, owner: str, repo: str) -> str:
        return self._req("GET", f"/repos/{owner}/{repo}")["default_branch"]

    def open_pull_request(self, owner: str, repo: str, base: str, files: list[dict], title: str, body: str) -> dict:
        sha = self._req("GET", f"/repos/{owner}/{repo}/git/ref/heads/{quote(base, safe='/')}")["object"]["sha"]
        branch = f"liara-agent/fix-{int(time.time())}"
        self._req("POST", f"/repos/{owner}/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha})
        for f in files:
            path = quote(f["path"], safe="/")
            existing = self.http.request("GET", f"/repos/{owner}/{repo}/contents/{path}", headers=self.headers, params={"ref": branch})
            payload = {"message": f"Fix {f['path']} so the project builds on Liara", "branch": branch, "content": base64.b64encode(f["content"].encode()).decode()}
            if existing.status_code == 200:
                payload["sha"] = existing.json()["sha"]
            self._req("PUT", f"/repos/{owner}/{repo}/contents/{path}", json=payload)
        pr = self._req("POST", f"/repos/{owner}/{repo}/pulls", json={"title": title, "head": branch, "base": base, "body": body})
        return {"url": pr["html_url"], "number": pr["number"], "branch": branch}


OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
OAUTH_TOKEN = "https://github.com/login/oauth/access_token"
SCOPE = "repo"  # read private repositories to download them, and write a branch + pull request for the fixes (asked only when the person connects repositories)
SCOPE_SIGNIN = "read:user"  # signing in asks for nothing but who the person is


def authorize_url(client_id: str, state: str, redirect_uri: str, scope: str = SCOPE) -> str:
    from urllib.parse import urlencode
    return OAUTH_AUTHORIZE + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "scope": scope, "state": state, "allow_signup": "true"})


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, http: httpx.Client | None = None) -> str:
    """The access token GitHub gives for an authorization code. The code is single-use and only valid with this app's secret."""
    http = http or httpx.Client(timeout=20)
    r = http.post(OAUTH_TOKEN, headers={"Accept": "application/json"}, data={"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri})
    data = r.json() if r.content else {}
    if r.status_code != 200 or not data.get("access_token"):
        raise GitHubError("github_oauth_failed", "GitHub did not give an access token for that sign-in", 400)
    return data["access_token"]


def list_repos(token: str, http: httpx.Client | None = None, limit: int = 100) -> list[dict]:
    """The repositories the person can read, most recently pushed first."""
    gh = GitHub(token, http)
    r = gh.http.get("/user/repos", headers=gh.headers, params={"sort": "pushed", "per_page": min(limit, 100), "affiliation": "owner,collaborator,organization_member"})
    if r.status_code == 401:
        raise GitHubError("github_token_rejected", "GitHub did not accept the access token", 400)
    if r.status_code >= 400:
        raise GitHubError("github_rejected", f"GitHub answered HTTP {r.status_code}", 502, status=r.status_code)
    return [{"full_name": x["full_name"], "private": bool(x["private"]), "default_branch": x.get("default_branch") or "main", "url": x["html_url"],
             "pushed_at": x.get("pushed_at")} for x in r.json()]
