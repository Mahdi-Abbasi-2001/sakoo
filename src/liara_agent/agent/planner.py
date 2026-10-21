from __future__ import annotations

import secrets
from pathlib import Path
from typing import Callable

from liara_agent.agent.loop import AgentResult, run_agent
from liara_agent.config import Settings
from liara_agent.detect.fingerprints import SECRETISH
from liara_agent.detect.intent import find_compose, parse_compose
from liara_agent.liara.live import LiveCatalog
from liara_agent.llm.client import LLM, LLMError, OpenAICompatLLM
from liara_agent.plan.fallback import plan_without_model
from liara_agent.detect.fingerprints import fingerprint
from liara_agent.plan.oneclick import marker_app, plan_from_spec
from liara_agent.plan.compose import _slug, decompose
from liara_agent.plan.model import DeployPlan


GENERIC_NAMES = {"app", "web", "server", "api", "backend", "frontend", "site", "service", "main", "www", "node", "python"}


def make_plan(path: str | Path, project: str, cat: LiveCatalog, settings: Settings, *, llm: LLM | None = None,
              ask: Callable[[str], str] | None = None, emit: Callable[[str, dict], None] = lambda k, d: None) -> tuple[DeployPlan | None, str, AgentResult | None]:
    """Compose projects are decomposed deterministically; everything else is investigated by the agent."""
    if (name := marker_app(path)):  # a ready-made app: no source, the plan comes from its spec
        return plan_from_spec(name, project, ram_of=lambda pid: cat.project_plans[pid].ram.amount if pid in cat.project_plans else 0.5), f"from one-click app {name}", None
    compose = find_compose(path)
    if compose:
        return decompose(parse_compose(compose), project_name=project, db_versions=cat.database_versions,
                         db_defaults=cat.default_database_versions), f"from {compose.name}", None
    try:
        if llm is None:
            if not settings.llm_api_key:
                raise RuntimeError("LLM_API_KEY is not set; single-app projects need the agent (see .env.example)")
            llm = OpenAICompatLLM(settings)
        result = run_agent(llm, path, ask=ask, emit=emit, max_steps=settings.max_agent_steps, language=settings.language)
    except (RuntimeError, LLMError):   # no model to use: an ordinary one-app project can still be planned by fixed rules (and the plan says so)
        plan = plan_without_model(path, project, fingerprint(path))
        if plan is None:
            raise
        return plan, "from fixed rules", None
    if result.plan and len(result.plan.apps) == 1:
        # Liara app ids are unique across ALL accounts, and a model likes "app" or "web": those are taken. The project's own name makes a clash unlikely.
        only, stem = result.plan.apps[0], _slug(project)
        if stem and only.name in GENERIC_NAMES and stem not in only.name:
            only.name = _slug(f"{stem}-{only.name}")[:40].strip("-")
    return result.plan, "investigated by the agent", result


def apply_env(plan: DeployPlan, prompt_secret: Callable[[str], str] | None) -> list[str]:
    """Fill each app's env from plan.env: generated values are random, user values are prompted (hidden), database values stay
    as $DB_ templates. Returns names that still have no value. Values are never logged or sent to the LLM."""
    web = next((a for a in plan.apps if a.role == "web"), plan.apps[0] if plan.apps else None)
    missing: list[str] = []
    # A model may write a plausible value for a secret into the app's env ("change-me", a made-up key). That value would be deployed and is
    # guessable, so secrets never keep a model-written value: it comes from generation or from the user, nowhere else.
    for var in plan.env:
        if web is not None and var.source != "database" and (var.secret or SECRETISH.search(var.name)):
            current = web.env.get(var.name)
            if current is not None and not any(p in current for p in ("$DB_", "$S3_", "$MAIL_")):  # placeholders for created resources are not model-written values
                del web.env[var.name]
    for var in plan.env:
        if web is not None and var.name.upper() == "PORT" and var.name not in web.env and web.port:
            web.env[var.name] = str(web.port)  # the port is the plan's own decision: never ask the user for it
            continue
        if web is None or var.name in web.env or var.source == "database":
            continue
        if var.source == "generated":
            if SECRETISH.search(var.name):  # only secrets are safe to invent: SECRET_KEY, JWT_SECRET, SESSION_TOKEN ...
                web.env[var.name] = secrets.token_urlsafe(32)
            elif var.name.upper() == "PORT" and web.port:
                web.env[var.name] = str(web.port)
            else:
                missing.append(var.name)  # a random string would break it (a port, a URL, a host...): ask the user instead
        elif prompt_secret:
            value = prompt_secret(var.name)
            if value:
                web.env[var.name] = value
                continue
            missing.append(var.name)
        else:
            missing.append(var.name)
    return missing
