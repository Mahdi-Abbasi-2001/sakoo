"""Which private network a project's apps and databases join.

Liara refuses to create an app or a database without a private network (verified: HTTP 400 `network_is_required`, and `g2_plan_unavailable` on the newer plans),
so every project has one; apps and databases of a project reach each other through it. The agent decides:
  - none exists yet: it makes one for this project (after the person approves the plan: nothing is created before),
  - some exist: it asks, offering a new network for this project (recommended: nothing else of the person's shares it) and each existing one,
  - the person already named one (the command line): that one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from liara_agent.plan.model import DeployPlan

CREATE_NEW = "create_new"


@dataclass
class NetworkChoice:
    name: str | None = None
    id: str | None = None   # None for a network that is still to be created
    new: bool = False       # to be created, after approval

    def public(self) -> dict | None:
        return {"name": self.name, "new": self.new} if self.name else None


def needs_network(plan: DeployPlan) -> bool:
    return bool(plan.databases or plan.apps)   # Liara needs one for every app and database


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")[:30].strip("-") or "project"


def new_name(project: str, taken: set[str]) -> str:
    base = f"{_slug(project)}-net"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def decide_network(plan: DeployPlan, project: str, networks: list[dict], preferred: str,
                   ask: Callable[..., str]) -> NetworkChoice:
    """`networks` are the account's existing ones ({name, _id}); `preferred` a name given up front ('' for none); `ask(text, options, code=, params=)` asks the person."""
    if not needs_network(plan):
        return NetworkChoice()
    by_name = {n["name"]: n["_id"] for n in networks}
    if preferred and preferred in by_name:
        return NetworkChoice(preferred, by_name[preferred])
    fresh = new_name(project, set(by_name))
    if not by_name:
        return NetworkChoice(fresh, None, True)
    answer = ask("Which private network should this project use? Its apps and databases reach each other through it.", [CREATE_NEW, *by_name],
                 code="network_choice", params={"new_name": fresh})
    if answer in by_name:
        return NetworkChoice(answer, by_name[answer])
    return NetworkChoice(fresh, None, True)
