import base64
import json

import httpx
import pytest

from liara_agent.web import github
from test_web import connect, make, register  # noqa: F401
from test_web_branches import fake_clone
from liara_agent.web import sources

PATCH = "--- a/package.json\n+++ b/package.json\n@@ -1 +1,2 @@\n-{}\n+{\"engines\": {}}\n"


def test_repo_and_patch_paths_are_validated(tmp_path):
    assert github.repo_of("https://github.com/a/b.git") == ("a", "b")
    for bad in ("https://gitlab.com/a/b", "https://github.com/a", None):
        with pytest.raises(github.GitHubError) as e:
            github.repo_of(bad)
        assert e.value.code == "pr_not_github"
    (tmp_path / "package.json").write_text("new")
    assert github.changed_files(PATCH, tmp_path) == [{"path": "package.json", "content": "new"}]
    with pytest.raises(github.GitHubError) as e:
        github.changed_files("+++ b/../../etc/passwd\n", tmp_path)
    assert e.value.code == "pr_bad_path"


class FakeGH:
    def __init__(self, write_ok=True):
        self.calls, self.write_ok = [], write_ok

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        self.calls.append((req.method, req.url.path, body))
        p = req.url.path
        assert req.headers["authorization"] == "Bearer ghp_writer12345"
        if req.method != "GET" and not self.write_ok:
            return httpx.Response(403, json={})
        if p.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "abc123"}})
        if p.endswith("/git/refs"):
            return httpx.Response(201, json={})
        if "/contents/" in p and req.method == "GET":
            return httpx.Response(200, json={"sha": "old-sha"})
        if "/contents/" in p:
            return httpx.Response(200, json={})
        if p.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/a/b/pull/7", "number": 7})
        if p == "/repos/a/b":
            return httpx.Response(200, json={"default_branch": "main"})
        return httpx.Response(404, json={})


def project_with_patch(make, monkeypatch, fake):
    monkeypatch.setattr(sources, "clone_git", fake_clone([]))
    c, app, deps = make()
    deps.github_http = httpx.Client(base_url=github.API, transport=httpx.MockTransport(fake))
    register(c)
    pid = c.post("/api/projects", data={"name": "b", "git_url": "https://github.com/a/b"}).json()["id"]
    state = app.state.settings.data_dir / "projects" / str(pid) / "src" / ".agent-state"
    (state / "patched" / "b-web").mkdir(parents=True)
    (state / "fixes.patch").write_text(PATCH)
    (state / "patched" / "b-web" / "package.json").write_text('{"engines": {}}\n')
    return c, pid


def test_pull_request_goes_to_a_new_branch_and_needs_confirmation_and_a_token(make, monkeypatch):
    fake = FakeGH()
    c, pid = project_with_patch(make, monkeypatch, fake)
    prev = c.get(f"/api/projects/{pid}/pr").json()
    assert prev["repo"] == "a/b" and prev["files"] == [{"path": "package.json"}] and "engines" in prev["diff"]
    assert c.post(f"/api/projects/{pid}/pr", json={}).json()["detail"]["code"] == "confirm_required"
    assert c.post(f"/api/projects/{pid}/pr", json={"confirm": True}).json()["detail"]["code"] == "pr_needs_token"
    assert fake.calls == []  # nothing reached GitHub before a token and a confirmation
    r = c.post(f"/api/projects/{pid}/pr", json={"confirm": True, "token": "ghp_writer12345"})
    assert r.status_code == 201 and r.json()["url"].endswith("/pull/7") and r.json()["branch"].startswith("liara-agent/")
    methods = [(m, p.split("/repos/a/b")[1]) for m, p, _ in fake.calls]
    assert ("POST", "/git/refs") in methods and ("PUT", "/contents/package.json") in methods and ("POST", "/pulls") in methods
    put = next(b for m, p, b in fake.calls if m == "PUT")
    assert put["branch"].startswith("liara-agent/") and put["sha"] == "old-sha" and base64.b64decode(put["content"]) == b'{"engines": {}}\n'
    assert not any(b and b.get("ref") == "refs/heads/main" for _, _, b in fake.calls)  # the person's branch is never written to


def test_a_token_that_cannot_write_is_explained(make, monkeypatch):
    c, pid = project_with_patch(make, monkeypatch, FakeGH(write_ok=False))
    r = c.post(f"/api/projects/{pid}/pr", json={"confirm": True, "token": "ghp_writer12345"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "github_no_write"


def test_nothing_to_propose_without_a_patch(make, monkeypatch):
    monkeypatch.setattr(sources, "clone_git", fake_clone([]))
    c, *_ = make()
    register(c)
    pid = c.post("/api/projects", data={"name": "b", "git_url": "https://github.com/a/b"}).json()["id"]
    assert c.get(f"/api/projects/{pid}/pr").json()["detail"]["code"] == "pr_no_patch"
