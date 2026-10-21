from urllib.parse import parse_qs, urlparse

import httpx

from liara_agent.web import github, sources
from test_web import connect, make, register  # noqa: F401
from test_web_branches import fake_clone


class FakeGitHub:
    def __init__(self, login="octo", gid=4242):
        self.calls, self.login, self.gid = [], login, gid

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, str(req.url), req.headers.get("authorization")))
        if str(req.url) == github.OAUTH_TOKEN:
            form = dict(x.split("=") for x in req.content.decode().split("&"))
            return httpx.Response(200, json={"access_token": "gho_oauthtoken123"} if form["code"] == "good" else {"error": "bad_verification_code"})
        if req.url.path == "/user":
            return httpx.Response(200, json={"login": self.login, "id": self.gid})
        if req.url.path == "/user/repos":
            return httpx.Response(200, json=[{"full_name": "octo/secret", "private": True, "default_branch": "dev", "html_url": "https://github.com/octo/secret", "pushed_at": "2026-10-01T00:00:00Z"}])
        return httpx.Response(404, json={})


def setup(make, login="octo", **ws):
    fake = FakeGitHub(login)
    c, app, deps = make(**{"github_client_id": "cid", "github_client_secret": "csecret", **ws})
    deps.github_http = httpx.Client(base_url=github.API, transport=httpx.MockTransport(fake))
    return c, app, fake


def start(c, **q):
    r = c.get("/api/github/login", params=q)
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    return url, parse_qs(urlparse(url).query)["state"][0]


def finish(c, state, code="good"):
    return c.get("/api/github/callback", params={"code": code, "state": state}, follow_redirects=False)


def test_login_is_off_without_an_oauth_app(make):
    c, *_ = make()
    register(c)
    assert c.get("/api/limits").json()["github_oauth"] is False
    assert c.get("/api/github/login").json()["detail"]["code"] == "github_oauth_off"


def test_connecting_asks_for_repo_and_stores_the_token_encrypted(make):
    c, app, fake = setup(make)
    register(c)
    assert c.get("/api/limits").json()["github_oauth"] is True
    url, state = start(c)
    assert url.startswith("https://github.com/login/oauth/authorize?") and "client_id=cid" in url and "scope=repo" in url
    assert finish(c, "forged").headers["location"] == "/login?github_error=github_oauth_state"
    r = finish(c, state)
    assert r.status_code == 302 and r.headers["location"] == "/settings?github=connected"
    assert c.get("/api/settings").json()["github_login"] == "octo"
    assert "gho_oauth" not in app.state.db.one("SELECT github_token_enc FROM settings")["github_token_enc"] and "gho_oauth" not in c.get("/api/settings").text
    assert c.get("/api/github/repos").json()["repos"][0]["full_name"] == "octo/secret"
    assert any(a == "Bearer gho_oauthtoken123" for _, _, a in fake.calls)
    assert c.delete("/api/github/connection").json()["github_login"] is None
    assert c.get("/api/github/repos").json()["detail"]["code"] == "github_not_connected"


def test_connecting_needs_a_session_and_returns_where_it_was_asked_to(make):
    c, app, fake = setup(make)
    assert c.get("/api/github/login").status_code == 401
    register(c)
    url, state = start(c, next="/new")
    assert finish(c, state).headers["location"] == "/new"
    url, state = start(c, next="//evil.example/x")        # only paths on this site
    assert finish(c, state).headers["location"] == "/settings?github=connected"


def test_a_bad_code_goes_back_with_an_error(make):
    c, app, fake = setup(make)
    register(c)
    url, state = start(c)
    assert finish(c, state, code="bad").headers["location"] == "/settings?github_error=github_oauth_failed"


def test_a_connected_account_clones_private_repos_without_a_pasted_token(make, monkeypatch):
    calls = []
    monkeypatch.setattr(sources, "clone_git", fake_clone(calls))
    c, app, fake = setup(make)
    register(c)
    url, state = start(c)
    finish(c, state)
    pid = c.post("/api/projects", data={"name": "p", "git_url": "https://github.com/octo/secret"}).json()["id"]
    assert calls[-1][1] == "gho_oauthtoken123"
    c.post(f"/api/projects/{pid}/branches", json={"branch": "dev"})
    assert calls[-1][1] == "gho_oauthtoken123"
    c.post("/api/projects", data={"name": "q", "git_url": "https://gitlab.com/octo/x"})
    assert calls[-1][1] in (None, "")  # never sent to another host


# ---- sign in with GitHub ----
def test_signing_in_creates_an_account_asks_only_for_identity_and_keeps_no_token(make):
    c, app, fake = setup(make)
    url, state = start(c, mode="signin")
    assert "scope=read%3Auser" in url
    r = finish(c, state)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = c.get("/api/me").json()
    assert me["username"] == "octo"
    row = app.state.db.one("SELECT github_id FROM users WHERE username='octo'")
    assert row["github_id"] == "4242"
    assert app.state.db.one("SELECT COUNT(*) n FROM settings WHERE github_token_enc IS NOT NULL")["n"] == 0   # identity only: nothing to keep
    assert c.post("/api/auth/login", json={"username": "octo", "password": "anything-at-all"}).status_code == 401   # no password anyone knows


def test_signing_in_again_finds_the_same_account_even_if_the_login_changed(make):
    c, app, fake = setup(make)
    start_url, state = start(c, mode="signin")
    finish(c, state)
    c.post("/api/auth/logout")
    fake.login = "renamed"
    url, state = start(c, mode="signin")
    finish(c, state)
    assert c.get("/api/me").json()["username"] == "octo"
    assert app.state.db.one("SELECT COUNT(*) n FROM users")["n"] == 1


def test_a_taken_username_gets_a_suffix(make):
    c, app, fake = setup(make, login="alice")
    register(c, "alice")
    c.post("/api/auth/logout")
    url, state = start(c, mode="signin")
    finish(c, state)
    assert c.get("/api/me").json()["username"].startswith("alice-")


def test_a_server_with_an_invite_code_only_lets_new_github_accounts_in_with_it(make):
    c, app, fake = setup(make, registration_code="open-sesame-1")
    url, state = start(c, mode="signin")
    r = finish(c, state)
    assert r.headers["location"] == "/register?github_error=invite_required"
    assert c.get("/api/me").status_code == 401
    url, state = start(c, mode="signin", invite="wrong")
    assert finish(c, state).headers["location"] == "/register?github_error=invite_required"
    url, state = start(c, mode="signin", invite="open-sesame-1")
    assert finish(c, state).headers["location"] == "/" and c.get("/api/me").json()["username"] == "octo"
    c.post("/api/auth/logout")                                   # an account that already exists signs in without the code
    url, state = start(c, mode="signin")
    assert finish(c, state).headers["location"] == "/" and c.get("/api/me").status_code == 200


def test_the_state_cookie_cannot_be_forged_or_swapped(make):
    c, app, fake = setup(make, registration_code="open-sesame-1")
    url, state = start(c, mode="signin", invite="open-sesame-1")
    c.cookies.set("gh_ctx", "e30.deadbeef", path="/api/github")   # a made-up context
    assert finish(c, state).headers["location"] == "/login?github_error=github_oauth_state"


def test_behind_a_proxy_the_callback_uses_the_address_the_browser_used(make):
    c, app, fake = setup(make)
    register(c)
    r = c.get("/api/github/login", headers={"x-forwarded-proto": "https", "x-forwarded-host": "sakoo.liara.run"})
    assert parse_qs(urlparse(r.json()["url"]).query)["redirect_uri"] == ["https://sakoo.liara.run/api/github/callback"]
    c2, *_ = setup(make, public_url="https://launchpad.example.com/")
    register(c2, "erin")
    assert parse_qs(urlparse(c2.get("/api/github/login").json()["url"]).query)["redirect_uri"] == ["https://launchpad.example.com/api/github/callback"]
