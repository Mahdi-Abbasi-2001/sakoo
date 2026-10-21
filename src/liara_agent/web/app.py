"""HTTP API. Cookie sessions, per-user isolation, encrypted Liara tokens, live event streaming."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from contextlib import asynccontextmanager, contextmanager
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from liara_agent.config import Settings, get_settings
from liara_agent.report import build_report
from liara_agent.explain import ExplainError, explain, log_tail
from liara_agent.chat.engine import ChatBusy, ChatEngine, ToolError
from liara_agent.web import chat_store, chat_tools
from liara_agent import dnscheck
from liara_agent.deploy import history
from liara_agent.deploy import executor as _executor
from liara_agent import domains as dom
from liara_agent.deploy.cleanup import remove_all
from liara_agent.deploy.pipeline import LIARA_STATUS_CODES
from liara_agent.events import Event, scrub
from liara_agent.liara.client import Journal, LiaraAPIError, NotOwnedError
from liara_agent.liara import oneclick
from liara_agent.plan import oneclick as plan_oneclick
from liara_agent.liara import metrics
from liara_agent import analysis, limits as liara_limits, live_diagnosis, registrar
from liara_agent import wpdeliver
from liara_agent.detect.wordpress import looks_like_wordpress
from liara_agent.web import alerts, github, monitor
from liara_agent.web import sources
from liara_agent.web.config import WebSettings
from liara_agent.web import models as m
from liara_agent.web.db import DB
from liara_agent.llm.client import LLMError
from liara_agent.web.runs import RealDeps, RunManager
from liara_agent.web.security import RateLimiter, TokenVault, hash_password, new_session_token, token_digest, verify_password

SESSION_COOKIE = "session"
USERNAME = re.compile(r"^[a-z0-9_.-]{3,32}$")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message, **({"params": extra.pop("params")} if "params" in extra else {}), **extra})


class Credentials(BaseModel):
    username: str
    password: str = Field(min_length=1, max_length=200)
    invite: str = Field(default="", max_length=200)  # only checked when the server has a registration code


class LiaraCheck(BaseModel):
    token: str = Field(min_length=10, max_length=500)
    team_id: str = ""


class SettingsIn(BaseModel):
    liara_token: str | None = Field(default=None, min_length=10, max_length=500)
    team_id: str | None = None
    team_name: str | None = None  # display only, taken from /liara/check
    language: str | None = None


class RunIn(BaseModel):
    mode: str = "deploy"  # deploy = real run that pauses for approval before creating anything; preview = dry run
    network: str = ""


class RespondIn(BaseModel):
    request_id: str
    value: Any


class WpUploadIn(BaseModel):
    host: str = Field(max_length=253)
    port: int = Field(default=21, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=500)
    implicit: bool = False  # FTPS that starts TLS at once (port 990) instead of upgrading after connecting
    remote_root: str = Field(default="", max_length=200)
    uploads: bool = False
    confirm: bool = False


class ConfirmIn(BaseModel):
    confirm: bool = False
    token: str = Field(default="", max_length=300)  # a token that can write, if the saved one cannot (never stored)


class WebhookIn(BaseModel):
    url: str = Field(min_length=10, max_length=1000)


class OneClickIn(BaseModel):
    app: str = Field(max_length=100)
    name: str = Field(default="", max_length=100)


class BranchIn(BaseModel):
    branch: str = Field(max_length=100)


class CleanupIn(BaseModel):
    confirm: str


class FixIn(BaseModel):
    tool: str
    args: dict = {}
    value: str | None = None   # a variable's value: typed once, used once, never stored


class ChatIn(BaseModel):
    project_id: int = 0   # 0 = the general conversation on the dashboard
    text: str = Field(min_length=1, max_length=4000)


class ChatDecisionIn(BaseModel):
    project_id: int = 0
    decision: str = Field(pattern="^(approve|decline)$")
    value: str | None = Field(default=None, max_length=5000)        # a secret typed on the card: used once, never stored or shown to the model
    confirm_name: str | None = Field(default=None, max_length=200)


class DomainPlanIn(BaseModel):
    domain: str = Field(min_length=1, max_length=300)
    use_liara_dns: bool = False  # host the domain's DNS on Liara (the records are then added for you)
    zone: str | None = Field(default=None, max_length=253)  # the registrable domain, when it is not the last two labels
    app: str | None = None
    www: bool = False  # also attach www.<domain> and redirect it to the domain


class RollbackIn(BaseModel):
    confirm: bool = False  # the person has seen which release this goes back to and approved it


class DomainCreateIn(DomainPlanIn):
    confirm: bool = False  # the person has seen the plan and approved it


def log_model_failure(e: Exception) -> None:
    """Why a model call failed, for the server's log: the kind of error and HTTP status only (the message may quote the conversation)."""
    logging.getLogger("uvicorn.error").warning("the language model call failed: %s%s", type(e).__name__, f" (HTTP {e.status_code})" if getattr(e, "status_code", None) else "")


def create_app(settings: WebSettings | None = None, deps=None, agent_settings: Settings | None = None) -> FastAPI:
    settings = settings or WebSettings()
    agent_settings = agent_settings or get_settings()
    db = DB(settings.data_dir / "app.db")
    vault = TokenVault(settings.ensure_secret())
    manager = RunManager(settings, db, deps or RealDeps(), agent_settings)
    login_limit, check_limit = RateLimiter(5, 300), RateLimiter(10, 300)
    @asynccontextmanager
    async def lifespan(_app):
        stop = monitor.start_loop(db, settings.monitor_interval_seconds, lambda: app.state.monitor_targets(), lambda u: monitor.ping(u), lambda *a: app.state.notify(*a)) if settings.monitor_interval_seconds > 0 else None
        health_stop = threading.Event()
        if settings.health_report_hours > 0:
            def _health_loop():
                while not health_stop.wait(settings.health_report_hours * 3600):
                    app.state.health_round()
            threading.Thread(target=_health_loop, daemon=True).start()
        yield
        health_stop.set()
        if stop:
            stop.set()

    app = FastAPI(title="Liara Launchpad API", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings, app.state.db, app.state.manager = settings, db, manager
    app.add_middleware(_GZipExceptStreams, minimum_size=1024)
    app.add_middleware(CORSMiddleware, allow_origins=sorted(settings.origins()), allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type", "X-Requested-With", "Last-Event-ID"])

    # -- request guards ------------------------------------------------------------------------------------------
    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        """Cookie auth needs CSRF protection: a changing request must come from an allowed origin, or carry a header that
        cross-site forms cannot set (and that CORS does not allow other origins to set)."""
        if request.method in UNSAFE:
            origin = request.headers.get("origin")
            # same-origin (the page and the API share a host: the normal deployment) or an explicitly allowed origin (a dev server)
            same_origin = bool(origin) and urlparse(origin).netloc == request.headers.get("host", "")
            ok = (same_origin or origin.rstrip("/") in settings.origins()) if origin else request.headers.get("x-requested-with") == "XMLHttpRequest"
            if not ok:
                return JSONResponse({"detail": {"code": "csrf", "message": "request origin not allowed"}}, status_code=403)
        return await call_next(request)

    def current_user(request: Request) -> dict:
        tok = request.cookies.get(SESSION_COOKIE)
        row = db.one("SELECT u.id, u.username, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.digest=?", (token_digest(tok),)) if tok else None
        if not row or row["expires_at"] < time.time():
            raise err(401, "not_logged_in", "please log in")
        return {"id": row["id"], "username": row["username"]}

    def user_settings(uid: int) -> dict:
        r = db.one("SELECT liara_token_enc, team_id, team_name, language FROM settings WHERE user_id=?", (uid,))
        return {"token_enc": r["liara_token_enc"] if r else None, "team_id": r["team_id"] if r else "", "team_name": r["team_name"] if r else "",
                "language": r["language"] if r else "en"}

    def token_of(uid: int) -> tuple[str, str]:
        s = user_settings(uid)
        token = vault.decrypt(s["token_enc"]) if s["token_enc"] else None
        if not token:
            raise err(400, "no_liara_token", "connect your Liara account first")
        return token, s["team_id"]

    def github_login_name(uid: int) -> str | None:
        row = db.one("SELECT github_login, github_token_enc FROM settings WHERE user_id=?", (uid,))
        return (row["github_login"] or "?") if row and row["github_token_enc"] else None

    def github_login_token(uid: int) -> str:
        row = db.one("SELECT github_token_enc FROM settings WHERE user_id=?", (uid,))
        return (vault.decrypt(row["github_token_enc"]) or "") if row and row["github_token_enc"] else ""

    def clone_token(row) -> str:
        """The token to clone this project with: its own (a private repository's), else the account's GitHub login for a GitHub repository."""
        if row["git_token_enc"]:
            return vault.decrypt(row["git_token_enc"]) or ""
        return github_login_token(row["user_id"]) if (urlparse(row["git_url"] or "").hostname or "").lower() == "github.com" else ""

    def own_project(uid: int, pid: int):
        r = db.one("SELECT * FROM projects WHERE id=? AND user_id=?", (pid, uid))
        if not r:
            raise err(404, "not_found", "project not found")
        return r

    def own_run(uid: int, rid: str):
        r = db.one("SELECT * FROM runs WHERE id=? AND user_id=?", (rid, uid))
        if not r:
            raise err(404, "not_found", "run not found")
        return r

    def project_dir(pid: int) -> Path:
        return settings.data_dir / "projects" / str(pid) / "src"

    def result_of(summary: dict | None) -> dict | None:
        """The structured outcome of a finished run: everything the UI needs to word the result line from codes."""
        if not summary:
            return None
        d = summary.get("diagnosis")
        return {"ok": summary.get("ok"), "stopped": summary.get("stopped"), "reason_code": summary.get("reason_code"),
                "reason_params": summary.get("reason_params") or {}, "failed_step": summary.get("failed_step"),
                "diagnosis_code": d["code"] if d else None, "created": summary.get("created") or {"apps": [], "databases": []},
                "urls": summary.get("urls") or {}, "dry_run": summary.get("dry_run"),
                "fixes": summary.get("fixes") or [], "heal_attempts": summary.get("heal_attempts") or 0}

    def run_view(r) -> dict:
        proj = db.one("SELECT name FROM projects WHERE id=?", (r["project_id"],))
        summary = json.loads(r["summary"]) if r["summary"] else None
        if summary is None:
            # the run has just finished: its status already comes from the live log, so its result must too (the database row is written a moment later)
            done = next((e for e in reversed(manager.events(r["id"])) if e.kind == "run_finished"), None)
            summary = {k: v for k, v in done.data.items() if k != "would_send"} if done else None
        return {"id": r["id"], "project_id": r["project_id"], "project": proj["name"] if proj else None, "mode": r["mode"],
                "status": manager.current_status(r["id"], r["status"]), "created_at": r["created_at"], "finished_at": r["finished_at"],
                "cleaned_at": r["cleaned_at"], "result": result_of(summary), "summary": summary}

    def live_resources(runs) -> dict | None:
        """What a project currently costs on Liara: resources of its latest successful, not-cleaned-up real run (with the free-address deadline)."""
        ok = [r for r in runs if r["status"] == "succeeded" and r["mode"] == "deploy" and not r["cleaned_at"]]
        if not ok:
            return None
        latest = ok[0]
        res: dict[str, dict] = {}
        for e in manager.events(latest["id"]):
            if e.kind in ("resource_created", "resource_updated"):
                res[e.data["id"]] = {**res.get(e.data["id"], {}), **{k: e.data[k] for k in ("kind", "id", "plan", "tier", "tier_label", "hourly_price", "monthly_price") if k in e.data}}
        if not res:
            return None
        hourly = round(sum(x["hourly_price"] for x in res.values()), 2)
        summary = json.loads(latest["summary"]) if latest["summary"] else {}
        first_deploy = min((r["finished_at"] or r["created_at"]) for r in ok)
        return {"run_id": latest["id"], "urls": summary.get("urls") or {}, "resources": list(res.values()), "hourly_incl_vat": hourly,
                "monthly_incl_vat": round(hourly * 720, -2), "free_address_expires_at": first_deploy + 7 * 86400 if summary.get("urls") else None}

    def project_view(row, with_runs: bool = False) -> dict:
        runs = db.q("SELECT * FROM runs WHERE project_id=? AND user_id=? AND mode!='release' ORDER BY created_at DESC", (row["id"], row["user_id"]))   # a release of new code is listed with the releases
        out = {"id": row["id"], "name": row["name"], "source_kind": row["source_kind"], "git_url": row["git_url"], "git_branch": row["git_branch"], "private": bool(row["git_token_enc"]), "down": monitor.is_down(db, row["id"]), "wordpress": bool(row["source_kind"] != "oneclick" and project_dir(row["id"]).is_dir() and looks_like_wordpress(project_dir(row["id"]))), "created_at": row["created_at"],
               "last_run": None if not runs else {k: v for k, v in run_view(runs[0]).items() if k not in ("summary", "project", "project_id")},
               "live": live_resources(runs)}
        if with_runs:
            out["runs"] = [run_view(x) for x in runs]
        return out

    # -- auth --------------------------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/auth/register", status_code=201, response_model=m.UserOut)
    def register(body: Credentials, request: Request, response: Response):
        if settings.registration_code and not hmac.compare_digest(body.invite.strip().encode(), settings.registration_code.encode()):
            if not login_limit.allow(f"invite|{request.client.host if request.client else '?'}"):  # guessing the code is rate limited like a password
                raise err(429, "rate_limited", "too many attempts; try again in a few minutes")
            raise err(403, "invite_required", "this server needs an invite code to create an account")
        username = body.username.strip().lower()
        if not USERNAME.match(username):
            raise err(422, "bad_username", "username: 3-32 characters, letters, digits, . _ -")
        if len(body.password) < 8:
            raise err(422, "weak_password", "password must be at least 8 characters")
        if db.one("SELECT 1 FROM users WHERE username=?", (username,)):
            raise err(409, "username_taken", "that username is taken")
        uid = db.run("INSERT INTO users(username,pw_hash,created_at) VALUES (?,?,?)", (username, hash_password(body.password), time.time()))
        _login(uid, response)
        return {"id": uid, "username": username}

    def _login(uid: int, response: Response) -> None:
        tok = new_session_token()
        db.run("INSERT INTO sessions(digest,user_id,expires_at) VALUES (?,?,?)", (token_digest(tok), uid, time.time() + settings.session_days * 86400))
        response.set_cookie(SESSION_COOKIE, tok, httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=settings.session_days * 86400, path="/")

    @app.post("/api/auth/login", response_model=m.UserOut)
    def login(body: Credentials, request: Request, response: Response):
        key = f"{request.client.host if request.client else '?'}|{body.username.strip().lower()}"
        if not login_limit.allow(key):
            raise err(429, "rate_limited", "too many attempts; try again in a few minutes")
        row = db.one("SELECT id, pw_hash FROM users WHERE username=?", (body.username.strip().lower(),))
        # verify even for unknown users so the response time does not reveal which usernames exist
        ok = verify_password(row["pw_hash"] if row else hash_password("x" * 12), body.password) and row is not None
        if not ok:
            raise err(401, "bad_credentials", "wrong username or password")
        login_limit.reset(key)
        _login(row["id"], response)
        return {"id": row["id"], "username": body.username.strip().lower()}

    @app.post("/api/auth/logout", response_model=m.OkOut)
    def logout(request: Request, response: Response):
        tok = request.cookies.get(SESSION_COOKIE)
        if tok:
            db.run("DELETE FROM sessions WHERE digest=?", (token_digest(tok),))
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/session", response_model=m.SessionOut)
    def session(request: Request):
        """200 whether or not the visitor is signed in, so the UI can ask without a 401 appearing in the console."""
        try:
            return {"authenticated": True, "user": current_user(request)}
        except HTTPException:
            return {"authenticated": False, "user": None}

    @app.get("/api/me", response_model=m.MeOut)
    def me(user=Depends(current_user)):
        s = user_settings(user["id"])
        return {**user, "has_liara_token": bool(s["token_enc"]), "team_id": s["team_id"], "team_name": s["team_name"], "language": s["language"]}

    # -- Liara account settings -----------------------------------------------------------------------------------
    @app.post("/api/liara/check", response_model=m.CheckOut)
    def liara_check(body: LiaraCheck, request: Request, user=Depends(current_user)):
        """Validate a token WITHOUT storing it; tells the UI which teams, networks and existing apps are visible."""
        if not check_limit.allow(f"{user['id']}"):
            raise err(429, "rate_limited", "too many checks; wait a few minutes")
        try:
            api = manager.deps.api(body.token, body.team_id)
            teams = api.list_teams()
            return {"valid": True, "teams": [{"id": t["_id"], "name": t["name"]} for t in teams], "team_required": bool(teams) and not body.team_id,
                    "networks": [n["name"] for n in api.list_networks()], "apps": api.list_app_ids()}
        except httpx.HTTPStatusError as e:  # Liara answered: say how it refused
            status = e.response.status_code
            return {"valid": False, "code": {401: "liara_token_invalid", 403: "liara_forbidden"}.get(status, "liara_rejected"), "params": {"status": status}}
        except Exception:  # no answer at all
            return {"valid": False, "code": "liara_unreachable", "params": {}}

    @app.get("/api/settings", response_model=m.SettingsOut)
    def get_settings_(user=Depends(current_user)):
        s = user_settings(user["id"])
        hook = db.one("SELECT alert_webhook_enc FROM settings WHERE user_id=?", (user["id"],))
        return {"has_liara_token": bool(s["token_enc"]), "has_alert_webhook": bool(hook and hook["alert_webhook_enc"]), "github_login": github_login_name(user["id"]), "team_id": s["team_id"], "team_name": s["team_name"], "language": s["language"]}

    @app.put("/api/settings", response_model=m.SettingsOut)
    def put_settings(body: SettingsIn, user=Depends(current_user)):
        if body.language is not None and body.language not in ("en", "fa"):
            raise err(422, "bad_language", "language must be en or fa")
        cur = user_settings(user["id"])
        enc = vault.encrypt(body.liara_token) if body.liara_token else cur["token_enc"]
        team_id = body.team_id if body.team_id is not None else cur["team_id"]
        if body.team_name is not None:
            team_name = body.team_name
        elif body.team_id is not None and body.team_id != cur["team_id"]:
            team_name = ""  # a different team: the old name no longer applies
        else:
            team_name = cur["team_name"]
        if not team_id:
            team_name = ""
        db.run("INSERT INTO settings(user_id,liara_token_enc,team_id,team_name,language) VALUES (?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
               "liara_token_enc=excluded.liara_token_enc, team_id=excluded.team_id, team_name=excluded.team_name, language=excluded.language",
               (user["id"], enc, team_id, team_name[:100], body.language or cur["language"]))
        return get_settings_(user)

    @app.put("/api/settings/alert-webhook", response_model=m.SettingsOut)
    def put_alert_webhook(body: WebhookIn, user=Depends(current_user)):
        """Where outage alerts go (any https endpoint that accepts a JSON POST). Stored encrypted: Slack-style addresses are secrets."""
        try:
            url = alerts.validate_webhook(body.url)
        except alerts.WebhookError as e:
            raise err(422, e.code, str(e))
        db.run("INSERT OR IGNORE INTO settings(user_id) VALUES (?)", (user["id"],))
        db.run("UPDATE settings SET alert_webhook_enc=? WHERE user_id=?", (vault.encrypt(url), user["id"]))
        return get_settings_(user)

    @app.delete("/api/settings/alert-webhook", response_model=m.SettingsOut)
    def delete_alert_webhook(user=Depends(current_user)):
        db.run("UPDATE settings SET alert_webhook_enc=NULL WHERE user_id=?", (user["id"],))
        return get_settings_(user)

    @app.post("/api/settings/alert-webhook/test", response_model=m.OkOut)
    def test_alert_webhook(user=Depends(current_user)):
        row = db.one("SELECT alert_webhook_enc FROM settings WHERE user_id=?", (user["id"],))
        url = vault.decrypt(row["alert_webhook_enc"]) if row and row["alert_webhook_enc"] else None
        if not url:
            raise err(409, "no_webhook", "set an alert address first")
        if not alerts.send(url, alert_payload("test", "Liara Launchpad", {"url": "", "status": None})):
            raise err(502, "webhook_failed", "the alert address did not accept the message")
        return {"ok": True}

    @app.delete("/api/settings/liara-token", response_model=m.OkOut)
    def delete_token(user=Depends(current_user)):
        db.run("UPDATE settings SET liara_token_enc=NULL WHERE user_id=?", (user["id"],))
        return {"ok": True}

    @app.get("/api/liara/networks", response_model=m.NetworksOut)
    def networks(user=Depends(current_user)):
        token, team = token_of(user["id"])
        try:
            return {"networks": [n["name"] for n in manager.deps.api(token, team).list_networks()]}
        except Exception:
            raise err(502, "liara_unreachable", "could not reach Liara with the saved token")

    # -- projects ---------------------------------------------------------------------------------------------------
    @app.post("/api/projects", status_code=201, response_model=m.ProjectCreatedOut)
    async def create_project(name: str = Form(...), git_url: str = Form(""), git_token: str = Form(""), git_branch: str = Form(""), file: UploadFile | None = File(None), user=Depends(current_user)):
        name = sources.slug(name)
        if bool(file and file.filename) == bool(git_url.strip()):
            raise err(422, "source_required", "provide either a zip file or a Git URL")
        git_token, git_branch = git_token.strip(), git_branch.strip()
        pid = db.run("INSERT INTO projects(user_id,name,source_kind,git_url,git_token_enc,git_branch,created_at) VALUES (?,?,?,?,?,?,?)",
                     (user["id"], name, "zip" if file and file.filename else "git", git_url.strip() or None, vault.encrypt(git_token) if git_token else None, git_branch or None, time.time()))
        dest = project_dir(pid)
        try:
            limit = settings.max_extracted_mb * 1024 * 1024
            if file and file.filename:
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                    size = 0
                    while chunk := await file.read(1 << 20):
                        size += len(chunk)
                        if size > settings.max_upload_mb * 1024 * 1024:
                            raise sources.SourceError("upload_too_large", f"the upload is larger than {settings.max_upload_mb} MB", limit_mb=settings.max_upload_mb)
                        tmp.write(chunk)
                try:
                    sources.safe_extract_zip(Path(tmp.name), dest, max_extracted_bytes=limit, max_files=settings.max_files)
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
            else:
                sources.clone_git(git_url, dest, allowed_hosts=settings.hosts(), max_extracted_bytes=limit, max_files=settings.max_files, token=git_token or (github_login_token(user["id"]) if (urlparse(git_url).hostname or "").lower() == "github.com" else ""), branch=git_branch)
        except sources.SourceError as e:
            db.run("DELETE FROM projects WHERE id=?", (pid,))
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise err(422, e.code, str(e), params=e.params)
        return {"id": pid, "name": name}

    # -- uptime -----------------------------------------------------------------------------------------------------
    def monitor_targets(row) -> list[str]:
        """The addresses worth asking: domains this agent attached (https) or, before one is attached, the run's liara.run address while it still works."""
        runs = db.q("SELECT * FROM runs WHERE project_id=? AND mode!='release' ORDER BY created_at DESC", (row["id"],))
        live = live_resources(runs)
        if not live:
            return []
        ok = [r for r in runs if r["status"] == "succeeded" and r["mode"] == "deploy" and not r["cleaned_at"]]
        domains = [f"https://{d['name']}" for d in Journal.load(manager.run_dir(ok[0]["id"]) / "created.json").domains]
        if domains:
            return domains[:3]
        if live["free_address_expires_at"] and live["free_address_expires_at"] < time.time():
            return []  # the test address is dead after 7 days: asking it would report an outage that is not one
        return list(live["urls"].values())[:3]

    def all_targets() -> list[tuple[int, str]]:
        return [(r["id"], u) for r in db.q("SELECT * FROM projects") for u in monitor_targets(r)]

    def alert_payload(event: str, project: str, info: dict, diagnosis: dict | None = None) -> dict:
        text = {"down": f"{project} is not answering ({info.get('url')})", "up": f"{project} is answering again ({info.get('url')})", "test": "Liara Launchpad: this is a test alert",
                "report": f"{project}: daily health check, {(diagnosis or {}).get('state', 'unknown')} ({info.get('url')})"}[event]
        out = {"event": event, "project": project, "url": info.get("url"), "status": info.get("status")}
        if event == "report" and diagnosis and not diagnosis.get("causes"):
            adv = [a["code"] for a in diagnosis.get("advice", [])]
            text += ". Worth improving: " + ", ".join(adv) if adv else ""
        if diagnosis and diagnosis.get("causes"):
            top = diagnosis["causes"][0]
            text += ". " + live_diagnosis.summary_text(diagnosis)
            out["diagnosis"] = {"state": diagnosis["state"], "cause": top["code"], "evidence": [e[:160] for e in top.get("evidence", [])[:2]],
                                "fixes": [f.get("text", "") for f in top.get("fixes", [])[:2]]}
        return {**out, "text": text, "content": text}

    def ai_read(uid: int, result: dict, logs: list[str]) -> list[dict] | None:
        """The model's second opinion on a diagnosis the rules were unsure about; None when there is no model or it failed (the rules' answer stands on its own)."""
        try:
            lang = user_settings(uid)["language"]
            llm = manager.deps.llm(agent_settings.model_copy(update={"language": lang}))
            return analysis.analyze(llm, result, logs, lang) if llm is not None else None
        except Exception as e:
            logging.getLogger("liara_agent.monitor").warning("the model's reading of a diagnosis failed: %s", type(e).__name__)
            return None

    def unsure(result: dict) -> bool:
        """Nothing explained it, or only a vague answer: the cases where a model's reading adds something."""
        c = result.get("causes") or []
        return result["state"] != "healthy" and (not c or c[0]["score"] < 60)

    def keep_diagnosis(pid: int, url: str, found: dict, source: str) -> None:
        db.run("INSERT INTO incidents(project_id,ts,url,body,source) VALUES (?,?,?,?,?)", (pid, time.time(), url, json.dumps(found), source))
        db.run("DELETE FROM incidents WHERE project_id=? AND id NOT IN (SELECT id FROM incidents WHERE project_id=? ORDER BY ts DESC LIMIT 20)", (pid, pid))

    def auto_diagnose(uid: int, pid: int, url: str) -> dict | None:
        """The app has just stopped answering: find out why now, keep what was found (the project page shows it) and return it. Never raises: monitoring must go on."""
        try:
            found, logs = collect_evidence(uid, pid)
            if unsure(found) and (ai := ai_read(uid, found, logs)):
                found["ai"] = ai
            found = scrub(found)
            keep_diagnosis(pid, url, found, "outage")
            return found
        except Exception as e:  # a failed diagnosis must not hide the alert itself
            logging.getLogger("liara_agent.monitor").warning("the automatic diagnosis of project %s failed: %s", pid, type(e).__name__)
            return None

    def health_round() -> int:
        """The scheduled health check: every running app is read once, the result kept (the project page shows the newest) and, when something needs attention, sent to the person's
        alert webhook. Returns how many apps were checked. Never raises."""
        n = 0
        for row in db.q("SELECT p.id, p.name, p.user_id, s.alert_webhook_enc FROM projects p LEFT JOIN settings s ON s.user_id=p.user_id"):
            try:
                if not monitor_targets(dict(row)):
                    continue
                found, logs = collect_evidence(row["user_id"], row["id"])
                if unsure(found) and (ai := ai_read(row["user_id"], found, logs)):
                    found["ai"] = ai
                found = scrub(found)
                keep_diagnosis(row["id"], found.get("url") or "", found, "daily")
                n += 1
                if row["alert_webhook_enc"] and (found["state"] != "healthy" or found.get("advice")):
                    alerts.send(vault.decrypt(row["alert_webhook_enc"]), alert_payload("report", row["name"], {"url": found.get("url"), "status": found["signals"]["http"]["status"]}, found))
            except Exception as e:   # one app that cannot be read must not stop the others
                logging.getLogger("liara_agent.monitor").warning("the health check of project %s failed: %s", row["id"], type(e).__name__)
        return n

    def notify(pid: int, event: str, info: dict) -> None:
        row = db.one("SELECT p.name, p.user_id, s.alert_webhook_enc FROM projects p JOIN settings s ON s.user_id=p.user_id WHERE p.id=?", (pid,))
        if not row:
            return
        diagnosis = auto_diagnose(row["user_id"], pid, info.get("url") or "") if event == "down" else None
        url = vault.decrypt(row["alert_webhook_enc"]) if row["alert_webhook_enc"] else None
        if url:
            alerts.send(url, alert_payload(event, row["name"], info, diagnosis))

    app.state.notify = notify
    app.state.health_round = health_round
    app.state.monitor_targets = all_targets  # the loop itself is started by the lifespan below

    @app.get("/api/projects/{pid}/uptime", response_model=m.UptimeOut)
    def uptime(pid: int, user=Depends(current_user)):
        row = own_project(user["id"], pid)
        return {"interval_seconds": settings.monitor_interval_seconds, "watching": monitor_targets(row), **monitor.summary(db, pid)}

    @app.post("/api/projects/{pid}/uptime/check", response_model=m.UptimeOut)
    def uptime_now(pid: int, user=Depends(current_user)):
        row = own_project(user["id"], pid)
        targets = monitor_targets(row)
        if not targets:
            raise err(409, "no_live_app", "deploy the project first: there is nothing running to check")
        for u in targets:
            monitor.record(db, pid, u, lambda x: monitor.ping(x), lambda *a: app.state.notify(*a))
        return {"interval_seconds": settings.monitor_interval_seconds, "watching": targets, **monitor.summary(db, pid)}

    @app.get("/api/projects/{pid}/metrics", response_model=m.MetricsOut)
    def project_metrics(pid: int, hours: int = 6, user=Depends(current_user)):
        """CPU and memory of the running app, read from Liara (read-only)."""
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            return metrics.fetch(domain_client(user["id"], journal), app_name, max(1, min(hours, 72)))

    def collect_diagnosis(uid: int, pid: int) -> dict:
        return collect_evidence(uid, pid)[0]

    def collect_evidence(uid: int, pid: int) -> tuple[dict, list[str]]:
        """Everything that says why the running app is failing or slow, read at once (an HTTP probe, runtime logs, releases, CPU and memory, the plan's limits), then ranked; and the
        log lines it read. Read-only: nothing is changed and no variable values are read."""
        row = own_project(uid, pid)
        journal, app_name = live_app(uid, pid)
        live = (project_view(row)["live"] or {})
        res = next((r for r in live.get("resources", []) if r.get("kind") == "app" and r.get("id") == app_name), {})
        url = f"https://{app_name}.liara.run/"
        status, ms, error = live_diagnosis.probe(httpx.get, url, 15)
        token, team = token_of(uid)
        client = domain_client(uid, journal)
        with liara_errors():
            rel = history.list_releases(client, app_name, project_dir(pid) / ".agent-state", 12)
            now_release = next((x["id"] for x in rel["releases"] if x["current"]), None)
            logs = client.runtime_logs(app_name, now_release)[-150:]   # this release's own lines: what an earlier release printed is not this problem
            series = metrics.fetch(client, app_name, 6)
            envs = set(client.app_envs(app_name))
            cat = manager.deps.api(token, team).live_catalog()
        rows = rel["releases"]
        idx = next((i for i, x in enumerate(rows) if x["current"]), None)
        cur = rows[idx] if idx is not None else None
        prev = next((x for x in rows[(idx or 0) + 1:] if str(x["state"]).upper() == "READY" and x.get("rollback")), None) if idx is not None else None
        plan_id = res.get("plan") or ""
        plan = cat.project_plans.get(plan_id)
        vals = lambda k: [x["value"] for x in series.get(k) or []]   # noqa: E731
        mem, cpu = vals("memory"), vals("cpu")
        snap = live_diagnosis.Snapshot(
            app=app_name, url=url, http_status=status, http_ms=ms, http_error=error, logs=logs, platform=(cur or {}).get("type") or "docker", plan_id=plan_id,
            tier=(res.get("tier") or "basic"), port=(cur or {}).get("port"), ram_gb=plan.ram.amount if plan else 0.0, cpu_cores=plan.cpu.amount if plan else 0.0,
            mem_now=mem[-1] if mem else None, mem_max=max(mem) if mem else None, cpu_now=cpu[-1] if cpu else None, cpu_max=max(cpu) if cpu else None,
            current_release=None if not cur else {"id": cur["id"], "state": cur["state"], "age_minutes": live_diagnosis.age_minutes(cur.get("created_at"))},
            previous_release=None if not prev else {"id": prev["id"], "age_minutes": live_diagnosis.age_minutes(prev.get("created_at"))}, env_names=envs, cat=cat)
        return {"app": app_name, "url": url, "checked_at": time.time(), **live_diagnosis.rank(snap)}, logs

    def collect_limits(uid: int, pid: int) -> dict:
        row = own_project(uid, pid)
        journal, app_name = live_app(uid, pid)
        live = (project_view(row)["live"] or {})
        res = next((r for r in live.get("resources", []) if r.get("kind") == "app" and r.get("id") == app_name), {})
        token, team = token_of(uid)
        with liara_errors():
            rel = history.list_releases(domain_client(uid, journal), app_name, project_dir(pid) / ".agent-state", 50)
            cat = manager.deps.api(token, team).live_catalog()
        tier = res.get("tier") or "basic"
        return {"daily_deployments": {**liara_limits.daily_deployments(rel["releases"], liara_limits.cap_of(cat, res.get("plan") or "", tier)), "tier": tier}, "known": list(liara_limits.KNOWN)}

    @app.get("/api/projects/{pid}/limits", response_model=m.AppLimitsOut)
    def project_limits(pid: int, user=Depends(current_user)):
        """How many releases of the app Liara allows a day and how many were made in the last 24 hours, and the things the agent cannot do through Liara's API."""
        return collect_limits(user["id"], pid)

    FIX_TOOLS = ("rollback", "resize_plan", "set_env")   # the changes a diagnosis can name: the same tools (and the same checks) the assistant's cards use

    def fix_tool(uid: int, pid: int, name: str):
        own_project(uid, pid)
        if name not in FIX_TOOLS:
            raise err(422, "fix_not_allowed", "that change cannot be made from here")
        return next(t for t in chat_tools.build_tools(UserActions(uid, pid)) if t.name == name)

    @app.post("/api/projects/{pid}/fixes/preview", response_model=m.ChatActionOut)
    def fix_preview(pid: int, body: FixIn, user=Depends(current_user)):
        """What a fix from the diagnosis would do, as the same approval card the assistant shows. Nothing is changed."""
        tool = fix_tool(user["id"], pid, body.tool)
        try:
            info = tool.describe(None, body.args)
        except ToolError as e:
            raise err(409, "fix_invalid", str(e))
        return {"id": "fix", "tool": tool.name, "status": "pending", "summary": info.get("summary", tool.name), "code": info.get("code", tool.name), "params": info.get("params") or {},
                "needs_value": bool(info.get("needs_value")), "value_label": info.get("value_label"), "confirm_name": bool(info.get("confirm_name")),
                "price_delta_hourly": info.get("price_delta_hourly"), "warnings": info.get("warnings") or []}

    @app.post("/api/projects/{pid}/fixes/apply", response_model=m.FixAppliedOut)
    def fix_apply(pid: int, body: FixIn, user=Depends(current_user)):
        """Make the change after the person approved its card. The same code path as the assistant's approved card."""
        tool = fix_tool(user["id"], pid, body.tool)
        try:
            tool.describe(None, body.args)   # the same checks as the preview: a stale card does nothing
            result = tool.run(None, {**body.args, **({"_value": body.value} if body.value else {})})
        except ToolError as e:
            raise err(409, "fix_invalid", str(e))
        return {"ok": True, "tool": tool.name, "result": {k: v for k, v in scrub(result).items() if k != "link"} if isinstance(result, dict) else {}}

    @app.get("/api/projects/{pid}/diagnosis/latest", response_model=m.LatestDiagnosisOut)
    def live_diagnosis_latest(pid: int, user=Depends(current_user)):
        """What the agent found by itself the last time the uptime check saw the app go down (nothing when it never did)."""
        own_project(user["id"], pid)
        r = db.one("SELECT body, source FROM incidents WHERE project_id=? ORDER BY ts DESC LIMIT 1", (pid,))
        return {"diagnosis": json.loads(r["body"]) if r else None, "source": r["source"] if r else None}

    analyze_limit = RateLimiter(6, 300)

    @app.post("/api/projects/{pid}/diagnosis/analyze", response_model=m.AnalysisOut)
    def live_diagnosis_analyze(pid: int, user=Depends(current_user)):
        """Ask the language model to read the app's evidence (the log tail and what was measured, scrubbed) and propose causes: a second opinion below the rules."""
        if not analyze_limit.allow(str(user["id"])):
            raise err(429, "rate_limited", "too many analyses; try again in a few minutes")
        lang = user_settings(user["id"])["language"]
        llm = manager.deps.llm(agent_settings.model_copy(update={"language": lang}))
        if llm is None:
            raise err(503, "llm_unavailable", "no language model is configured on this server")
        found, logs = collect_evidence(user["id"], pid)
        try:
            return {"hypotheses": analysis.analyze(llm, found, logs, lang)}
        except LLMError as e:
            log_model_failure(e)
            raise err(502, "llm_failed", "the language model could not be reached")

    @app.get("/api/projects/{pid}/diagnosis", response_model=m.LiveDiagnosisOut)
    def live_diagnosis_get(pid: int, user=Depends(current_user)):
        """Why the running app is failing or slow: the signals read now and the likely causes, most likely first, each with what points to it and the fix that follows."""
        return collect_diagnosis(user["id"], pid)

    # -- WordPress: send the project's code to a managed site over FTPS ---------------------------------------------------
    wp_jobs: dict[int, dict] = {}

    @app.get("/api/projects/{pid}/wordpress", response_model=m.WpPlanOut)
    def wp_plan(pid: int, uploads: bool = False, user=Depends(current_user)):
        """The theme, plugin and language files that would be sent (and the uploads folder if asked). Nothing leaves the server."""
        own_project(user["id"], pid)
        try:
            plan = wpdeliver.collect(project_dir(pid), uploads)
        except wpdeliver.WpError as e:
            raise err(422 if e.code != "wp_no_content" else 409, e.code, str(e), params=e.params)
        return {"files": len(plan["files"]), "total_bytes": plan["total_bytes"], "parts": plan["parts"], "uploads": plan["uploads"], "sample": [f["path"] for f in plan["files"][:12]]}

    @app.post("/api/projects/{pid}/wordpress/upload", status_code=202, response_model=m.WpJobOut)
    def wp_upload(pid: int, body: WpUploadIn, user=Depends(current_user)):
        """Starts sending the files over FTPS with the access the person made in the Liara console. The password is used for this transfer and not stored."""
        own_project(user["id"], pid)
        if not body.confirm:
            raise err(422, "confirm_required", "confirm to send the files")
        if wp_jobs.get(pid, {}).get("state") == "running":
            raise err(409, "wp_busy", "an upload is already running for this project")
        try:
            plan = wpdeliver.collect(project_dir(pid), body.uploads)
            wpdeliver.check_host(body.host)
        except wpdeliver.WpError as e:
            raise err(422 if e.code != "wp_no_content" else 409, e.code, str(e), params=e.params)
        job = wp_jobs[pid] = {"state": "running", "sent": 0, "total": len(plan["files"]), "bytes": 0, "error": None}

        def work():
            try:
                def progress(done, total):
                    job["sent"] = done
                out = wpdeliver.upload(plan, host=body.host, port=body.port, user=body.user, password=body.password, implicit=body.implicit, remote_root=body.remote_root, progress=progress)
                job.update(state="done", bytes=out["bytes"])
            except wpdeliver.WpError as e:
                job.update(state="failed", error=e.code)
            except Exception as e:  # never leave the job hanging; only the kind of error is kept (a message may quote the login)
                job.update(state="failed", error=type(e).__name__)
        threading.Thread(target=work, daemon=True).start()
        return {**job}

    @app.get("/api/projects/{pid}/wordpress/upload", response_model=m.WpJobOut)
    def wp_upload_status(pid: int, user=Depends(current_user)):
        own_project(user["id"], pid)
        return wp_jobs.get(pid) or {"state": "idle", "sent": 0, "total": 0, "bytes": 0, "error": None}

    # -- sign in with GitHub --------------------------------------------------------------------------------------------
    def oauth_redirect_uri(request: Request) -> str:
        """The callback address GitHub must be given: it has to match the registered one exactly, and behind Liara's proxy the app itself only sees http, so the
        proxy's own X-Forwarded-Proto/Host say what the browser used (or WEB_PUBLIC_URL, if set)."""
        if settings.public_url:
            return settings.public_url.rstrip("/") + "/api/github/callback"
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
        host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host", "") or request.url.netloc
        return f"{proto}://{host}/api/github/callback"

    def sign_ctx(d: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
        return raw + "." + hmac.new(settings.ensure_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()

    def read_ctx(v: str) -> dict | None:
        raw, _, sig = (v or "").partition(".")
        if not raw or not hmac.compare_digest(sig, hmac.new(settings.ensure_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()):
            return None
        try:
            return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        except ValueError:
            return None

    def safe_next(path: str, default: str) -> str:
        return path if path.startswith("/") and not path.startswith("//") and "\\" not in path and len(path) < 200 else default

    @app.get("/api/github/login")
    def github_login(request: Request, mode: str = "connect", invite: str = "", next: str = ""):
        """Starts GitHub's authorization. mode=signin: sign in or create an account (asks only for who you are; a server with an invite code needs `invite` for a NEW account).
        mode=connect (needs a session): also asks for the `repo` permission, so private repositories can be downloaded and fixes sent as pull requests. Off unless the server has an OAuth app."""
        if not (settings.github_client_id and settings.github_client_secret):
            raise err(409, "github_oauth_off", "login with GitHub is not set up on this server")
        if mode not in ("signin", "connect"):
            raise err(422, "bad_option", "mode must be signin or connect")
        if mode == "connect":
            current_user(request)   # connecting needs an account to attach the token to
        nonce = secrets.token_urlsafe(24)
        ctx = {"n": nonce, "m": mode, "i": invite.strip()[:200], "r": safe_next(next, "/dashboard" if mode == "signin" else "/settings?github=connected")}
        resp = JSONResponse({"url": github.authorize_url(settings.github_client_id, nonce, oauth_redirect_uri(request), github.SCOPE_SIGNIN if mode == "signin" else github.SCOPE)})
        resp.set_cookie("gh_ctx", sign_ctx(ctx), httponly=True, samesite="lax", secure=settings.cookie_secure, max_age=600, path="/api/github")
        return resp

    def github_username(login: str) -> str:
        base = re.sub(r"[^a-z0-9._-]", "", login.lower())[:24] or "github"
        base = base if len(base) >= 3 else base + "-gh"
        name = base
        while db.one("SELECT 1 FROM users WHERE username=?", (name,)):
            name = f"{base}-{secrets.token_hex(2)}"
        return name

    @app.get("/api/github/callback")
    def github_callback(request: Request, code: str = "", state: str = ""):
        """GitHub sends the person back here (a browser visit, so every outcome is a redirect). The state must match the signed cookie from the start of this round trip."""
        ctx = read_ctx(request.cookies.get("gh_ctx", ""))
        fail = lambda where, why: Response(status_code=302, headers={"Location": f"{where}?github_error={why}"})  # noqa: E731
        if not ctx or not code or not hmac.compare_digest(str(ctx.get("n", "")), state):
            return fail("/login", "github_oauth_state")
        mode, back = ctx.get("m"), ctx.get("r", "/")
        where = "/settings" if mode == "connect" else ("/register" if ctx.get("i") or settings.registration_code else "/login")
        try:
            http = getattr(manager.deps, "github_http", None)
            token = github.exchange_code(settings.github_client_id, settings.github_client_secret, code, oauth_redirect_uri(request), http)
            who = github.GitHub(token, http)._req("GET", "/user")
        except github.GitHubError as e:
            return fail(where, e.code)
        except httpx.HTTPError:
            return fail(where, "github_unreachable")
        gid, login = str(who.get("id", "")), str(who.get("login", ""))[:100]
        if not gid:
            return fail(where, "github_oauth_failed")
        resp = Response(status_code=302, headers={"Location": back})
        resp.delete_cookie("gh_ctx", path="/api/github")
        if mode == "connect":
            try:
                user = current_user(request)
            except HTTPException:
                return fail("/login", "not_logged_in")
            db.run("INSERT OR IGNORE INTO settings(user_id) VALUES (?)", (user["id"],))
            db.run("UPDATE settings SET github_token_enc=?, github_login=? WHERE user_id=?", (vault.encrypt(token), login, user["id"]))
            return resp
        row = db.one("SELECT id FROM users WHERE github_id=?", (gid,))
        if row:
            uid = row["id"]
        else:
            if settings.registration_code and not hmac.compare_digest(str(ctx.get("i", "")).encode(), settings.registration_code.encode()):
                return fail("/register", "invite_required")   # a NEW account still needs the invite code
            uid = db.run("INSERT INTO users(username,pw_hash,created_at,github_id) VALUES (?,?,?,?)",
                         (github_username(login), hash_password(secrets.token_urlsafe(32)), time.time(), gid))   # no password anyone knows: GitHub is how this account signs in
        _login(uid, resp)
        return resp

    @app.delete("/api/github/connection", response_model=m.SettingsOut)
    def github_disconnect(user=Depends(current_user)):
        db.run("UPDATE settings SET github_token_enc=NULL, github_login=NULL WHERE user_id=?", (user["id"],))
        return get_settings_(user)

    @app.get("/api/github/repos", response_model=m.GithubReposOut)
    def github_repos(user=Depends(current_user)):
        token = github_login_token(user["id"])
        if not token:
            raise err(409, "github_not_connected", "connect your GitHub account first")
        try:
            return {"repos": github.list_repos(token, getattr(manager.deps, "github_http", None))}
        except github.GitHubError as e:
            raise err(e.status, e.code, str(e), params=e.params)
        except httpx.HTTPError:
            raise err(502, "github_unreachable", "could not reach GitHub")

    @app.get("/api/oneclick", response_model=m.OneClickListOut)
    def oneclick_list(user=Depends(current_user)):
        """Liara's ready-made apps (a snapshot). Those with a spec can be installed here; the rest need a cloud server."""
        out = []
        for name, a in sorted(oneclick.catalog().items(), key=lambda kv: -kv[1].get("installs", 0)):
            spec = oneclick.compose_spec(name) if a["spec"] else None
            out.append({"app": name, "about": a.get("about") or "", "categories": a.get("categories") or [], "installs": a.get("installs") or 0, "github": a.get("github"),
                        "needs_vm": not spec, "databases": sorted({d["type"] for d in (spec or {}).get("databases") or []}), "apps": len((spec or {}).get("apps") or [1])})
        return {"apps": out}

    @app.post("/api/projects/oneclick", status_code=201, response_model=m.ProjectCreatedOut)
    def create_oneclick_project(body: OneClickIn, user=Depends(current_user)):
        """A project with no source: its plan comes from a ready-made app's spec. Nothing is created on Liara until the run is approved."""
        entry = oneclick.catalog().get(body.app)
        if entry is None:
            raise err(404, "oneclick_unknown", "that is not one of Liara's ready-made apps")
        if not entry["spec"]:
            raise err(409, "oneclick_needs_vm", "this app needs a cloud server, which this agent does not create")
        name = sources.slug(body.name or body.app)
        pid = db.run("INSERT INTO projects(user_id,name,source_kind,git_url,created_at) VALUES (?,?,?,?,?)", (user["id"], name, "oneclick", None, time.time()))
        dest = project_dir(pid)
        dest.mkdir(parents=True)
        (dest / plan_oneclick.MARKER).write_text(body.app)
        return {"id": pid, "name": name}

    @app.post("/api/projects/{pid}/branches", status_code=201, response_model=m.ProjectCreatedOut)
    def preview_branch(pid: int, body: BranchIn, user=Depends(current_user)):
        """A preview deployment: the same repository at another branch becomes its own project, so its app, databases and cleanup are separate
        from the main one's. The access token of a private repository is reused, never returned."""
        base = own_project(user["id"], pid)
        if base["source_kind"] != "git" or not base["git_url"]:
            raise err(409, "not_a_git_project", "only a project that came from a Git repository can preview a branch")
        try:
            branch = sources.validate_branch(body.branch)
        except sources.SourceError as e:
            raise err(422, e.code, str(e))
        token = clone_token(base)
        stem = base["name"][: -len(sources.slug(base["git_branch"])) - 1] if base["git_branch"] and base["name"].endswith("-" + sources.slug(base["git_branch"])) else base["name"]
        name = sources.slug(f"{stem}-{branch}")
        new = db.run("INSERT INTO projects(user_id,name,source_kind,git_url,git_token_enc,git_branch,created_at) VALUES (?,?,?,?,?,?,?)",
                     (user["id"], name, "git", base["git_url"], base["git_token_enc"], branch, time.time()))
        dest = project_dir(new)
        try:
            sources.clone_git(base["git_url"], dest, allowed_hosts=settings.hosts(), max_extracted_bytes=settings.max_extracted_mb * 1024 * 1024,
                              max_files=settings.max_files, token=token, branch=branch)
        except sources.SourceError as e:
            db.run("DELETE FROM projects WHERE id=?", (new,))
            shutil.rmtree(dest.parent, ignore_errors=True)
            raise err(422, e.code, str(e), params=e.params)
        return {"id": new, "name": name}

    @app.get("/api/projects", response_model=m.ProjectsOut)
    def list_projects(user=Depends(current_user)):
        return {"projects": [project_view(r) for r in db.q("SELECT * FROM projects WHERE user_id=? ORDER BY id DESC", (user["id"],))]}

    @app.get("/api/projects/{pid}", response_model=m.ProjectDetailOut)
    def get_project(pid: int, user=Depends(current_user)):
        return project_view(own_project(user["id"], pid), with_runs=True)

    @app.delete("/api/projects/{pid}", response_model=m.ProjectDeletedOut)
    def delete_project(pid: int, confirm: str = "", user=Depends(current_user)):
        """Deletes the project, ALL its runs and everything those runs created on Liara (apps, databases, disks, domains...). The project's name must be given as `confirm`.
        If Liara cannot be cleaned (an error, or no saved token), nothing local is deleted either, so it can be tried again and nothing is left running unseen."""
        row = own_project(user["id"], pid)
        if confirm != row["name"]:
            raise err(422, "confirm_mismatch", "type the project name to confirm", expected_hint=row["name"][:1] + "...")
        runs = db.q("SELECT id, cleaned_at FROM runs WHERE project_id=? AND user_id=?", (pid, user["id"]))
        if any(manager.get_live(r["id"]) and manager.get_live(r["id"]).thread.is_alive() for r in runs):
            raise err(409, "run_active", "a run is still active for this project")
        journals = []
        for r in runs:
            path = manager.run_dir(r["id"]) / "created.json"
            if r["cleaned_at"] or not path.is_file():
                continue
            j = Journal.load(path)
            if any(getattr(j, k) for k in ("apps", "databases", "domains", "buckets", "mail_servers", "disks", "ftps", "keys", "smtp", "records")):
                journals.append((r["id"], j))
        deleted: dict[str, list[str]] = {"apps": [], "databases": [], "domains": [], "buckets": [], "mail_servers": [], "disks": []}
        if journals:
            token, team = token_of(user["id"])
            for rid, j in journals:
                one: dict[str, list[str]] = {}
                try:
                    remove_all(manager.deps.client(token, team, False, j), one)
                except (LiaraAPIError, NotOwnedError, httpx.HTTPError) as e:
                    for k, v in one.items():
                        deleted.setdefault(k, []).extend(v)
                    raise err(502, "cleanup_failed", "could not delete everything on Liara, so the project was kept", deleted=deleted, detail=str(e)[:200])
                db.run("UPDATE runs SET cleaned_at=? WHERE id=?", (time.time(), rid))
                for k, v in one.items():
                    deleted.setdefault(k, []).extend(v)
        for r in runs:
            shutil.rmtree(manager.run_dir(r["id"]), ignore_errors=True)
        shutil.rmtree(project_dir(pid).parent, ignore_errors=True)
        db.run("DELETE FROM projects WHERE id=?", (pid,))
        chat_store.forget_project(db, user["id"], pid, lambda name: bool(name) and sources.slug(name) == row["name"])
        return {"ok": True, "deleted": deleted}

    # -- runs ---------------------------------------------------------------------------------------------------------
    @app.post("/api/projects/{pid}/runs", status_code=201, response_model=m.RunCreatedOut)
    def start_run(pid: int, body: RunIn, user=Depends(current_user)):
        if body.mode not in ("deploy", "preview"):
            raise err(422, "bad_mode", "mode must be deploy or preview")
        proj = own_project(user["id"], pid)
        token, team = token_of(user["id"])
        if manager.active_count(user["id"]) >= settings.max_concurrent_runs_per_user:
            raise err(429, "too_many_runs", "finish or cancel your active runs first")
        rid = manager.start(user_id=user["id"], project_id=pid, project_name=proj["name"], source=project_dir(pid), token=token, team_id=team,
                            language=user_settings(user["id"])["language"], mode=body.mode, network=body.network)
        return {"id": rid}

    @app.get("/api/runs", response_model=m.RunsOut)
    def list_runs(user=Depends(current_user)):
        return {"runs": [run_view(r) for r in db.q("SELECT * FROM runs WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user["id"],))]}

    @app.get("/api/runs/{rid}", response_model=m.RunDetailOut)
    def get_run(rid: str, user=Depends(current_user)):
        r = own_run(user["id"], rid)
        return {**run_view(r), "open_requests": manager.open_requests(rid)}

    @app.get("/api/runs/{rid}/events", response_model=m.EventsOut)
    def run_events(rid: str, after: int = 0, user=Depends(current_user)):
        own_run(user["id"], rid)
        return {"events": [e.as_dict() for e in manager.events(rid) if e.id > after]}

    @app.get("/api/runs/{rid}/stream")
    def run_stream(rid: str, request: Request, after: int = 0, user=Depends(current_user)):
        """Server-sent events. Reconnect with the Last-Event-ID header (or ?after=) to continue without gaps."""
        r = own_run(user["id"], rid)
        last = int(request.headers.get("last-event-id") or after or 0)

        def gen():
            nonlocal last
            idle, finished = 0.0, False
            while not finished:
                batch = [e for e in manager.events(rid) if e.id > last]
                for e in batch:
                    last = e.id
                    yield f"id: {e.id}\nevent: {e.kind}\ndata: {json.dumps(e.as_dict(), ensure_ascii=False)}\n\n"
                    finished = finished or e.kind == "run_finished"
                if finished:
                    break
                live = manager.get_live(rid)
                if not batch and not (live and live.thread and live.thread.is_alive()):
                    break  # the worker is gone (finished earlier, or the server restarted) and nothing more will come
                idle += 0.25
                if idle >= 15:
                    idle = 0
                    yield ": keep-alive\n\n"
                time.sleep(0.25)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/runs/{rid}/cancel", response_model=m.OkOut, status_code=202)
    def cancel_run(rid: str, user=Depends(current_user)):
        """Stop a run midway. It ends as cancelled at its next step; anything it already created stays (delete it from the run page), and a release already
        building on Liara finishes there."""
        own_run(user["id"], rid)
        if not manager.cancel(rid):
            raise err(409, "run_not_active", "this run is not running any more")
        return {"ok": True}

    @app.post("/api/runs/{rid}/respond", response_model=m.OkOut)
    def respond(rid: str, body: RespondIn, user=Depends(current_user)):
        own_run(user["id"], rid)
        live = manager.get_live(rid)
        if not live or not live.thread.is_alive():
            raise err(404, "no_such_request", "that request is not waiting for an answer (it may have expired)")
        problem = live.channel.check(body.request_id, body.value)
        if problem == "bad_option":
            raise err(422, "bad_option", "that is not one of the offered options")
        if problem or not live.channel.respond(body.request_id, body.value):
            raise err(404, "no_such_request", "that request is not waiting for an answer (it may have expired)")
        return {"ok": True}

    @app.get("/api/runs/{rid}/report")
    def run_report(rid: str, user=Depends(current_user)):
        """The run as a Markdown document (names only, no values), as a download."""
        r = own_run(user["id"], rid)
        proj = db.one("SELECT name FROM projects WHERE id=?", (r["project_id"],))
        body = build_report(proj["name"] if proj else "project", r["mode"], r["status"], r["created_at"], manager.events(rid))
        return Response(body, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="report-{proj["name"] if proj else rid}.md"'})

    @app.post("/api/runs/{rid}/explain", response_model=m.ExplainOut)
    def explain_run(rid: str, user=Depends(current_user)):
        """A plain-language reading of the run's log from the language model (scrubbed first; no tools, no secrets)."""
        own_run(user["id"], rid)
        lang = user_settings(user["id"])["language"]
        try:
            return {"explanation": explain(manager.deps.llm(agent_settings.model_copy(update={"language": lang})), manager.events(rid), lang)}
        except ExplainError as e:
            raise err(e.status, e.code, str(e))
        except Exception:  # the provider is down, out of credit, or refused: say so without echoing its message (it may quote the request)
            raise err(502, "llm_failed", "the language model could not be reached")

    @app.post("/api/runs/{rid}/cleanup", response_model=m.CleanupOut)
    def cleanup(rid: str, body: CleanupIn, user=Depends(current_user)):
        r = own_run(user["id"], rid)
        proj = db.one("SELECT name FROM projects WHERE id=?", (r["project_id"],))
        live = manager.get_live(rid)
        if live and live.thread.is_alive():
            raise err(409, "run_active", "the run is still active")
        if body.confirm != proj["name"]:
            raise err(422, "confirm_mismatch", "type the project name to confirm", expected_hint=proj["name"][:1] + "...")
        token, team = token_of(user["id"])
        journal = Journal.load(manager.run_dir(rid) / "created.json")
        client = manager.deps.client(token, team, False, journal)
        deleted: dict[str, list[str]] = {"apps": [], "databases": [], "domains": [], "buckets": [], "mail_servers": []}
        try:
            remove_all(client, deleted)
        except (LiaraAPIError, NotOwnedError) as e:
            raise err(502, "cleanup_failed", "could not delete everything", deleted=deleted, detail=str(e)[:200])
        db.run("UPDATE runs SET cleaned_at=? WHERE id=?", (time.time(), rid))
        return {"deleted": deleted}

    # -- custom domains -------------------------------------------------------------------------------------------
    def live_app(uid: int, pid: int, want: str | None = None) -> tuple[Journal, str]:
        """The app a project currently has on Liara, and the journal that proves this agent created it. Domains are only ever attached to those."""
        own_project(uid, pid)
        runs = db.q("SELECT * FROM runs WHERE project_id=? AND user_id=? ORDER BY created_at DESC", (pid, uid))
        ok = [r for r in runs if r["status"] == "succeeded" and r["mode"] == "deploy" and not r["cleaned_at"]]
        if not ok:
            raise err(409, "no_live_app", "deploy the project first: a domain needs a running app")
        journal = Journal.load(manager.run_dir(ok[0]["id"]) / "created.json")
        urls = (json.loads(ok[0]["summary"]) if ok[0]["summary"] else {}).get("urls") or {}
        name = want or next((a for a in journal.apps if a in urls), journal.apps[0] if journal.apps else None)
        if not name or name not in journal.apps:
            raise err(409, "no_live_app", "this project has no app created by this agent")
        return journal, name

    @contextmanager
    def liara_errors():
        try:
            yield
        except (dom.DomainError, history.HistoryError) as e:
            raise err(e.status, e.code, str(e), params=e.params)
        except NotOwnedError:
            raise err(403, "not_owned", "this agent did not create that, so it will not change it")
        except LiaraAPIError as e:
            raise err(502, LIARA_STATUS_CODES.get(e.status, "liara_rejected"), f"Liara answered HTTP {e.status}", params={"status": e.status, "detail": e.body[:200]})
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            raise err(502, LIARA_STATUS_CODES.get(status, "liara_rejected"), f"Liara answered HTTP {status}", params={"status": status})
        except httpx.TransportError:
            raise err(502, "liara_unreachable", "could not reach Liara")

    def domain_client(uid: int, journal: Journal, dry_run: bool = False):
        token, team = token_of(uid)
        return manager.deps.client(token, team, dry_run, journal)

    def managed(journal: Journal, view: dict) -> dict:
        return {**view, "managed": any(d["id"] == view["id"] for d in journal.domains)}

    @app.get("/api/projects/{pid}/domains", response_model=m.DomainsOut)
    def list_domains(pid: int, user=Depends(current_user)):
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            client = domain_client(user["id"], journal)
            target = dom.route_target(app_name)
            out = [managed(journal, dom.domain_view(full, dom.zone_or_none(client, dom.zone_for(d["name"])) if d.get("name") else None, target))
                   for d in client.app_domains(app_name) for full in [dom._with_check(client, d)]]
        return {"app": app_name, "console_url": f"https://console.liara.ir/apps/{app_name}/domains", "domains": out}

    @app.get("/api/projects/{pid}/domains/guide", response_model=m.DomainGuidesOut)
    def domains_guide(pid: int, domain: str = "", user=Depends(current_user)):
        """What the person does at the place the domain is registered (IRNIC's panel for a .ir domain), per domain of the app, with Liara's own nameservers and records filled in."""
        out = list_domains(pid, user)
        want = domain.strip().lower()
        return {"guides": [registrar.guide(d) for d in out["domains"] if not want or d["name"].lower() == want]}

    @app.post("/api/projects/{pid}/domains/plan", response_model=m.DomainPlanOut)
    def plan_domain(pid: int, body: DomainPlanIn, user=Depends(current_user)):
        """What connecting the domain would do. Reads only; nothing is created."""
        journal, app_name = live_app(user["id"], pid, body.app)
        with liara_errors():
            return dom.plan_domain(domain_client(user["id"], journal), app_name, body.domain, use_liara_dns=body.use_liara_dns, zone=body.zone, www=body.www)

    @app.post("/api/projects/{pid}/domains", status_code=201, response_model=m.DomainOut)
    def connect_domain(pid: int, body: DomainCreateIn, user=Depends(current_user)):
        if not body.confirm:
            raise err(422, "confirm_required", "approve the plan first")
        journal, app_name = live_app(user["id"], pid, body.app)
        with liara_errors():
            view = dom.connect_domain(domain_client(user["id"], journal), app_name, body.domain, use_liara_dns=body.use_liara_dns, zone=body.zone, www=body.www)
        return managed(journal, view)

    @app.post("/api/projects/{pid}/domains/{did}/check", response_model=m.DomainOut)
    def check_domain(pid: int, did: str, user=Depends(current_user)):
        """'Check records': asks Liara to look again, and nudges the nameserver check of a pending Liara DNS zone."""
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            return managed(journal, dom.refresh_domain(domain_client(user["id"], journal), app_name, did))

    @app.get("/api/projects/{pid}/domains/{did}/dns-check", response_model=m.DnsCheckOut)
    def dns_check(pid: int, did: str, user=Depends(current_user)):
        """Asks the zone's own nameservers and the big public resolvers about each record of the domain. Reads DNS only; nothing is changed."""
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            client = domain_client(user["id"], journal)
            d = next((x for x in client.app_domains(app_name) if (x.get("_id") or x.get("id")) == did), None)
            if not d:
                raise dom.DomainError("domain_not_found", "that domain is not attached to the app", status=404)
            view = dom.domain_view(dom._with_check(client, d), None, dom.route_target(app_name))
        return dnscheck.check_domain(view["name"], dom.zone_for(view["name"]), view["records"])

    @app.post("/api/projects/{pid}/domains/{did}/ssl", response_model=m.DomainOut)
    def enable_ssl(pid: int, did: str, user=Depends(current_user)):
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            client = domain_client(user["id"], journal)
            client.enable_ssl(did)
            return managed(journal, dom.refresh_domain(client, app_name, did))

    @app.get("/api/projects/{pid}/domains/{did}/removal", response_model=m.RemovalPlanOut)
    def removal_plan(pid: int, did: str, user=Depends(current_user)):
        """The DNS records Liara added for this domain to a Liara DNS zone, which removing the domain can clean up too. Reads only."""
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            return dom.removal_plan(domain_client(user["id"], journal), app_name, did)

    @app.delete("/api/projects/{pid}/domains/{did}", response_model=m.DomainRemovedOut)
    def detach_domain(pid: int, did: str, remove_records: bool = False, user=Depends(current_user)):
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            return {"ok": True, "records_removed": dom.remove_domain(domain_client(user["id"], journal), app_name, did, remove_records)}

    # -- releases of newer code (the button on the project page, the chat) ---------------------------------------------------
    hook_locks: dict[int, threading.Lock] = {}

    def deploys_view(row) -> dict:
        deploys = db.q("SELECT * FROM auto_deploys WHERE project_id=? ORDER BY ts DESC LIMIT 10", (row["id"],))
        return {"deploys": [{"id": d["id"], "ts": d["ts"], "finished_at": d["finished_at"], "commit_id": d["commit_id"], "message": d["message"],
                             "state": d["state"], "error": d["error"], "tail": json.loads(d["tail"]) if d["tail"] else None, "run_id": d["run_id"]} for d in deploys]}

    @app.get("/api/projects/{pid}/deploys", response_model=m.DeploysOut)
    def deploys_get(pid: int, user=Depends(current_user)):
        """Releases of newer code made by this server (newest first): their state while they build, and how they ended."""
        return deploys_view(own_project(user["id"], pid))

    def run_auto_deploy(row, did: int, token: str, local: bool, run) -> None:
        """Release new code on the app, as a run (events the run page already knows how to show). `local`: the code is this server's own copy of the project (edited in the chat), not a fresh clone."""
        dest = project_dir(row["id"]).parent / f"hook-{did}"
        state, error, ok = "failed", None, False
        lines: list[str] = []
        nothing = {"apps": [], "databases": [], "database_ids": [], "buckets": [], "mail_servers": []}   # a release creates nothing: its run has nothing to delete
        failure: dict = {}
        try:
            journal, app_name = live_app(row["user_id"], row["id"])
            api_token, team = token_of(row["user_id"])
            msg = db.one("SELECT message FROM auto_deploys WHERE id=?", (did,))["message"] or "release"
            run.emit("run_started", {"project": row["name"], "dry_run": False, "release": True, "message": msg})
            for phase in ("analyze", "plan"):
                run.emit("phase_started", {"phase": phase})
                if phase == "plan":   # the architecture of the first deployment, so the page shows what this release goes to
                    before = next((r for r in db.q("SELECT id FROM runs WHERE project_id=? AND mode='deploy' AND status='succeeded' ORDER BY created_at DESC", (row["id"],))), None)
                    ev = next((e for e in manager.events(before["id"]) if e.kind == "plan_ready"), None) if before else None
                    if ev:
                        data = json.loads(json.dumps(ev.data))
                        apps = data.get("plan", {}).get("apps") or []
                        if len(apps) == 1:
                            apps[0]["name"] = app_name
                        run.emit("plan_ready", data)
                run.emit("phase_finished", {"phase": phase, **({"manifests": {}, "hints": []} if phase == "analyze" else {})})
            run.emit("phase_started", {"phase": "confirm"})
            run.emit("phase_finished", {"phase": "confirm", "approved": True})
            run.emit("phase_started", {"phase": "deploy"})
            step = f"app:{app_name}"
            run.emit("step_started", {"step": step})
            if not local:
                sources.clone_git(row["git_url"], dest, allowed_hosts=settings.hosts(), max_extracted_bytes=settings.max_extracted_mb * 1024 * 1024,
                                  max_files=settings.max_files, token=token, branch=row["git_branch"] or "")
            if local:   # the server's own copy (with the chat's edits) never holds the agent's earlier fixes: they are put onto a throw-away copy that is released
                shutil.copytree(project_dir(row["id"]), dest, ignore=shutil.ignore_patterns(".agent-state", ".git", "node_modules"))
            # what the agent had to fix to make it run is not in the repository (unless its pull request was merged): put it back
            sources.apply_saved_fixes(dest, project_dir(row["id"]) / ".agent-state" / "fixes.patch")
            client = manager.deps.client(api_token, team, False, journal)
            last_saved = [0.0]

            def follow(kind: str, text: str) -> None:   # every build line goes to the run page; the last few are also kept on the release's row
                lines.append(text.strip()[:200])
                run.emit(kind if kind in ("build", "build_error") else "build", {"step": step, "line": text})
                if time.time() - last_saved[0] > 1.5:
                    last_saved[0] = time.time()
                    db.run("UPDATE auto_deploys SET tail=? WHERE id=?", (json.dumps([x for x in lines if x][-8:]), did))
            out = history.redeploy(client, app_name, dest, project_dir(row["id"]) / ".agent-state", message=f"{'chat edit' if local else 'release'}: {msg}"[:120], emit=follow)
            ok, state = out["ok"], "succeeded" if out["ok"] else "failed"
            error = None if ok else f"the release ended {out['state']}"
            url = f"https://{app_name}.liara.run/"
            if ok:
                good, detail = _executor.verify_http(url)
                run.emit("test_result", {"step": step, "name": "http_200", "ok": good, "detail": detail, "url": url})
                if not good:
                    ok, state, error = False, "failed", detail
            run.emit("step_finished", {"step": step, "state": out["state"], "dry_run": False})
            run.emit("phase_finished", {"phase": "deploy", "ok": ok})
            if ok:
                run.emit("run_finished", {"ok": True, "dry_run": False, "release": True, "created": nothing, "urls": {app_name: url}, "fixes": [], "heal_attempts": 0, "follow_ups": []})
            else:
                failure = {"ok": False, "dry_run": False, "release": True, "stopped": "error", "failed_step": step, "reason": error, "reason_code": "release_failed",
                           "reason_params": {"state": str(out["state"]), "detail": error or ""}, "created": nothing, "urls": {}, "fixes": [], "heal_attempts": 0, "follow_ups": []}
        except (history.HistoryError, sources.SourceError) as e:
            error = e.code
            failure = {"ok": False, "dry_run": False, "release": True, "stopped": "error", "reason": e.code, "reason_code": "release_failed", "reason_params": {"state": "", "detail": e.code}, "created": nothing, "urls": {}}
        except Exception as e:  # a release must never take the server down; the person sees the code, not the message (it may hold details)
            error = type(e).__name__
            failure = {"ok": False, "dry_run": False, "release": True, "stopped": "error", "reason": error, "reason_code": "release_failed", "reason_params": {"state": "", "detail": error}, "created": nothing, "urls": {}}
        finally:
            shutil.rmtree(dest, ignore_errors=True)
            db.run("UPDATE auto_deploys SET state=?, ok=?, error=?, finished_at=?, tail=? WHERE id=?", (state, int(ok), error, time.time(), json.dumps([x for x in lines if x][-8:]) if lines else None, did))
            hook_locks[row["id"]].release()
        if failure:
            run.emit("phase_finished", {"phase": "deploy", "ok": False}) if not any(e.kind == "phase_finished" and e.data.get("phase") == "deploy" for e in run.events) else None
            run.emit("run_finished", failure)

    def start_release(row, message: str, token: str = "", local: bool = False) -> str:
        """Release new code on the project's running app as a run of its own, in the background. Returns the run's id: its page follows the build as it happens."""
        try:   # the daily cap is Liara's: a release past it would only be answered with HTTP 428, so say so first, in words
            q = collect_limits(row["user_id"], row["id"])["daily_deployments"]
        except Exception:  # a failed look must not block a release: Liara's own answer still comes
            q = {"remaining": None}
        if q["remaining"] == 0:
            raise err(409, "deploy_limit_reached", "this app has used its releases for the day on its plan", params={"used": q["used"], "cap": q["cap"], "tier": q.get("tier")})
        lock = hook_locks.setdefault(row["id"], threading.Lock())
        if not lock.acquire(blocking=False):
            raise err(409, "deploy_busy", "a deployment of this project is already running")
        try:
            did = db.run("INSERT INTO auto_deploys(project_id,ts,commit_id,message,state) VALUES (?,?,?,?,?)", (row["id"], time.time(), "", message, "running"))
            rid = manager.start_task(user_id=row["user_id"], project_id=row["id"], mode="release", work=lambda run: run_auto_deploy(dict(row), did, token, local, run))
        except BaseException:
            lock.release()
            raise
        db.run("UPDATE auto_deploys SET run_id=? WHERE id=?", (rid, did))
        return rid

    def start_redeploy(row, message: str) -> str:
        """Release the newest code of a Git project on its running app (a run, like any other)."""
        return start_release(row, message, clone_token(row))

    @app.post("/api/projects/{pid}/redeploy", status_code=202, response_model=m.StartedOut)
    def redeploy_now(pid: int, user=Depends(current_user)):
        """Release the newest code of the project's repository on the app this agent already runs (same settings, nothing new created)."""
        row = own_project(user["id"], pid)
        if row["source_kind"] != "git" or not row["git_url"]:
            raise err(409, "not_a_git_project", "only a project that came from a Git repository can pick up new code")
        live_app(user["id"], pid)
        return {"status": "started", "run_id": start_redeploy(row, "released from the page")}

    # -- pull request with the agent's fixes ------------------------------------------------------------------------------
    def pr_material(user: dict, pid: int):
        row = own_project(user["id"], pid)
        owner, repo = github.repo_of(row["git_url"])
        patch_file = project_dir(pid) / ".agent-state" / "fixes.patch"
        if not patch_file.is_file():
            raise err(409, "pr_no_patch", "the agent made no changes to this project, so there is nothing to propose")
        patch = patch_file.read_text(errors="ignore")
        patched = sorted((project_dir(pid) / ".agent-state" / "patched").glob("*"))
        files = github.changed_files(patch, patched[0]) if patched else []
        if not files:
            raise err(409, "pr_no_patch", "the agent made no changes to this project, so there is nothing to propose")
        token = clone_token(row)
        return row, owner, repo, patch, files, token

    @app.get("/api/projects/{pid}/pr", response_model=m.PrPreviewOut)
    def pr_preview(pid: int, user=Depends(current_user)):
        try:
            row, owner, repo, patch, files, _ = pr_material(user, pid)
        except github.GitHubError as e:
            raise err(e.status, e.code, str(e), params=e.params)
        return {"repo": f"{owner}/{repo}", "base": row["git_branch"], "files": [{"path": f["path"]} for f in files], "diff": patch[:100_000]}

    @app.post("/api/projects/{pid}/pr", status_code=201, response_model=m.PrCreatedOut)
    def pr_create(pid: int, body: ConfirmIn, user=Depends(current_user)):
        """Opens a pull request on the person's repository with the agent's fixes, on a new branch. Needs a token that can write (the one given for a private repo, or a new one)."""
        if not body.confirm:
            raise err(422, "confirm_required", "confirm to open the pull request")
        try:
            row, owner, repo, patch, files, saved = pr_material(user, pid)
            token = body.token or saved
            if not token:
                raise github.GitHubError("pr_needs_token", "an access token that can write to the repository is needed", 409)
            gh = github.GitHub(token, getattr(manager.deps, "github_http", None))
            base = row["git_branch"] or gh.default_branch(owner, repo)
            return gh.open_pull_request(owner, repo, base, files, "Make the project deployable on Liara",
                                        "These changes were needed to get the project to build and run on Liara. They were found by Liara Launchpad while deploying a copy of the project.\n\n```diff\n" + patch[:50_000] + "\n```")
        except github.GitHubError as e:
            raise err(e.status, e.code, str(e), params=e.params)
        except httpx.HTTPError:
            raise err(502, "github_unreachable", "could not reach GitHub")

    # -- deploy history and rollback ----------------------------------------------------------------------------------------
    @app.get("/api/projects/{pid}/releases", response_model=m.ReleasesOut)
    def list_project_releases(pid: int, user=Depends(current_user)):
        """The app's releases on Liara, newest first, with whether each can be rolled back to."""
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            return history.list_releases(domain_client(user["id"], journal), app_name, project_dir(pid) / ".agent-state")

    @app.post("/api/projects/{pid}/releases/{rid}/rollback", response_model=m.RollbackOut)
    def rollback_release(pid: int, rid: str, body: RollbackIn, user=Depends(current_user)):
        """Make an earlier release the running one again. Creates a new release (Liara builds nothing for a kept source it already knows, but it does deploy)."""
        if not body.confirm:
            raise err(422, "confirm_required", "approve the rollback first")
        journal, app_name = live_app(user["id"], pid)
        with liara_errors():
            out = history.rollback(domain_client(user["id"], journal), app_name, rid, project_dir(pid) / ".agent-state")
        url = f"https://{app_name}.liara.run/"
        serving = None
        if out["ok"]:
            serving, _ = _executor.verify_http(url, attempts=12)
        return {**out, "url": url, "serving": serving}

    # -- the assistant (chat) ----------------------------------------------------------------------------------------------
    chat_limit = RateLimiter(40, 300)
    chat_locks: dict[int, threading.Lock] = {}

    @contextmanager
    def tool_errors():
        """What the page's routes refuse with, said to the model as a sentence it can repeat."""
        try:
            with liara_errors():
                yield
        except HTTPException as e:
            d = e.detail if isinstance(e.detail, dict) else {}
            raise ToolError(str(d.get("message") or d.get("code") or "that did not work"))

    class UserActions(chat_tools.Actions):
        """The chat's hands: the same functions the pages' routes call, for one signed-in person."""

        def __init__(self, uid: int, pid: int):
            self.uid, self.project_id, self.user = uid, pid, {"id": uid, "username": ""}

        def project(self, name=None):
            if self.project_id:
                return project_view(own_project(self.uid, self.project_id))
            row = db.one("SELECT * FROM projects WHERE user_id=? AND name=?", (self.uid, sources.slug(name) if name else ""))
            if not row:
                raise ToolError("say which project: " + (", ".join(r["name"] for r in db.q("SELECT name FROM projects WHERE user_id=? ORDER BY id DESC LIMIT 20", (self.uid,))) or "there are none yet"))
            return project_view(row)

        def projects(self):
            return [project_view(r) for r in db.q("SELECT * FROM projects WHERE user_id=? ORDER BY id DESC", (self.uid,))]

        def app_name(self, p):
            with tool_errors():
                return live_app(self.uid, p["id"])[1]

        def client(self, p):
            with tool_errors():
                return domain_client(self.uid, live_app(self.uid, p["id"])[0])

        def releases(self, p):
            with tool_errors():
                return list_project_releases(p["id"], self.user)

        def rollback(self, p, release_id):
            """Runs in the background (building and waiting for the old version to answer takes minutes); the outcome shows in the recent deployments."""
            with tool_errors():
                live_app(self.uid, p["id"])
            lock = hook_locks.setdefault(p["id"], threading.Lock())
            if not lock.acquire(blocking=False):
                raise ToolError("a deployment of this project is already running")
            did = db.run("INSERT INTO auto_deploys(project_id,ts,commit_id,message,state) VALUES (?,?,?,?,?)", (p["id"], time.time(), release_id[:40], f"rollback to {release_id[:8]}", "running"))

            def work():
                state, error, ok = "failed", None, False
                try:
                    out = rollback_release(p["id"], release_id, RollbackIn(confirm=True), self.user)
                    ok = bool(out["ok"])
                    state, error = ("succeeded", None) if ok else ("failed", f"the release ended {out['state']}" + (f": {out.get('reason')}" if out.get("reason") else ""))
                except HTTPException as e:
                    error = (e.detail or {}).get("code") if isinstance(e.detail, dict) else "error"
                except Exception as e:  # never leave the row 'running'; only the kind of error is kept
                    error = type(e).__name__
                finally:
                    db.run("UPDATE auto_deploys SET state=?, ok=?, error=?, finished_at=? WHERE id=?", (state, int(ok), error, time.time(), did))
                    lock.release()
            threading.Thread(target=work, daemon=True).start()
            return {"started": True, "note": "going back now; ask for the recent deployments to see when it is done"}

        def redeploy(self, p):
            row = own_project(self.uid, p["id"])
            if row["source_kind"] != "git" or not row["git_url"]:
                raise ToolError("this project did not come from a Git repository")
            with tool_errors():
                live_app(self.uid, p["id"])
            try:
                rid = start_redeploy(row, "requested in the chat")
            except HTTPException as e:
                d = e.detail if isinstance(e.detail, dict) else {}
                if d.get("code") == "deploy_limit_reached":
                    p_ = d.get("params") or {}
                    raise ToolError(f"Liara allows this app {p_.get('cap')} releases a day on its plan and {p_.get('used')} were made in the last 24 hours: that is a limit of Liara's API, not of mine. "
                                    "Wait for it to reset, or move the app to a plan with a higher daily limit (Liara's silver tier allows 50, gold is unlimited)")
                raise ToolError("a deployment of this project is already running")
            return {"started": True, "run_id": rid, "note": "building now; the person is taken to the run page by itself"}

        def source_dir(self, p):
            return project_dir(own_project(self.uid, p["id"])["id"])

        def backup_dir(self, p):
            return project_dir(p["id"]).parent / "chat-undo"

        def deploy_local(self, p):
            row = own_project(self.uid, p["id"])
            with tool_errors():
                live_app(self.uid, p["id"])
            try:
                rid = start_release(row, "edits made in the chat", "", True)
            except HTTPException as e:
                d = e.detail if isinstance(e.detail, dict) else {}
                if d.get("code") == "deploy_limit_reached":
                    p_ = d.get("params") or {}
                    raise ToolError(f"Liara allows this app {p_.get('cap')} releases a day on its plan and {p_.get('used')} were made in the last 24 hours: that is a limit of Liara's API, not of mine. "
                                    "Wait for it to reset, or move the app to a plan with a higher daily limit (Liara's silver tier allows 50, gold is unlimited)")
                raise ToolError("a deployment of this project is already running")
            return {"started": True, "run_id": rid, "note": "building now; the person is taken to the run page by itself"}

        def new_empty_project(self, name):
            name = sources.slug(name or "my-app")
            pid = db.run("INSERT INTO projects(user_id,name,source_kind,git_url,created_at) VALUES (?,?,?,?,?)", (self.uid, name, "zip", None, time.time()))
            project_dir(pid).mkdir(parents=True)
            return {"id": pid, "name": name}

        def recent_releases(self, p):
            return deploys_view(own_project(self.uid, p["id"]))["deploys"]

        def last_run_log(self, p):
            r = db.one("SELECT id FROM runs WHERE project_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1", (p["id"], self.uid))
            return log_tail(manager.events(r["id"])) if r else ""

        def uptime(self, p):
            row = own_project(self.uid, p["id"])
            return {"watching": monitor_targets(row), **monitor.summary(db, p["id"])}

        def metrics(self, p):
            with tool_errors():
                return project_metrics(p["id"], 6, self.user)

        def limits(self, p):
            with tool_errors():
                return collect_limits(self.uid, p["id"])

        def diagnose(self, p):
            with tool_errors():
                return collect_diagnosis(self.uid, p["id"])

        def domains(self, p):
            with tool_errors():
                out = list_domains(p["id"], self.user)
            return {"domains": [{k: d.get(k) for k in ("name", "status", "ssl", "managed", "records")} for d in out["domains"]]}

        def domain_guide(self, p, domain):
            with tool_errors():
                return domains_guide(p["id"], domain, self.user)["guides"]

        def plan_domain(self, p, domain, www):
            with tool_errors():
                return plan_domain(p["id"], DomainPlanIn(domain=domain, www=www), self.user)

        def connect_domain(self, p, domain, www):
            with tool_errors():
                return connect_domain(p["id"], DomainCreateIn(domain=domain, www=www, confirm=True), self.user)

        def catalog(self):
            token, team = token_of(self.uid)
            with tool_errors():
                return manager.deps.api(token, team).live_catalog()

        def start_run(self, p, mode):
            with tool_errors():
                return start_run(p["id"], RunIn(mode=mode), self.user)

        def cleanup(self, p, confirm):
            r = next((x for x in db.q("SELECT * FROM runs WHERE project_id=? AND user_id=? ORDER BY created_at DESC", (p["id"], self.uid))
                      if x["status"] == "succeeded" and x["mode"] == "deploy" and not x["cleaned_at"]), None)
            if not r:
                raise ToolError("nothing is deployed for this project")
            with tool_errors():
                return cleanup(r["id"], CleanupIn(confirm=confirm), self.user)["deleted"]

        def find_ready_made(self, query):
            q = query.lower().strip()
            hits = [(n, a_) for n, a_ in oneclick.catalog().items() if a_["spec"] and (not q or q in n.lower() or q in (a_.get("about") or "").lower() or any(q in c.lower() for c in a_.get("categories") or []))]
            return [{"app": n, "about": (a_.get("about") or "")[:160]} for n, a_ in sorted(hits, key=lambda kv: -kv[1].get("installs", 0))[:8]]

        def install_ready_made(self, app_, name):
            with tool_errors():
                return create_oneclick_project(OneClickIn(app=app_, name=name), self.user)

        def create_from_git(self, url, name, branch):
            host = (urlparse(url).hostname or "").lower()
            name = sources.slug(name or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "project")
            pid = db.run("INSERT INTO projects(user_id,name,source_kind,git_url,git_branch,created_at) VALUES (?,?,?,?,?,?)", (self.uid, name, "git", url, branch or None, time.time()))
            dest = project_dir(pid)
            try:
                sources.clone_git(url, dest, allowed_hosts=settings.hosts(), max_extracted_bytes=settings.max_extracted_mb * 1024 * 1024, max_files=settings.max_files,
                                  token=github_login_token(self.uid) if host == "github.com" else "", branch=branch)
            except sources.SourceError as e:
                db.run("DELETE FROM projects WHERE id=?", (pid,))
                shutil.rmtree(dest.parent, ignore_errors=True)
                raise ToolError(str(e))
            return {"id": pid, "name": name}

        def github_repos(self):
            with tool_errors():
                return [{k: r.get(k) for k in ("full_name", "private", "description", "url") if k in r} for r in github_repos(self.user)["repos"][:40]]

        def link(self, path):
            return path

    def chat_scope(uid: int, project_id: int):
        row = own_project(uid, project_id) if project_id else None
        cid = chat_store.chat_id(db, uid, project_id)
        return row, cid, chat_store.DbStore(db, cid)

    def chat_engine(uid: int, row, store, project_id: int) -> ChatEngine:
        lang = user_settings(uid)["language"]
        llm = manager.deps.llm(agent_settings.model_copy(update={"language": lang}))
        if llm is None:
            raise err(503, "llm_unavailable", "no language model is configured on this server")
        return ChatEngine(llm, chat_tools.build_tools(UserActions(uid, project_id)), None, store, chat_tools.system_prompt(lang, dict(row) if row else None))

    def chat_busy(cid: int):
        lock = chat_locks.setdefault(cid, threading.Lock())
        if not lock.acquire(blocking=False):
            raise err(409, "chat_busy", "the assistant is still answering")
        return lock

    model_state = {"checked": 0.0, "ok": False}

    def model_available() -> bool:
        """A model is set up AND its address can be reached from this server (a good verdict is kept 5 minutes, a bad one 15 seconds; a failed chat call also marks it down)."""
        llm = manager.deps.llm(agent_settings)
        if llm is None:
            return False
        if time.time() - model_state["checked"] > (300 if model_state["ok"] else 15):   # a down verdict is rechecked soon: the model may come back
            model_state.update(checked=time.time(), ok=bool(getattr(llm, "reachable", lambda: True)()))
        return model_state["ok"]

    @app.get("/api/chat", response_model=m.ChatOut)
    def chat_get(project_id: int = 0, user=Depends(current_user)):
        row, cid, store = chat_scope(user["id"], project_id)
        return {**chat_store.view(store), "available": model_available()}

    @app.post("/api/chat/messages", response_model=m.ChatOut)
    def chat_send(body: ChatIn, user=Depends(current_user)):
        row, cid, store = chat_scope(user["id"], body.project_id)
        if not chat_limit.allow(str(user["id"])):
            raise err(429, "rate_limited", "too many messages; wait a few minutes")
        engine = chat_engine(user["id"], row, store, body.project_id)
        lock = chat_busy(cid)
        try:
            hidden = engine.send(body.text)
        except ChatBusy:
            raise err(409, "action_pending", "decide the proposed change first")
        except ValueError as e:
            raise err(409, "chat_full", str(e))
        except Exception as e:  # the provider is down or refused: say so without echoing its message (it may quote the request)
            if isinstance(e, HTTPException):
                raise
            log_model_failure(e)
            raise err(502, "llm_failed", "the language model could not be reached")
        finally:
            lock.release()
        return {**chat_store.view(store), "hidden_secret": hidden}

    def hand_over_after(uid: int, store, action_id: str, project_id: int) -> None:
        """A deployment approved in the general chat opens the project's run page: the conversation goes there with it."""
        a = None if project_id else store.get_action(action_id)
        if not a or a["tool"] != "start_run" or a["status"] != "done":
            return
        run = db.one("SELECT project_id FROM runs WHERE id=? AND user_id=?", (str((a["result"] or {}).get("id") or ""), uid))
        if run:
            chat_store.hand_over(db, uid, run["project_id"])

    @app.post("/api/chat/actions/{action_id}", response_model=m.ChatOut)
    def chat_decide(action_id: str, body: ChatDecisionIn, user=Depends(current_user)):
        row, cid, store = chat_scope(user["id"], body.project_id)
        engine = chat_engine(user["id"], row, store, body.project_id)
        lock = chat_busy(cid)
        try:
            engine.decide(action_id, body.decision, body.value, body.confirm_name)
            hand_over_after(user["id"], store, action_id, body.project_id)
        except ToolError as e:
            raise err(422, "action_invalid", str(e))
        except Exception as e:
            if isinstance(e, HTTPException):
                raise
            raise err(502, "llm_failed", "the language model could not be reached")
        finally:
            lock.release()
        return chat_store.view(store)

    def chat_error(e: Exception) -> dict:
        """The code and message a failed chat step is reported with (the same ones the plain endpoints raise)."""
        if isinstance(e, HTTPException) and isinstance(e.detail, dict):
            return {"code": e.detail.get("code", "error"), "message": e.detail.get("message", "")}
        if isinstance(e, ChatBusy):
            return {"code": "action_pending", "message": "decide the proposed change first"}
        if isinstance(e, ToolError):
            return {"code": "action_invalid", "message": str(e)}
        if isinstance(e, ValueError):
            return {"code": "chat_full", "message": str(e)}
        log_model_failure(e)
        model_state.update(checked=0.0)   # look again next time the page asks
        return {"code": "llm_failed", "message": "the language model could not be reached"}

    def chat_stream(engine: ChatEngine, store, lock, step) -> StreamingResponse:
        """Runs one chat step in a worker thread (so it finishes and is saved even if the page goes away) and relays its live events as server-sent events,
        ending with {type: done, ...the conversation} or {type: error, code, message}."""
        q: queue.Queue = queue.Queue()
        engine.on_event = q.put

        def work():
            try:
                q.put({"type": "done", **step()})
            except Exception as e:
                q.put({"type": "error", **chat_error(e)})
            finally:
                q.put(None)
                lock.release()
        threading.Thread(target=work, daemon=True).start()

        def gen():
            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                if ev is None:
                    return
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat/messages/stream")
    def chat_send_stream(body: ChatIn, user=Depends(current_user)):
        """Like POST /api/chat/messages, but the answer arrives live as server-sent events (step, delta, tool, card, then done)."""
        row, cid, store = chat_scope(user["id"], body.project_id)
        if not chat_limit.allow(str(user["id"])):
            raise err(429, "rate_limited", "too many messages; wait a few minutes")
        engine = chat_engine(user["id"], row, store, body.project_id)
        if store.pending():
            raise err(409, "action_pending", "decide the proposed change first")
        lock = chat_busy(cid)

        def step():
            hidden = engine.send(body.text)
            return {**chat_store.view(store), "hidden_secret": hidden}
        return chat_stream(engine, store, lock, step)

    @app.post("/api/chat/actions/{action_id}/stream")
    def chat_decide_stream(action_id: str, body: ChatDecisionIn, user=Depends(current_user)):
        row, cid, store = chat_scope(user["id"], body.project_id)
        engine = chat_engine(user["id"], row, store, body.project_id)
        lock = chat_busy(cid)

        def step():
            engine.decide(action_id, body.decision, body.value, body.confirm_name)
            hand_over_after(user["id"], store, action_id, body.project_id)
            return chat_store.view(store)
        return chat_stream(engine, store, lock, step)

    @app.delete("/api/chat", response_model=m.OkOut)
    def chat_clear(project_id: int = 0, user=Depends(current_user)):
        _, cid, _ = chat_scope(user["id"], project_id)
        chat_store.clear(db, cid)
        return {"ok": True}

    @app.get("/api/limits", response_model=m.LimitsOut)
    def limits():
        """The numbers behind the upload/Git error messages, so the UI can show them before the user hits them."""
        return {"max_upload_mb": settings.max_upload_mb, "max_extracted_mb": settings.max_extracted_mb, "max_files": settings.max_files,
                "git_hosts": sorted(settings.hosts()), "max_concurrent_runs": settings.max_concurrent_runs_per_user,
                "approval_timeout_seconds": settings.request_timeout_seconds, "max_heal_attempts": settings.max_heal_attempts, "github_oauth": bool(settings.github_client_id and settings.github_client_secret), "registration_code_required": bool(settings.registration_code)}

    _mount_frontend(app, settings)
    return app


class _GZipExceptStreams:
    """gzip for pages, scripts and JSON; never for the live event stream (compression would hold events back in a buffer)."""

    def __init__(self, app, minimum_size: int = 1024):
        self.app, self.gzip = app, GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].endswith("/stream"):
            return await self.app(scope, receive, send)
        return await self.gzip(scope, receive, send)


def _mount_frontend(app: FastAPI, settings: WebSettings) -> None:
    """Serve the built single-page app. Unknown non-API paths get index.html so client-side routes work on reload."""
    root = settings.static_dir.resolve()
    if not (root / "index.html").is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, {"code": "not_found", "message": "no such endpoint"})
        target = (root / path).resolve()
        if path and root in target.parents and target.is_file():  # never serve anything outside the build folder
            # hashed build files never change: let browsers keep them for a year
            return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"} if path.startswith("assets/") else {})
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
