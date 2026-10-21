"""An exportable Markdown report of one run, built only from its (already scrubbed) event log. It holds names, never values: environment variables are
listed without their contents, so the file is safe to attach to a ticket or keep in the repository."""
from __future__ import annotations

import time

from liara_agent.events import Event, scrub


def _t(n) -> str:
    return f"{int(n):,}"


def build_report(project: str, mode: str, status: str, created_at: float, events: list[Event]) -> str:
    last = lambda kind: next((e.data for e in reversed(events) if e.kind == kind), None)  # noqa: E731
    plan_ev, fin = last("plan_ready"), last("run_finished")
    L = [f"# Deployment report: {project}", "",
         f"- Date: {time.strftime('%Y-%m-%d %H:%M', time.localtime(created_at))}",
         f"- Kind of run: {'real deploy' if mode == 'deploy' else 'preview (nothing was created)'}",
         f"- Result: {status}", "- Made by: Liara Launchpad", ""]
    if fin and fin.get("urls"):
        L += ["## Where it runs", ""] + [f"- {a}: {u}" for a, u in fin["urls"].items()]
        L += ["", "The `liara.run` address works for 7 days after the first deploy. Connect your own domain before then.", ""]
    if plan_ev:
        plan = plan_ev["plan"]
        L += ["## What was deployed", "", f"Found by: {plan_ev['how']}.", ""]
        for a in plan["apps"]:
            L.append(f"- App `{a['name']}`: {a['platform']}, port {a['port'] or 'none'} ({a['role']})" + (f", start `{a['start_command']}`" if a["start_command"] else ""))
            for d in a["disks"]:
                L.append(f"  - disk `{d['name']}` mounted at `{d['mount_to']}`")
        for d in plan["databases"]:
            L.append(f"- Database `{d['id']}`: {d['kind']}{' ' + d['version'] if d['version'] else ''}")
        for s in plan.get("storage") or []:
            L.append(f"- Storage bucket `{s['id']}` ({s['permission']})")
        if plan.get("mail"):
            L.append(f"- Email server for `{plan['mail']['domain']}` ({plan['mail']['mode']} mode)")
        if plan["env"]:
            L += ["", "Environment variables (names only):", ""] + [f"- `{v['name']}`{' (secret)' if v['secret'] else ''}, from {v['source']}" for v in plan["env"]]
        if plan["links"]:
            L += ["", "Connections:", ""] + [f"- `{k['from']}` uses `{k['to']}` through {', '.join('`' + e + '`' for e in k['env'])}" for k in plan["links"]]
        c = plan_ev["cost"]
        L += ["", "## Cost (toman, VAT included)", "", "| Resource | Plan | Per month |", "|---|---|---|"]
        L += [f"| {x['kind']} `{x['name']}` | {x['plan']} / {x['tier_label']} | {_t(x['monthly_incl_vat'])} |" for x in c["lines"]]
        L += [f"| **Total** | | **{_t(c['total_monthly_incl_vat'])}** (about {_t(c['total_hourly_incl_vat'])} per hour) |", "",
              "Liara bills by the hour. Delete what you no longer need."]
        notes = [n for n in plan["notes"] if n["level"] in ("warning", "blocker", "info")]
        if notes:
            L += ["", "## Notes from the analysis", ""] + [f"- {n['level']}: {n['message']}" for n in notes]
        found = (plan_ev.get("readiness") or {}).get("findings") or []
        if found:
            L += ["", "## Production readiness", ""] + [f"- {f['level']}: {f['code']} in `{f['file']}`" + (f" line {f['line']}" if f["line"] else "") for f in found]
    tests = [e.data for e in events if e.kind == "test_result"]
    if tests:
        L += ["", "## Checks after deploy", ""] + [f"- {'passed' if t['ok'] else 'FAILED'}: {t['name']} {t.get('url') or ''} {t.get('detail') or ''}".rstrip() for t in tests]
    heals = [e.data for e in events if e.kind == "diagnosis"]
    if heals:
        L += ["", "## Problems found and handled", ""] + [f"- {d['code']}: {d['summary']}" for d in heals]
    if fin and not fin.get("ok") and fin.get("reason"):
        L += ["", "## Why it stopped", "", str(fin["reason"])]
    if fin and fin.get("follow_ups"):
        L += ["", "## Still to do by you", ""]
        for f in fin["follow_ups"]:
            L.append(f"- {f['code']}")
            L += [f"  - {r['type']} `{r['name']}` → `{r['value']}`" for r in f["records"]]
    return str(scrub("\n".join(L) + "\n"))
