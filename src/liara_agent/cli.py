from __future__ import annotations

from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from liara_agent.config import get_settings
from liara_agent.detect.fingerprints import SECRETISH, fingerprint
from liara_agent.detect.intent import find_compose, parse_compose
from liara_agent.liara.live import TIER_LABELS, LiaraAPI, snapshot_catalog
from liara_agent.liara.pricing import HOURS_PER_MONTH, estimate_plan, project_cost, total
from liara_agent.plan.compose import decompose

app = typer.Typer(help="Deploy any project to Liara with an AI agent.", no_args_is_help=True)
domain_app = typer.Typer(help="Connect your own domain to an app this agent deployed (dry run unless --execute).", no_args_is_help=True)
app.add_typer(domain_app, name="domain")
console = Console()


@app.command()
def analyze(path: Path = typer.Argument(Path("."), exists=True, file_okay=False)):
    """Static analysis only (no LLM, no network): show what the agent would start from."""
    fp = fingerprint(path)
    t = Table(title=f"Hints for {fp.root}")
    for c in ("kind", "value", "dir", "evidence"):
        t.add_column(c)
    for h in fp.hints:
        t.add_row(h.kind, h.value, h.path, f"{h.evidence.file} {h.evidence.note}".strip())
    console.print(t)
    if fp.env_vars:
        e = Table(title="Environment variables read by the code")
        for c in ("name", "secret?", "where"):
            e.add_column(c)
        for name, ev in sorted(fp.env_vars.items()):
            e.add_row(name, "yes" if SECRETISH.search(name) else "", f"{ev.file}:{ev.line}")
        console.print(e)


def _api() -> LiaraAPI:
    cfg = get_settings()
    return LiaraAPI(cfg.liara_api_token, team_id=cfg.liara_team_id)


@app.command()
def docs(query: str = typer.Argument(..., help="what to look up in Liara's documentation"), limit: int = typer.Option(3, help="how many passages")):
    """Search Liara's documentation (an offline snapshot)."""
    from liara_agent.liara import docs as liara_docs
    console.print(liara_docs.as_text(liara_docs.search(query, limit)))


@app.command()
def whoami():
    """Read-only: show the teams, networks and apps this token can see."""
    api = _api()
    for t in api.list_teams():
        console.print(f"team: {t['name']}  id={t['_id']}  (set LIARA_TEAM_ID to work inside it)")
    console.print(f"scope: {'team ' + api.team_id if api.team_id else 'personal account'}")
    console.print("networks:", ", ".join(n["name"] for n in api.list_networks()) or "-")
    console.print("apps:", ", ".join(api.list_app_ids()) or "-")


@app.command()
def plans():
    """Read-only: available app plans with real prices (toman, incl. 10% VAT) and bundle limits."""
    cat = _api().live_catalog()
    t = Table(title="Liara app plans (g2), price incl. VAT")
    for c in ("plan", "RAM", "vCPU", "disk", "tier", "per hour", "per month", "build timeout", "deploys/day"):
        t.add_column(c, justify="right")
    for pid, plan in cat.project_plans.items():
        if not plan.available or not pid.endswith("-g2"):
            continue
        for tier, b in cat.project_bundles[pid].items():
            if not b.available:
                continue
            c = project_cost(cat, pid, tier)
            t.add_row(pid, f"{plan.ram.amount:g} GB", f"{plan.cpu.amount:g}", f"{plan.volume:g} GB", TIER_LABELS.get(tier, tier),
                      f"{c.hourly_with_vat:,.0f}", f"{c.monthly_with_vat:,.0f}", f"{b.build_timeout:g} min", str(b.max_deployments_per_day))
    console.print(t)
    console.print(f"monthly = hourly x {HOURS_PER_MONTH}h; databases: {', '.join(cat.database_versions)}")


@app.command()
def plan(path: Path = typer.Argument(Path("."), exists=True, file_okay=False),
         name: str = typer.Option("", help="Project name used for ids (default: folder name)"),
         offline: bool = typer.Option(False, "--offline", help="Use the bundled price snapshot instead of the Liara API")):
    """Dry run: show the apps and databases that would be created, with costs. Creates nothing."""
    project = _slug_name(name or path.resolve().name)
    cfg = get_settings()
    try:
        if offline or not cfg.liara_api_token:
            raise ValueError
        cat = _api().live_catalog()
        source = "live Liara prices"
    except Exception:
        cat, source = snapshot_catalog(), "bundled price snapshot (may be stale)"
    p, how, res = _plan_or_exit(path, project, cat, cfg)
    t = Table(title=f"Plan ({how})")
    for c in ("kind", "id", "details", "liara plan", "per month (incl. VAT)"):
        t.add_column(c)
    lines = estimate_plan(cat, p)
    by_name = {l.name: l for l in lines}
    for a in p.apps:
        l = by_name[a.name]
        t.add_row("app", a.name, f"{a.role}, port {a.port or '-'}, {'image ' + a.image if a.image else 'build ' + a.path}",
                  f"{l.plan} / {TIER_LABELS.get(l.tier, l.tier)}", f"{l.cost.monthly_with_vat:,.0f}")
    for d in p.databases:
        l = by_name[d.id]
        t.add_row("database", d.id, f"{d.kind} {d.version}", f"{l.plan} / {TIER_LABELS.get(l.tier, l.tier)}", f"{l.cost.monthly_with_vat:,.0f}")
    console.print(t)
    tot = total(lines)
    console.print(f"[bold]Total: {tot.hourly_with_vat:,.0f} toman/hour, about {tot.monthly_with_vat:,.0f} toman/month incl. VAT[/] ({source})")
    for n in p.notes:
        color = {"blocker": "red", "warning": "yellow"}.get(n.level, "dim")
        console.print(f"[{color}]{n.level}:[/] {n.message}")
    for q in p.questions:
        console.print(f"[cyan]question:[/] {q}")
    _print_env_and_usage(p, res)


@app.command()
def serve(host: str = typer.Option("127.0.0.1", help="Use 0.0.0.0 only behind HTTPS"), port: int = typer.Option(8000)):
    """Start the web API (accounts, projects, live runs). Data lives in WEB_DATA_DIR (default .web-data)."""
    import uvicorn

    from liara_agent.web.app import create_app
    uvicorn.run(create_app(), host=host, port=port, log_level="info")


@app.command()
def cleanup(path: Path = typer.Argument(Path("."), exists=True, file_okay=False)):
    """Delete the apps, databases, buckets, email servers and domains this agent created for the project (never anything else)."""
    from liara_agent.deploy.cleanup import nothing_created, remove_all
    from liara_agent.liara.client import Journal, LiaraClient
    cfg = get_settings()
    journal = Journal.load(path / ".agent-state" / "created.json")
    if nothing_created(journal):
        console.print("Nothing to clean up: this agent created no resources for this project.")
        return
    console.print(f"Will delete apps {journal.apps}, databases {journal.databases}, buckets {journal.buckets}, email servers {[m['domain'] for m in journal.mail_servers]} "
                  f"and domains {[d['name'] for d in journal.domains]} (their data is lost).")
    if typer.prompt("Type 'delete' to confirm") != "delete":
        raise typer.Exit(1)
    client = LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=False, journal=journal)
    deleted: dict[str, list[str]] = {}
    try:
        remove_all(client, deleted)
    finally:
        for kind, names in deleted.items():
            for n in names:
                console.print(f"[red]could not delete: {n}[/]" if kind == "failed" else f"deleted {kind.rstrip('s')} {n}")


def _plan_or_exit(path, project, cat, cfg):
    from liara_agent.agent.planner import make_plan

    def show(kind: str, data: dict) -> None:
        if kind in ("tool_call", "agent_message", "plan_rejected"):
            console.print(f"[dim]{kind}[/] {data.get('name', '')} {data.get('arguments', data.get('text', data.get('reason', '')))}"[:200])
    try:
        p, how, res = make_plan(path, project, cat, cfg, ask=lambda q: typer.prompt(f"[agent asks] {q}"), emit=show)
    except RuntimeError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    if p is None:
        console.print(f"[red]The agent could not produce a valid plan ({res.stopped_because} after {res.steps} steps).[/]")
        raise typer.Exit(1)
    return p, how, res


def _print_env_and_usage(p, res) -> None:
    if p.env:
        console.print("Environment variables to provide: " + ", ".join(f"{v.name}{' (secret)' if v.secret else ''} [{v.source}]" for v in p.env))
    if res:
        console.print(f"[dim]agent: {res.steps} steps, {res.usage.calls} LLM calls, {res.usage.prompt_tokens} prompt + {res.usage.completion_tokens} completion tokens[/]")


def _slug_name(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-") or "app"


def cli_channel(project: str, run):
    """Terminal prompts, recorded in the event log like any other channel."""
    from liara_agent.interact import CallbackChannel

    def confirm(prompt: str, kind: str) -> bool:
        if kind == "plan":  # real runs create billable resources: make the user type the project name
            console.print(f"[red]{prompt}[/]")
            return typer.prompt(f"Type the project name '{project}' to confirm", default="", show_default=False) == project
        return typer.confirm(prompt, default=False)

    return CallbackChannel(
        run, confirm,
        lambda text, options: typer.prompt(f"[agent asks] {text}"),
        lambda name: typer.prompt(f"Value for {name} (hidden, empty to skip)", hide_input=True, default="", show_default=False))


def _print_event(ev) -> None:
    k, d = ev.kind, ev.data
    if k == "phase_started":
        console.print(f"[bold cyan]> {d['phase']}[/]")
    elif k in ("tool_call", "agent_message", "plan_rejected"):
        console.print(f"[dim]{k} {d.get('name', '')} {d.get('arguments', d.get('text', d.get('reason', '')))}"[:200] + "[/]")
    elif k == "plan_ready":
        c = d["cost"]
        console.print(f"Plan ({d['how']}): {len(d['plan']['apps'])} app(s), {len(d['plan']['databases'])} database(s); "
                      f"[bold]{c['total_hourly_incl_vat']:,} toman/hour (about {c['total_monthly_incl_vat']:,}/month incl. VAT)[/]")
        for line in c["lines"]:
            console.print(f"  {line['kind']:8} {line['name']:32} {line['plan']}/{TIER_LABELS.get(line['tier'], line['tier'])}  {line['monthly_incl_vat']:,}/month")
        for n in d["plan"]["notes"]:
            console.print(f"[{ {'blocker': 'red', 'warning': 'yellow'}.get(n['level'], 'dim') }]{n['level']}:[/] {n['message']}")
        for f in d.get("readiness", {}).get("findings", []):
            console.print(f"[{'yellow' if f['level'] == 'warning' else 'dim'}]readiness {f['code']}:[/] {f['file']}{':' + str(f['line']) if f['line'] else ''} {f['params'] or ''}".rstrip())
        if d["plan"]["env"]:
            console.print("Environment variables: " + ", ".join(f"{v['name']}{' (secret)' if v['secret'] else ''} [{v['source']}]" for v in d["plan"]["env"]))
        if d.get("usage"):
            u = d["usage"]
            console.print(f"[dim]agent: {u['calls']} LLM calls, {u['prompt_tokens']} prompt + {u['completion_tokens']} completion tokens[/]")
    elif k == "resource_created":
        console.print(f"  {'would create' if d.get('dry_run') else 'created'} {d['kind']} {d['id']} ({d['plan']}{'/' + d['tier'] if d.get('tier') else ''}, {d['hourly_price']:,} toman/hour)")
    elif k == "diagnosis":
        console.print(f"[bold red]Diagnosis ({d['code']}):[/] {d['summary']}")
        for e in d["evidence"]:
            console.print(f"  [dim]{e}[/]")
    elif k in ("fix_applied", "fix_declined"):
        console.print(f"[{'green' if k == 'fix_applied' else 'yellow'}]{k.replace('_', ' ')}:[/] {d['fix']} {d['note']}")
    elif k == "patch_ready":
        console.print(f"[dim]patch for {d['file']}:\n{d['diff']}[/]")
    elif k == "test_result":
        console.print(f"[{'green' if d['ok'] else 'red'}]{d['name']}: {d['detail']}[/] {d['url']}")
    elif k in ("build", "build_error"):
        if d.get("line", "").strip():
            console.print(f"[dim]{d['line'].rstrip()}[/]")
    elif k == "warning":
        console.print(f"[yellow]warning:[/] {d['message']}")
    elif k == "run_finished":
        for name, url in d.get("urls", {}).items():
            console.print(f"[green]{name} is live:[/] {url}  [dim](the liara.run address stops working 7 days after the first deploy; add a custom domain)[/]")
        if d.get("dry_run"):
            console.print("\n[bold]Dry run. Nothing was sent. Requests that would be made:[/]")
            for w in d.get("would_send", []):
                console.print(f"  {w['method']:6} {w['path']}  {w['summary']}")
        if not d["ok"]:
            console.print(f"[red]Stopped ({d.get('stopped') or d.get('failed_step')}): {d.get('reason')}[/]")
            if d.get("diagnosis"):
                for f in d["diagnosis"]["fixes"]:
                    console.print(f"  - {f['text']}")
        c = d["created"]
        if not d.get("dry_run") and (c["apps"] or c["databases"]) and not d["ok"]:
            console.print(f"[yellow]Created resources keep billing: apps {c['apps']}, databases {c['databases']}. Remove them with `liara-agent cleanup`.[/]")


@app.command()
def deploy(path: Path = typer.Argument(Path("."), exists=True, file_okay=False),
           name: str = typer.Option("", help="Project name used for ids (default: folder name)"),
           execute_: bool = typer.Option(False, "--execute", help="Really create resources and deploy (costs money). Default: dry run."),
           network: str = typer.Option("", help="Private network name (default: the agent picks one, or makes one for the project)")):
    """Plan, show the cost, and deploy to Liara. Dry run unless --execute is given and confirmed."""
    from liara_agent.deploy.pipeline import run_deployment
    from liara_agent.events import Run
    from liara_agent.liara.client import Journal, LiaraClient

    cfg = get_settings()
    if not cfg.liara_api_token:
        console.print("[red]LIARA_API_TOKEN is not set (see .env.example).[/]")
        raise typer.Exit(1)
    api = _api()
    if api.list_teams() and not api.team_id:
        console.print("[red]Your token belongs to team(s) but LIARA_TEAM_ID is not set, so apps and networks in the team are invisible "
                      "(name clashes could not be detected). Run `liara-agent whoami`, then add LIARA_TEAM_ID to .env.[/]")
        raise typer.Exit(1)
    cat = api.live_catalog()
    project = _slug_name(name or path.resolve().name)
    nets = api.list_networks()
    client = LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=not execute_,
                         journal=Journal.load(path / ".agent-state" / "created.json"))
    run = Run(log_path=path / ".agent-state" / "runs" / f"{__import__('time').strftime('%Y%m%d-%H%M%S')}.jsonl")
    run.subscribe(_print_event)
    try:
        res = run_deployment(path, project, cat, cfg, client, cli_channel(project, run), run, network_id=None, networks=nets, network_name=network or "", execute_=execute_)
    except httpx.HTTPStatusError as e:  # an answer Liara gave that the agent did not expect: say what, and where what was already created can be removed
        console.print(f"[red]Liara answered HTTP {e.response.status_code} to {e.request.method} {e.request.url.path}.[/]")
        j = client.journal
        if j.apps or j.databases or j.disks:
            console.print(f"[yellow]Already created: apps {j.apps}, databases {len(j.databases)}, disks {j.disks}. Remove them with: liara-agent cleanup {path}[/]")
        raise typer.Exit(1)
    console.print(f"[dim]event log: {run.log_path}[/]")
    if not res.ok:
        raise typer.Exit(1)



def _owned_app(path: Path, app: str):
    """The Liara client and app name for a project this agent deployed from `path` (only apps in its journal are ever touched)."""
    from liara_agent.liara.client import Journal, LiaraClient
    cfg = get_settings()
    journal = Journal.load(path / ".agent-state" / "created.json")
    name = app or (journal.apps[0] if len(journal.apps) == 1 else "")
    if name not in journal.apps:
        console.print(f"[red]Pick the app with --app: {', '.join(journal.apps) or 'this project has none from this agent'}[/]")
        raise typer.Exit(1)
    return LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=False, journal=journal), name


@app.command()
def releases(path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option("", "--app"),
             raw: bool = typer.Option(False, "--raw", help="Show the field names Liara returns for the newest release")):
    """List the app's releases and which of them can be rolled back to."""
    from liara_agent.deploy import history
    client, name = _owned_app(path, app_name)
    if raw:
        first = (client.releases(name, 1, 1).get("releases") or [{}])[0]
        console.print({k: (v if isinstance(v, (str, int, float, bool)) and k not in ("imageName",) else type(v).__name__) for k, v in first.items()})
        return
    out = history.list_releases(client, name, path / ".agent-state")
    t = Table(title=f"Releases of {name} (running: {out['current'][:8] or '-'})")
    for c in ("id", "state", "running", "roll back via", "message"):
        t.add_column(c)
    for r in out["releases"]:
        t.add_row(r["id"], str(r["state"]), "yes" if r["current"] else "", r["rollback"] or "-", str(r["message"] or ""))
    console.print(t)


@app.command()
def rollback(release: str, path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option("", "--app"),
             yes: bool = typer.Option(False, "--yes", help="Do not ask")):
    """Make an earlier release the running one again (a new release from its kept source, or from Liara's image)."""
    from liara_agent.deploy import history
    client, name = _owned_app(path, app_name)
    if not yes and not typer.confirm(f"Roll {name} back to release {release}?", default=False):
        raise typer.Exit(1)
    try:
        out = history.rollback(client, name, release, path / ".agent-state", emit=lambda kind, text: console.print(f"[dim]{text}[/]"))
    except history.HistoryError as e:
        console.print(f"[red]{e.code}: {e}[/]")
        raise typer.Exit(1)
    console.print(f"release {out['release_id']} {out['state']} (via {out['via']})")
    if not out["ok"]:
        raise typer.Exit(1)


@app.command()
def redeploy(path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option("", "--app"),
             message: str = typer.Option("redeploy", help="Release message")):
    """Upload the project's current files to the app this agent created and release them (same settings as the first deploy)."""
    from liara_agent.deploy import history
    client, name = _owned_app(path, app_name)
    try:
        out = history.redeploy(client, name, path, path / ".agent-state", message=message, emit=lambda kind, text: console.print(f"[dim]{text}[/]"))
    except history.HistoryError as e:
        console.print(f"[red]{e.code}: {e}[/]")
        raise typer.Exit(1)
    console.print(f"release {out['release_id']} {out['state']}")
    if not out["ok"]:
        raise typer.Exit(1)


@app.command()
def push(path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(..., "--app", help="An app YOU created (in the console)"),
         port: int = typer.Option(8000), disk: list[str] = typer.Option([], "--disk", help="An existing disk to mount, as name:/path (repeatable)"),
         message: str = typer.Option("deployed by liara-agent", help="Release message")):
    """Upload this folder to an app you created yourself and release it. The app is NOT recorded as created by the agent, so `cleanup` will never delete it."""
    from liara_agent.deploy import history
    from liara_agent.liara.client import Journal, LiaraAPIError, LiaraClient
    cfg = get_settings()
    probe = LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=False, journal=Journal())
    if not probe.app_exists(app_name):
        console.print(f"[red]There is no app named {app_name!r} on this account.[/]")
        raise typer.Exit(1)
    have = {d.get("name") for d in probe.disks(app_name)}
    mounts = [{"name": n, "mountTo": m} for n, _, m in (x.partition(":") for x in disk)]
    missing = [d["name"] for d in mounts if d["name"] not in have]
    if missing:
        console.print(f"[red]Disk(s) {missing} do not exist on {app_name} (existing: {sorted(have)}). Create them in the console first.[/]")
        raise typer.Exit(1)
    console.print(f"Will upload {path.resolve()} to the EXISTING app [bold]{app_name}[/] (port {port}, disks {mounts or 'none'}) and release it. Its environment variables are not changed.")
    if typer.prompt(f"Type the app name '{app_name}' to confirm") != app_name:
        raise typer.Exit(1)
    client = LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=False, journal=Journal(apps=[app_name]))  # in memory only: never saved, so cleanup cannot touch this app
    try:
        out = history.redeploy(client, app_name, path, path / ".agent-state", message=message, emit=lambda kind, text: console.print(f"[dim]{text}[/]"),
                               meta={"app": app_name, "platform": "docker", "port": port, "platform_config": {}, "disks": mounts})
    except LiaraAPIError as e:
        console.print(f"[red]Liara answered HTTP {e.status}: {e.body[:300]}[/]")
        raise typer.Exit(1)
    console.print(f"release {out['release_id']} {out['state']}")
    if not out["ok"]:
        raise typer.Exit(1)


@app.command()
def logs(app_name: str = typer.Option(..., "--app", help="Any app on your account (read-only)"), lines: int = typer.Option(60)):
    """The app's recent runtime log lines (read-only; secrets are masked)."""
    from liara_agent.events import scrub
    from liara_agent.liara.client import Journal, LiaraClient
    cfg = get_settings()
    client = LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=False, journal=Journal())
    for line in client.runtime_logs(app_name, None)[-lines:]:
        console.print(scrub(line), markup=False)


@app.command()
def metrics(path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option("", "--app"), hours: int = typer.Option(1)):
    """CPU and memory of the running app, as Liara reports them (read-only)."""
    from liara_agent.liara import metrics as m
    client, name = _owned_app(path, app_name)
    out = m.fetch(client, name, hours)
    for kind in m.KINDS:
        pts = out[kind]
        console.print(f"{kind}: {len(pts)} points" + (f", last {pts[-1]['value']} (first {pts[0]['value']})" if pts else ""))


@app.command()
def explain(log: Path = typer.Argument(..., exists=True, dir_okay=False, help="A run's event log (.agent-state/runs/*.jsonl)"),
            language: str = typer.Option("en", help="en or fa")):
    """Explain in plain language what went wrong in a run (the log tail, secrets masked, goes to the language model)."""
    import json as _json
    from liara_agent.events import Event
    from liara_agent.explain import ExplainError, explain as explain_run
    from liara_agent.llm.client import OpenAICompatLLM
    cfg = get_settings()
    events = [Event(**{k: d[k] for k in ("id", "ts", "kind", "data")}) for d in (_json.loads(l) for l in log.read_text().splitlines() if l.strip())]
    try:
        console.print(explain_run(OpenAICompatLLM(cfg) if cfg.llm_api_key else None, events, language))
    except ExplainError as e:
        console.print(f"[red]{e.code}: {e}[/]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()


# -- custom domains -----------------------------------------------------------------------------------------------------------
def _domain_ctx(path: Path, app_name: str | None, dry_run: bool):
    """(client, app) for a project directory: the app must be one this agent created (the journal in .agent-state says which)."""
    from liara_agent.liara.client import Journal, LiaraClient
    cfg = get_settings()
    journal = Journal.load(path / ".agent-state" / "created.json")
    app_name = app_name or (journal.apps[0] if journal.apps else None)
    if not app_name or app_name not in journal.apps:
        console.print("[red]This project has no app created by this agent. Deploy it first (or pass --app with one of its apps).[/]")
        raise typer.Exit(1)
    return LiaraClient(cfg.liara_api_token, team_id=cfg.liara_team_id, dry_run=dry_run, journal=journal), app_name


def _show_records(records) -> None:
    t = Table(title="DNS records")
    for c in ("type", "name", "value", "Liara's check"):
        t.add_column(c)
    for r in records:
        t.add_row(r["type"], r["name"], r["value"] or "(see the Liara console)", (r.get("status") or "") + (" (derived)" if r.get("derived") else ""))
    console.print(t)


@domain_app.command("add")
def domain_add(domain: str, path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(None, "--app"),
               liara_dns: bool = typer.Option(False, "--liara-dns", help="host the domain's DNS on Liara (Liara then adds the records itself)"),
               zone: str = typer.Option(None, help="the DNS zone, when it is not the last two parts of the domain"),
               www: bool = typer.Option(False, "--www", help="also attach www.<domain> and redirect it to the domain"),
               execute: bool = typer.Option(False, "--execute", help="really attach it (default: only show the plan)")):
    """Show what connecting DOMAIN would do; with --execute, do it."""
    from liara_agent import domains as dom
    client, app_name = _domain_ctx(path, app_name, dry_run=not execute)
    try:
        plan = dom.plan_domain(client, app_name, domain, use_liara_dns=liara_dns, zone=zone, www=www)
    except dom.DomainError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
    console.print(f"[bold]{plan['domain']}[/] -> {plan['app']} ({'Liara DNS' if liara_dns else 'your own DNS'})")
    for i, st in enumerate(plan["steps"], 1):
        console.print(f"  {i}. {st['code']} {st['params']}")
    if plan["zone"] and not plan["zone"]["active"] and plan["zone"]["name_servers"]:
        console.print(f"  Liara nameservers to set at your registrar: {', '.join(plan['zone']['name_servers'])}")
    console.print("[yellow]A new domain can take a while (usually minutes, sometimes an hour or more) to start working. Your free liara.run address keeps working meanwhile.[/]")
    if not execute:
        console.print("Nothing was created. Add --execute to attach it.")
        return
    if not typer.confirm("Attach it now?"):
        raise typer.Exit(1)
    view = dom.connect_domain(client, app_name, domain, use_liara_dns=liara_dns, zone=zone, www=www)
    console.print(f"attached {view['name']} (status {view['status']}, certificate {view['certificates_status']})")
    _show_records(view["records"])


@domain_app.command("status")
def domain_status(path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(None, "--app")):
    """The domains of the app, with Liara's check of each record."""
    from liara_agent import domains as dom
    client, app_name = _domain_ctx(path, app_name, dry_run=True)
    target = dom.route_target(app_name)
    for d in client.app_domains(app_name):
        v = dom.domain_view(dom._with_check(client, d), dom.zone_or_none(client, dom.zone_for(d["name"])), target)
        console.print(f"[bold]{v['name']}[/]  status {v['status']}  certificate {v['certificates_status']}  ready={v['ready']}")
        _show_records(v["records"])
    console.print(f"https://console.liara.ir/apps/{app_name}/domains")


@domain_app.command("check")
def domain_check(domain: str, path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(None, "--app")):
    """Ask the zone's own nameservers and the big public resolvers about the domain's records."""
    from liara_agent import dnscheck
    from liara_agent import domains as dom
    client, app_name = _domain_ctx(path, app_name, dry_run=True)
    name = dom.normalize(domain)
    d = next((x for x in client.app_domains(app_name) if x.get("name") == name), None)
    if not d:
        console.print(f"[red]{name} is not attached to {app_name}.[/]")
        raise typer.Exit(1)
    view = dom.domain_view(dom._with_check(client, d), None, dom.route_target(app_name))
    out = dnscheck.check_domain(name, dom.zone_for(name), view["records"])
    console.print(f"nameservers: {', '.join(out['nameservers']) or 'none found'}")
    for r in out["records"]:
        console.print(f"  {r['type']:6} {r['fqdn']}: nameservers {sorted(set(r['authoritative'].values()))}, public {r['public']}, local {r['local']}")
    console.print(f"verdict: [bold]{out['verdict']}[/]")


@domain_app.command("ssl")
def domain_ssl(domain: str, path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(None, "--app"),
               execute: bool = typer.Option(False, "--execute")):
    """Request the free SSL certificate for a domain this agent attached."""
    from liara_agent import domains as dom
    client, app_name = _domain_ctx(path, app_name, dry_run=not execute)
    name = dom.normalize(domain)
    d = next((x for x in client.journal.domains if x["name"] == name), None)
    if not d:
        console.print(f"[red]This agent did not attach {name}, so it will not change it.[/]")
        raise typer.Exit(1)
    client.enable_ssl(d["id"])
    console.print("requested" if execute else "dry run: nothing was sent. Add --execute.")


@domain_app.command("remove")
def domain_remove(domain: str, path: Path = typer.Argument(Path("."), exists=True, file_okay=False), app_name: str = typer.Option(None, "--app"),
                  records: bool = typer.Option(False, "--records", help="also delete the DNS records Liara added to your Liara DNS zone for it"),
                  execute: bool = typer.Option(False, "--execute")):
    """Detach a domain this agent attached, and optionally clean up the records Liara added for it."""
    from liara_agent import domains as dom
    client, app_name = _domain_ctx(path, app_name, dry_run=not execute)
    name = dom.normalize(domain)
    d = next((x for x in client.journal.domains if x["name"] == name), None)
    if not d:
        console.print(f"[red]This agent did not attach {name}, so it will not change it.[/]")
        raise typer.Exit(1)
    plan = dom.removal_plan(_domain_ctx(path, app_name, dry_run=True)[0], app_name, d["id"])
    console.print(f"will detach {name}" + (f" and delete {len(plan['records'])} DNS records from {plan['zone']['name']}" if records and plan["zone"] else ""))
    for r in plan["records"]:
        console.print(f"  {r['type']} {r['name']} -> {r['value']}")
    if not execute:
        console.print("Nothing was changed. Add --execute.")
        return
    console.print(f"removed; {dom.remove_domain(client, app_name, d['id'], records)} DNS records deleted")
