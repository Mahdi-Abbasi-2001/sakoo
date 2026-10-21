"""What the chat agent can do. Each tool is a thin wrapper over something the web app already does for a person who clicks a button, so the chat can never
do more than the pages can: it only needs fewer clicks.

READ tools look and report (names of variables, never values; logs scrubbed by the engine). CHANGE tools never run until the person approves a card.
The app supplies `Actions` (closures over the logged-in person's account); tests supply a fake.
"""
from __future__ import annotations

import re
from typing import Any

from liara_agent.agent.prompts import PERSIAN_STYLE
from liara_agent.chat.engine import Tool, ToolError
from liara_agent.web import chat_code
from liara_agent.liara.pricing import VAT, project_cost

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,99}$")
EMPTY = {"type": "object", "properties": {}}


class Actions:
    """Everything the tools need from the app. Methods raise ToolError with a sentence the model can repeat."""
    project_id: int = 0          # 0 = the general chat on the dashboard

    def project(self, name: str | None = None) -> dict: ...                  # the project_view dict (this chat's, or the one called `name`)
    def projects(self) -> list[dict]: ...                                    # all of the person's projects, project_view dicts
    def app_name(self, project: dict) -> str: ...                            # the live Liara app of a project (ToolError when nothing is deployed)
    def client(self, project: dict): ...                                     # a LiaraClient limited to what the agent created for that project
    def releases(self, project: dict) -> dict: ...
    def rollback(self, project: dict, release_id: str) -> dict: ...
    def redeploy(self, project: dict) -> dict: ...
    def recent_releases(self, project: dict) -> list[dict]: ...
    def last_run_log(self, project: dict) -> str: ...
    def uptime(self, project: dict) -> dict: ...
    def metrics(self, project: dict) -> dict: ...
    def diagnose(self, project: dict) -> dict: ...
    def limits(self, project: dict) -> dict: ...
    def domains(self, project: dict) -> dict: ...
    def plan_domain(self, project: dict, domain: str, www: bool) -> dict: ...
    def connect_domain(self, project: dict, domain: str, www: bool) -> dict: ...
    def domain_guide(self, project: dict, domain: str) -> list[dict]: ...
    def catalog(self): ...                                                   # LiveCatalog
    def start_run(self, project: dict, mode: str) -> dict: ...
    def cleanup(self, project: dict, confirm: str) -> dict: ...
    def find_ready_made(self, query: str) -> list[dict]: ...
    def install_ready_made(self, app: str, name: str) -> dict: ...
    def create_from_git(self, url: str, name: str, branch: str) -> dict: ...
    def github_repos(self) -> list[dict]: ...
    def source_dir(self, project: dict): ...                                 # Path of this server's copy of the project's code
    def backup_dir(self, project: dict): ...                                 # Path where the last edit's old files are kept
    def deploy_local(self, project: dict) -> dict: ...                       # release this server's copy of the code on the running app
    def new_empty_project(self, name: str) -> dict: ...
    def link(self, path: str) -> str: ...


def _p(**props: Any) -> dict:
    req = [k for k, v in props.items() if v.pop("required", False)]
    return {"type": "object", "properties": props, "required": req}


S = lambda desc, **kw: {"type": "string", "description": desc, **kw}  # noqa: E731


def W(code: str, **params) -> dict:
    """A warning on a card: the page words it from the code (so it is translated); the English text is only a fallback."""
    return {"code": code, "params": params}


def build_tools(a: Actions) -> list[Tool]:
    global_chat = a.project_id == 0

    def proj(args: dict) -> dict:
        return a.project(args.get("project") or None)

    # -- reads -----------------------------------------------------------------------------------------------------------------
    def list_projects(_c, args):
        return [{"name": p["name"], "source": p["source_kind"], "git_url": p["git_url"], "live": bool(p["live"]), "down": p["down"],
                 "last_run": (p["last_run"] or {}).get("status"), "urls": (p["live"] or {}).get("urls")} for p in a.projects()]

    def get_status(_c, args):
        p = proj(args)
        live = p["live"]
        return {"name": p["name"], "source": p["source_kind"], "git_url": p["git_url"], "branch": p["git_branch"], "down_now": p["down"],
                "last_run": {k: (p["last_run"] or {}).get(k) for k in ("status", "mode", "finished_at")} if p["last_run"] else None,
                "last_result": (p["last_run"] or {}).get("result"),
                "live": None if not live else {"urls": live["urls"], "resources": [{k: r.get(k) for k in ("kind", "id", "plan", "tier_label", "monthly_price")} for r in live["resources"]],
                                               "monthly_toman_incl_vat": live["monthly_incl_vat"], "free_address_expires_at": live["free_address_expires_at"]},
                "uptime": a.uptime(p) if live else None}

    def get_releases(_c, args):
        r = a.releases(proj(args))
        return {"current": r["current"], "releases": [{k: x.get(k) for k in ("id", "state", "current", "created_at", "message", "commit", "rollback")} for x in r["releases"][:12]]}

    def get_logs(_c, args):
        p = proj(args)
        lines = a.client(p).runtime_logs(a.app_name(p))
        return {"lines": lines[-60:]}

    def get_last_run(_c, args):
        return {"log": a.last_run_log(proj(args)) or "(the last run has no build output or failure)"}

    def get_env_names(_c, args):
        p = proj(args)
        return {"names": sorted(a.client(p).app_envs(a.app_name(p)))}   # names only: the values never leave the server

    def get_metrics(_c, args):
        m = a.metrics(proj(args))
        out = {}
        for k in ("cpu", "memory"):
            vals = [x["value"] for x in m.get(k) or []]
            out[k] = {"now": vals[-1], "max": max(vals), "avg": round(sum(vals) / len(vals), 4), "points": len(vals)} if vals else None
        return {"hours": m.get("hours"), **out, "units": "cpu in cores, memory in bytes"}

    def diagnose_app(_c, args):
        out = a.diagnose(proj(args))
        return {k: v for k, v in out.items() if k not in ("checked_at", "url")}

    def get_domains(_c, args):
        return a.domains(proj(args))

    def quota_warning(p: dict) -> list[dict]:
        """A release uses one of the app's releases for the day: say so when only a few are left (and nothing when the number cannot be read)."""
        try:
            q = a.limits(p)["daily_deployments"]
        except ToolError:
            return []
        return [W("deploy_quota", used=q["used"], cap=q["cap"])] if q["remaining"] is not None and q["remaining"] <= 3 else []

    def get_limits(_c, args):
        return a.limits(proj(args))

    def domain_guide(_c, args):
        guides = a.domain_guide(proj(args), str(args.get("domain") or ""))
        if not guides:
            raise ToolError("no domain is attached to this project yet: add_domain first")
        return {"guides": guides}

    def get_costs(_c, args):
        p = proj(args)
        live = p["live"]
        if not live:
            return {"live": False, "note": "nothing is deployed, so nothing is being billed by this agent's resources"}
        return {"hourly_toman_incl_vat": live["hourly_incl_vat"], "monthly_toman_incl_vat": live["monthly_incl_vat"],
                "resources": [{k: r.get(k) for k in ("kind", "id", "plan", "tier_label", "hourly_price", "monthly_price")} for r in live["resources"]]}

    def list_plans(_c, args):
        cat = a.catalog()
        out = []
        for pid_, plan in cat.project_plans.items():
            if plan.available and pid_.endswith("-g2"):
                out.append({"plan": pid_, "ram_gb": plan.ram.amount, "cpu": plan.cpu.amount, "disk_gb": plan.volume,
                            "monthly_toman_incl_vat": int(round(project_cost(cat, pid_).monthly_with_vat, -2))})
        return sorted(out, key=lambda x: x["monthly_toman_incl_vat"])

    def find_ready_made(_c, args):
        return a.find_ready_made(str(args.get("query") or ""))

    def my_repos(_c, args):
        return a.github_repos()

    def recent_releases(_c, args):
        return a.recent_releases(proj(args))

    # -- changes ---------------------------------------------------------------------------------------------------------------
    def check_env(args):
        name = str(args.get("name") or "")
        if not ENV_NAME.match(name):
            raise ToolError("a variable name is letters, digits and underscores, and does not start with a digit")
        return name

    def d_set_env(_c, args):
        name = check_env(args)
        p = proj(args)
        return {"code": "set_env", "params": {"name": name, "project": p["name"]}, "summary": f"Set the variable {name} on {p['name']} and restart it",
                "needs_value": True, "value_label": name, "confirm_name": None, "warnings": [W("restart")]}

    def r_set_env(_c, args):
        name, p = check_env(args), proj(args)
        if not args.get("_value"):
            raise ToolError("no value was given")
        a.client(p).merge_envs(a.app_name(p), {name: args["_value"]})
        return {"set": name, "note": "saved; the app restarts to use it", "link": a.link(f"/projects/{p['id']}")}

    def d_unset_env(_c, args):
        name, p = check_env(args), proj(args)
        return {"code": "unset_env", "params": {"name": name, "project": p["name"]}, "summary": f"Remove the variable {name} from {p['name']} and restart it",
                "needs_value": False, "confirm_name": None, "warnings": [W("restart")]}

    def r_unset_env(_c, args):
        name, p = check_env(args), proj(args)
        c = a.client(p)
        if name not in c.app_envs(a.app_name(p)):
            raise ToolError(f"{name} is not set on this app")
        c.merge_envs(a.app_name(p), {name: None})
        return {"removed": name}

    def _plan_delta(p, plan):
        cat = a.catalog()
        if plan not in cat.project_plans or not cat.project_plans[plan].available:
            raise ToolError(f"{plan} is not an available plan: ask list_plans for the choices")
        app_res = next((r for r in (p["live"] or {}).get("resources", []) if r.get("kind") == "app"), None)
        if not app_res:
            raise ToolError("there is no running app to resize")
        tier = {"silver": "standard", "gold": "pro"}.get(app_res.get("tier_label") or "", app_res.get("tier") or "basic")
        try:
            old, new = project_cost(cat, app_res["plan"], tier).hourly, project_cost(cat, plan, tier).hourly
        except KeyError:
            raise ToolError(f"{plan} does not exist in the {tier} tier this app uses")
        return app_res, tier, round((new - old) * (1 + VAT), 2)

    def d_resize(_c, args):
        p, plan = proj(args), str(args.get("plan") or "")
        app_res, _tier, delta = _plan_delta(p, plan)
        if plan == app_res["plan"]:
            raise ToolError("the app is already on that plan")
        return {"code": "resize_plan", "params": {"project": p["name"], "from": app_res["plan"], "to": plan}, "summary": f"Move {p['name']} from {app_res['plan']} to {plan}",
                "needs_value": False, "confirm_name": None, "price_delta_hourly": delta, "warnings": [W("restart_brief")]}

    def r_resize(_c, args):
        p, plan = proj(args), str(args.get("plan") or "")
        _plan_delta(p, plan)
        a.client(p).resize_app(a.app_name(p), plan)
        return {"plan": plan, "link": a.link(f"/projects/{p['id']}")}

    def d_rollback(_c, args):
        p, rid = proj(args), str(args.get("release_id") or "")
        rel = next((x for x in a.releases(p)["releases"] if x["id"] == rid), None)
        if not rel:
            raise ToolError("that release is not in the app's history: ask get_releases for the ids")
        if rel["current"]:
            raise ToolError("that release is already the running one")
        if not rel.get("rollback"):
            raise ToolError("that release cannot be rolled back to (it never ran fine, or its code was not kept)")
        return {"code": "rollback", "params": {"project": p["name"], "release": rid[:8], "message": rel.get("message") or ""}, "summary": f"Go back to release {rid[:8]} on {p['name']}",
                "needs_value": False, "confirm_name": None, "warnings": [W("rebuild"), *quota_warning(p)]}

    def r_rollback(_c, args):
        p = proj(args)
        out = a.rollback(p, str(args["release_id"]))
        return {**out, "link": a.link(f"/projects/{p['id']}")}

    def d_redeploy(_c, args):
        p = proj(args)
        if p["source_kind"] != "git":
            raise ToolError("only a project that came from a Git repository can pick up new code: a zip has no newer version")
        a.app_name(p)
        return {"code": "redeploy", "params": {"project": p["name"], "branch": p["git_branch"] or ""}, "summary": f"Download the newest code of {p['name']} and release it",
                "needs_value": False, "confirm_name": None, "warnings": [W("nothing_new"), W("build_minutes"), *quota_warning(p)]}

    def r_redeploy(_c, args):
        p = proj(args)
        out = a.redeploy(p)
        return {**out, "done": True, "link": a.link(f"/runs/{out['run_id']}")}

    def d_add_domain(_c, args):
        p, domain = proj(args), str(args.get("domain") or "").strip().lower()
        plan = a.plan_domain(p, domain, bool(args.get("www")))
        return {"code": "add_domain", "params": {"project": p["name"], "domain": domain}, "summary": f"Connect {domain} to {p['name']}",
                "needs_value": False, "confirm_name": None, "warnings": [W("dns_records")]}

    def r_add_domain(_c, args):
        p = proj(args)
        out = a.connect_domain(p, str(args["domain"]).strip().lower(), bool(args.get("www")))
        return {"connected": out.get("name"), "records_to_add": out.get("records"), "link": a.link(f"/projects/{p['id']}")}

    def d_start_run(_c, args):
        p, mode = proj(args), args.get("mode") or "preview"
        if mode not in ("preview", "deploy"):
            raise ToolError("mode is preview or deploy")
        if mode == "deploy" and p["live"]:
            raise ToolError("this project is already online, so a new deployment would only be refused (its app and database exist). To put new code on it use deploy_edits "
                            "(releases this server's copy, with any edits made here) or, for a project from Git when the person wants the repository's newest code, redeploy")
        return {"code": "start_run_" + mode, "params": {"project": p["name"]}, "summary": f"{'Preview' if mode == 'preview' else 'Deploy'} {p['name']}",
                "needs_value": False, "confirm_name": None, "warnings": [W("approve_each")] if mode == "deploy" else []}

    def r_start_run(_c, args):
        p = proj(args)
        out = a.start_run(p, args.get("mode") or "preview")
        return {**out, "link": a.link(f"/runs/{out['id']}"), "note": "the person is taken to the run page by itself: do not tell them to open it and do not write its address"}

    def d_cleanup(_c, args):
        p = proj(args)
        if not p["live"]:
            raise ToolError("nothing is deployed for this project")
        names = ", ".join(f"{r['kind']} {r['id']}" for r in p["live"]["resources"])
        return {"code": "cleanup", "params": {"project": p["name"], "resources": names}, "summary": f"DELETE everything created for {p['name']} on Liara: {names}",
                "needs_value": False, "confirm_name": p["name"], "warnings": [W("irreversible")]}

    def r_cleanup(_c, args):
        p = proj(args)
        return {"deleted": a.cleanup(p, p["name"])}

    def r_install(_c, args):
        out = a.install_ready_made(str(args["app"]), str(args.get("name") or ""))
        return {**out, "done": True, "link": a.link(f"/projects/{out['id']}"), "note": "the project now exists (nothing is on Liara yet); the next step is start_run with mode deploy: the run page shows the plan and price and asks for approval before anything is created"}

    def r_from_git(_c, args):
        out = a.create_from_git(str(args["url"]), str(args.get("name") or ""), str(args.get("branch") or ""))
        return {**out, "done": True, "link": a.link(f"/projects/{out['id']}"), "note": "the project now exists (nothing is on Liara yet); the next step is start_run with mode deploy: the run page shows the plan and price and asks for approval before anything is created"}

    # -- build mode: read and edit the project's code ---------------------------------------------------------------------------
    def t_list_files(_c, args):
        return chat_code.list_files(a.source_dir(proj(args)), str(args.get("under") or ""))

    def t_read_file(_c, args):
        return chat_code.read_file(a.source_dir(proj(args)), str(args.get("path") or ""), int(args.get("start") or 1))

    def t_search(_c, args):
        return chat_code.search(a.source_dir(proj(args)), str(args.get("query") or ""))

    def d_edit(_c, args):
        p = proj(args)
        plan = chat_code.plan_changes(a.source_dir(p), args.get("files") or [])
        warns = [W("edit_local")] + ([W("edit_not_in_repo")] if p["source_kind"] == "git" else [])
        return {"code": "edit_files", "params": {"project": p["name"], "n": len(plan["files"])}, "summary": str(args.get("summary") or f"Change {len(plan['files'])} file(s) of {p['name']}")[:200],
                "files": plan["files"], "diff": plan["diff"], "needs_value": False, "confirm_name": None, "warnings": warns}

    def r_edit(_c, args):
        p = proj(args)
        root = a.source_dir(p)
        done = chat_code.apply(root, chat_code.plan_changes(root, args.get("files") or []), a.backup_dir(p))
        return {"changed": done, "done": True, "link": a.link(f"/projects/{p['id']}"),
                "note": "saved in this server's copy of the project; nothing is online yet. If the app is running, deploy_edits releases it; otherwise start_run with mode deploy opens the run page, which shows the plan and price and asks for approval first"}

    def d_undo(_c, args):
        p = proj(args)
        if not (a.backup_dir(p) / "manifest.json").is_file():
            raise ToolError("there is no edit to undo")
        return {"code": "undo_edit", "params": {"project": p["name"]}, "summary": f"Undo the last edit of {p['name']}", "needs_value": False, "confirm_name": None, "warnings": []}

    def r_undo(_c, args):
        p = proj(args)
        return {"restored": chat_code.undo(a.source_dir(p), a.backup_dir(p)), "done": True}

    def d_deploy_edits(_c, args):
        p = proj(args)
        a.app_name(p)
        return {"code": "deploy_edits", "params": {"project": p["name"]}, "summary": f"Release this server's copy of {p['name']} (with your edits) on its running app",
                "needs_value": False, "confirm_name": None, "warnings": [W("nothing_new"), W("build_minutes"), *quota_warning(p)] + ([W("edit_not_in_repo")] if p["source_kind"] == "git" else [])}

    def r_deploy_edits(_c, args):
        p = proj(args)
        out = a.deploy_local(p)
        return {**out, "done": True, "link": a.link(f"/runs/{out['run_id']}")}

    def r_new_project(_c, args):
        out = a.new_empty_project(str(args["name"]))
        return {**out, "done": True, "link": a.link(f"/projects/{out['id']}"), "note": "the project is empty: now propose its files with edit_files (project = this name)"}

    pj = {"project": S("Name of the project; leave out in a project's own chat")}
    tools: list[Tool] = [
        Tool("get_status", "Current state of a project: where its code came from, whether it is live and answering, its addresses and resources, and the last run.", _p(**pj), get_status),
        Tool("get_costs", "What the project's live resources cost now (toman, with VAT).", _p(**pj), get_costs),
        Tool("get_logs", "The last lines the running app printed (errors, requests).", _p(**pj), get_logs),
        Tool("get_last_run", "The tail of the last deployment attempt's build output and failures, with what the agent diagnosed.", _p(**pj), get_last_run),
        Tool("get_releases", "The app's releases (versions), newest first, and which can be rolled back to.", _p(**pj), get_releases),
        Tool("get_env_names", "The NAMES of the app's environment variables (never their values).", _p(**pj), get_env_names),
        Tool("get_metrics", "CPU and memory use of the running app over the last hours.", _p(**pj), get_metrics),
        Tool("diagnose_app", "Find out why the RUNNING app errors, is down or is slow: probes the site, reads the logs, releases, CPU and memory against the plan, and returns the likely causes "
                             "most likely first, each with evidence and the fix (a `tool` and `args` when a change card can do it).", _p(**pj), diagnose_app),
        Tool("get_limits", "How many releases Liara allows this app today and how many were used, and what the agent cannot do because Liara's API has no call for it (codes).", _p(**pj), get_limits),
        Tool("domain_guide", "The steps the person must do themselves where the domain is registered (for a .ir domain, IRNIC's panel), with Liara's nameservers and records filled in.",
             _p(domain=S("a domain attached to the project; empty for all"), **pj), domain_guide),
        Tool("get_domains", "Custom domains attached to the app and their DNS/SSL state.", _p(**pj), get_domains),
        Tool("get_recent_releases", "Releases of newer code made by this server (redeploy, rollback, edits from the chat), newest first, with how each ended.", _p(**pj), recent_releases),
        Tool("list_plans", "Hardware plans that the app can be moved to, with their monthly price in toman including VAT.", EMPTY, list_plans),
        Tool("set_env", "Set an environment variable on the running app. NEVER ask the person for the value in the chat: just call this tool with the name; "
                        "the person types the value in a private box on the approval card.", _p(name=S("Variable name", required=True), **pj), lambda c, x: r_set_env(c, x), change=True, describe=d_set_env),
        Tool("unset_env", "Remove an environment variable from the running app.", _p(name=S("Variable name", required=True), **pj), r_unset_env, change=True, describe=d_unset_env),
        Tool("resize_plan", "Move the app to another hardware plan of the same feature tier (restarts it).", _p(plan=S("Plan id from list_plans", required=True), **pj), r_resize, change=True, describe=d_resize),
        Tool("rollback", "Go back to an earlier release of the app.", _p(release_id=S("Release id from get_releases", required=True), **pj), r_rollback, change=True, describe=d_rollback),
        Tool("redeploy", "Release the newest code of the project's Git repository on the existing app.", _p(**pj), r_redeploy, change=True, describe=d_redeploy),
        Tool("add_domain", "Connect a custom domain to the app.", _p(domain=S("e.g. shop.example.com", required=True), www={"type": "boolean", "description": "also attach www"}, **pj), r_add_domain, change=True, describe=d_add_domain),
        Tool("start_run", "Start the agent's deployment pipeline for a project: 'preview' only shows the plan, 'deploy' creates things after the person approves each step on the run page.",
             _p(mode={"type": "string", "enum": ["preview", "deploy"]}, **pj), r_start_run, change=True, describe=d_start_run),
        Tool("list_files", "List the files of the project's code (not secrets or dependencies).", _p(under=S("a folder to look inside"), **pj), t_list_files),
        Tool("read_file", "Read a text file of the project's code, from a line (5000 characters at a time; next_start says where to continue).", _p(path=S("file path", required=True), start={"type": "integer", "description": "first line"}, **pj), t_read_file),
        Tool("search_code", "Find a word or phrase in the project's code.", _p(query=S("text to find", required=True), **pj), t_search),
        Tool("edit_files", "Propose changes to the project's code. Each entry is {path, content} (write the whole file), {path, replace: [{find, with}]} (exact text that appears once) or {path, delete: true}. "
                           "The person reads the diff on a card and approves. Nothing is online until deploy_edits or a run.",
             _p(summary=S("one sentence: what and why", required=True), files={"type": "array", "items": {"type": "object"}, "description": "the changes"}, **pj), r_edit, change=True, describe=d_edit),
        Tool("undo_last_edit", "Put back the files as they were before the last applied edit.", _p(**pj), r_undo, change=True, describe=d_undo),
        Tool("deploy_edits", "Release this server's (edited) copy of the code on the project's running app.", _p(**pj), r_deploy_edits, change=True, describe=d_deploy_edits),
        Tool("delete_everything", "Delete everything this agent created on Liara for the project (the app, databases, disks, domains). Dangerous.", _p(**pj), r_cleanup, change=True, describe=d_cleanup),
    ]
    if global_chat:
        tools += [
            Tool("list_projects", "All of the person's projects with their state.", EMPTY, list_projects),
            Tool("find_ready_made", "Search Liara's ready-made apps (WordPress, Ghost, N8N, Uptime Kuma...) by a word.", _p(query=S("What to look for", required=True)), find_ready_made),
            Tool("my_github_repos", "The person's GitHub repositories (only works when GitHub is connected).", EMPTY, my_repos),
            Tool("new_project", "Start an EMPTY project to build from the person's description; fill it with edit_files, then start_run with mode deploy.", _p(name=S("project name", required=True)), r_new_project),
            Tool("install_ready_made", "Make a project that installs a ready-made Liara app (use find_ready_made for its exact name).", _p(app=S("exact ready-made app name", required=True), name=S("project name")), r_install),
            Tool("create_project_from_git", "Download a public (or the person's connected GitHub) repository as a new project, ready to be deployed.",
                 _p(url=S("https://github.com/owner/repo", required=True), name=S("project name"), branch=S("branch")), r_from_git),
        ]
        for t in tools:   # in the general chat every project tool needs to be told which project
            if "project" in t.parameters.get("properties", {}):
                t.parameters["properties"]["project"]["description"] = "Name of the project (required here: see list_projects)"
    return tools


def system_prompt(language: str, project: dict | None) -> str:
    where = (f"This conversation is about the project {project['name']!r}." if project else
             "This is the person's general conversation: they may ask about any project, or want a new one (from a Git repository or a ready-made Liara app).")
    return (
        "You are Liara Launchpad's assistant. You help a developer who does not know deployment to put their project on Liara (an Iranian cloud platform) and to look after it "
        "afterwards. You act ONLY through the tools you are given; you cannot run commands, read the person's code or open other websites. " + where + "\n"
        "Rules:\n"
        "- Look before you answer: use the read tools for facts about the project instead of guessing. Never invent a name, price, address or release.\n"
        "- Changes (set_env, resize_plan, rollback, redeploy, add_domain, start_run, delete_everything, edit_files, deploy_edits) are NOT done by you: "
        "calling the tool shows the person a card, and only their approval runs it. Say in one sentence what you propose and why, call the tool, and stop. Do not ask 'shall I?' in text first for small things; the card is the confirmation.\n"
        "- When the person asks to try again or to deploy, call start_run with mode deploy: the run page itself asks for approval before anything is created. Use mode preview only when they ask to see what would be built without building it.\n"
        "- Making a project record (new_project, install_ready_made, create_project_from_git) happens at once, with no card: it costs nothing and touches nothing on Liara. Right after it, call start_run with mode deploy "
        "so the person is taken to the run page, where the plan and the price are shown and each creation is approved.\n"
        "- NEVER ask for, repeat or guess a secret (password, token, key) in the chat. For an environment variable call set_env with its name only: the value is typed on the card.\n"
        "- When the person says the site shows an error (500, 502...), is slow, down or behaving strangely, call diagnose_app FIRST. Explain in plain words the most likely cause and the "
        "evidence (the log line, the memory or CPU percentage, the release that was just made), mention a second cause only if it is close, then propose the fix: when a fix names a `tool`, call that tool with "
        "its `args` (the card is the person's approval). If the fix is a code change, read the file the error points to and propose an edit_files card. After a change is done, call diagnose_app again and "
        "say plainly whether the problem is gone. Never claim a cause the signals do not show.\n"
        "- For a custom domain, after add_domain (or whenever the person asks how to point their domain, mentions IRNIC, nic.ir, nameservers or DNS), call domain_guide and walk them through its steps "
        "in their language, a few at a time: they do these at the registrar themselves because you cannot log in there. Say how long it takes, then offer to check the DNS (get_domains) afterwards. "
        "Give the nameservers and records exactly as the guide has them.\n"
        "- Liara limits how many times an app may be released a day, and some things have no call in its API. When the person asks for something that hits a limit (a release past the daily cap, deleting a private network, 'cancel the build', changing the feature tier of an existing app), say plainly that it is a limit of Liara's API, not of you: call get_limits for the numbers and the list, and offer what is possible instead.\n"
        "- A project that is already online is updated with deploy_edits (this server's copy, including edits made in the chat) or redeploy (the repository's newest code); start_run deploy is only for a project that is not online yet. When the person says 'deploy again' or 'put it online' after a change, that is deploy_edits.\n"
        "- start_run, deploy_edits and redeploy take the person to the run page by themselves once they approve the card. After them, never tell the person to open the page and never write its address (a path starting with /runs/): say nothing more than one short sentence, or nothing.\n"
        "- If something the person typed was replaced by [hidden], tell them it looked like a secret and was kept out of the conversation, and offer the card instead.\n"
        "- Prices are in toman, with VAT; say whether it is per hour or per month. Say when a change restarts the app.\n"
        "- You can read and change the project's code (list_files, read_file, search_code, edit_files). Read the relevant files before changing them, keep each change small and focused, "
        "never put keys, tokens or passwords in code (read them from environment variables and tell the person which variable to set), and say in plain words what you changed and why. "
        "To build something new from a description: new_project, then edit_files with the complete set of files it needs (a Dockerfile or a standard framework layout that Liara can build, listening on one port), then start_run with mode deploy (the run page shows the plan and price and asks for approval before anything is created).\n"
        "- Edits are saved in this server's copy only. For a project from Git they do not change the person's repository: say so. deploy_edits releases the edited copy on a running app; redeploy instead downloads the repository's code again and would drop the edits.\n"
        "- After a change, say plainly whether it was done or failed (the tool result tells you), then offer the one natural next step. A new project has nothing on Liara until a run is approved: call start_run with mode deploy right away, because the run page it opens shows the plan and the price and asks for approval before anything is created.\n"
        "- If a tool says something is not possible, explain it in plain words and offer the nearest thing that is possible.\n"
        f"- Answer in {'Persian (فارسی)' if language == 'fa' else 'English'}, briefly, in plain text without markdown headings. Keep technical names (variables, commands, plan ids) exactly as they are."
        + (f"\n- {PERSIAN_STYLE}" if language == "fa" else "")
    )
