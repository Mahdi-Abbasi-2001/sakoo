from __future__ import annotations

from liara_agent.detect.fingerprints import SECRETISH, fingerprint
from liara_agent.detect.intent import dockerfile_info, find_compose, procfile
from liara_agent.liara import catalog
from pathlib import Path


LANGUAGES = {"en": "English", "fa": "Persian (Farsi)"}

# How Persian-speaking developers actually write about deployment: the words they use, not literal translations. Added wherever the model writes Persian for a person.
PERSIAN_STYLE = (
    "When you write Persian, write the way Iranian developers talk, in a friendly, plain, professional tone: use the common loanwords, not stiff literal translations. "
    "deploy = دیپلوی (never استقرار), database = دیتابیس, repository = ریپازیتوری, branch = برنچ, commit = کامیت, pull request = پول‌ریکوئست, release = ریلیز, rollback = رول‌بک, "
    "build = بیلد, log = لاگ, server = سرور, plan (hardware/price) = پلن, environment variable = متغیر محیطی, restart = ری‌استارت, token = توکن, domain = دامنه, address = آدرس, "
    "password = رمز عبور, agent = ایجنت, app = برنامه, live/online = آنلاین. Keep technical names, commands, file names and code in English exactly as they are."
)


def system_prompt(language: str = "en") -> str:
    plats = ", ".join(f"{p} (default port {catalog.default_port(p)})" for p in sorted(catalog.platforms()))
    dbs = ", ".join(sorted(catalog.databases()))
    return f"""You are a deployment engineer for Liara, an Iranian cloud platform. A developer finished a project and does not know how to deploy it.
Investigate the project with your tools, then call submit_plan exactly once with a complete DeployPlan.

Liara facts:
- Platforms: {plats}. Pick the one that fits; use `docker` only when a Dockerfile exists or nothing else fits.
- Managed databases (use these ids): {dbs}. Create one only when the code really needs it.
- Apps must listen on 0.0.0.0 and on the port you put in the plan. Prefer reading the port from the code or env.
- Apps and databases share a private network. A database id becomes its hostname. Database credentials reach the app only through
  variables of the form $DB_<databaseId>_HOST, _PORT, _USER, _PASSWORD, _NAME, so put those in AppSpec.env, never real passwords.
- Files written at runtime are lost unless an app has a disk; the root filesystem is small.
- When you are unsure about a Liara fact (limits, liara.json fields, how a database is reached), call search_docs instead of guessing.
- Servers in Iran may be unable to reach some foreign services (Telegram, OpenAI, others). If the code calls them, add a note (level warning).

How to work:
1. Start from the digest in the first message. Read the real entrypoint (start script, Procfile, CMD, main) and trace how config is loaded.
2. Find every environment variable the code reads. Classify each: `user` (the developer must supply it), `generated` (ONLY a secret we can invent randomly, such as a session or signing key; never a port, host, URL or any value with a meaning), or `database` (comes from a database). Put values you can decide yourself (like the port) in the app's env instead. List only variables the app cannot run without, or that must differ in production; leave out optional tuning variables that have a safe default in the code (worker counts, log levels, feature toggles).
3. Monorepos: one AppSpec per deployable part (path = its folder). Say how parts reach each other.
4. Every app and database MUST carry evidence (file and line). If you cannot find evidence, ask_user or leave it out; never guess.
5. NEVER write a secret value (a key, token, password, signing secret) into an app's `env`, not even a placeholder: list the variable in the plan's `env` list with `secret: true` and the right `source`, and leave it out of the app's own `env`. A non-secret default that the code itself defines (like a greeting text) may go in the app's `env`.
6. Be frugal: read only files you need. You cannot see secret values and must not ask for them; mention variable names only.
7. The digest hints (databases, services) are suggestions. If a hint points to a database or service that you decide NOT to include, add a note (level info) saying why, citing evidence, so the developer is not surprised.
8. Put anything uncertain in `questions`, risks in `notes` (level info/warning/blocker). Do not invent services.
9. Write every note, question and message meant for the developer in {LANGUAGES.get(language, "English")}. Keep file names, paths, variable names, ports, versions and code exactly as they are.{(chr(10) + "10. " + PERSIAN_STYLE) if language == "fa" else ""}
"""


def digest(root: str | Path) -> str:
    root = Path(root)
    fp = fingerprint(root)
    out = ["Project digest (hints, verify them before relying on them):"]
    out.append("manifests: " + ("; ".join(f"{d}: {', '.join(sorted(m))}" for d, m in sorted(fp.manifests.items())) or "none found"))
    for h in fp.hints:
        out.append(f"hint {h.kind}={h.value} in {h.path} ({h.evidence.file} {h.evidence.note})".strip())
    if fp.env_vars:
        out.append("env vars read in code: " + ", ".join(f"{k}{' [secret?]' if SECRETISH.search(k) else ''} ({v.file}:{v.line})" for k, v in sorted(fp.env_vars.items())[:40]))
    if find_compose(root):
        out.append(f"{find_compose(root).name} exists (normally handled before the agent runs)")
    for df in sorted(root.glob("**/Dockerfile"))[:3]:
        if not any(p in df.parts for p in ("node_modules", ".git", ".agent-state")):
            out.append(f"{df.relative_to(root)}: {dockerfile_info(df)}")
    if (root / "Procfile").is_file():
        out.append(f"Procfile: {procfile(root / 'Procfile')}")
    return "\n".join(out)
