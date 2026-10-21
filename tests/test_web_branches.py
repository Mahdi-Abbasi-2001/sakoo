from liara_agent.web import sources
from test_web import connect, make, register  # noqa: F401


def fake_clone(calls):
    def go(url, dest, **kw):
        calls.append((url, kw.get("token"), kw.get("branch")))
        dest.mkdir(parents=True)
        (dest / "Dockerfile").write_text("FROM python:3.12\nEXPOSE 3000\n")
    return go


def test_private_project_stores_the_token_encrypted_and_never_returns_it(make, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "clone_git", fake_clone(calls))
    c, app, _ = make()
    register(c)
    r = c.post("/api/projects", data={"name": "blog", "git_url": "https://github.com/a/blog", "git_token": "ghp_secret123456"})
    assert r.status_code == 201 and calls[0][1] == "ghp_secret123456"
    pid = r.json()["id"]
    row = app.state.db.one("SELECT git_token_enc FROM projects WHERE id=?", (pid,))
    assert row["git_token_enc"] and "ghp_secret" not in row["git_token_enc"]
    view = c.get(f"/api/projects/{pid}").json()
    assert view["private"] is True and "ghp_secret" not in c.get(f"/api/projects/{pid}").text


def test_a_branch_becomes_its_own_project_reusing_the_token(make, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "clone_git", fake_clone(calls))
    c, app, _ = make()
    register(c)
    pid = c.post("/api/projects", data={"name": "blog", "git_url": "https://github.com/a/blog", "git_token": "ghp_secret123456"}).json()["id"]
    r = c.post(f"/api/projects/{pid}/branches", json={"branch": "feature/login"})
    assert r.status_code == 201 and r.json()["name"] == "blog-feature-login"
    assert calls[-1] == ("https://github.com/a/blog", "ghp_secret123456", "feature/login")
    again = c.post(f"/api/projects/{r.json()['id']}/branches", json={"branch": "fix"}).json()
    assert again["name"] == "blog-fix"           # not blog-feature-login-fix
    assert c.post(f"/api/projects/{pid}/branches", json={"branch": "-bad"}).json()["detail"]["code"] == "git_branch"


def test_only_git_projects_have_branches(make):
    from test_web import new_project
    c, *_ = make()
    register(c)
    pid = new_project(c)
    assert c.post(f"/api/projects/{pid}/branches", json={"branch": "x"}).json()["detail"]["code"] == "not_a_git_project"
