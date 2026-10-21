"""Advice that changes nothing: things Liara offers that fit this project better than (or alongside) what the agent is about to do, and the signs that
the project does not belong on the app platform at all. Each becomes a plan note the person reads before approving."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from liara_agent.liara.oneclick import find_matches
from liara_agent.plan.model import DeployPlan, Note

CONSOLE = "https://console.liara.ir"
AI_BASE_URL = "https://ai.liara.ir/api/<workspace-id>/v1"  # from Liara's AI OpenAPI spec; the workspace id is the person's own
UDP_PORT = re.compile(r"/udp\b", re.I)
WINDOWS_IMAGE = re.compile(r"^\s*FROM\s+mcr\.microsoft\.com/windows", re.I | re.M)


def vm_signals(root: str | Path) -> list[str]:
    """Things in the project that an app platform (one container, HTTP in front, no host access) cannot give: each is a short identifier."""
    root = Path(root)
    out: list[str] = []
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        f = root / name
        if not f.is_file():
            continue
        try:
            data = yaml.safe_load(f.read_text(errors="ignore")) or {}
        except yaml.YAMLError:
            continue
        for svc in (data.get("services") or {}).values():
            if not isinstance(svc, dict):
                continue
            if svc.get("privileged"):
                out.append("privileged")
            if svc.get("network_mode") == "host":
                out.append("host_network")
            if svc.get("devices"):
                out.append("devices")
            if any(UDP_PORT.search(str(p)) for p in svc.get("ports") or []):
                out.append("udp_ports")
        break
    for df in list(root.glob("Dockerfile*"))[:5]:
        if WINDOWS_IMAGE.search(df.read_text(errors="ignore")):
            out.append("windows")
    return sorted(set(out))


def add_advice(plan: DeployPlan, root: str | Path, hints: set[tuple[str, str]]) -> None:
    for m in find_matches(root):
        if m.confidence < 0.9:
            continue  # a matching name alone proves nothing
        code = "oneclick_vm" if m.is_vm else "oneclick_available"
        plan.notes.append(Note(level="info", message=f"Liara has a ready-made app for {m.app}", code=code, params={"app": m.app, "url": CONSOLE}))
    if ("service", "openai") in hints:
        plan.notes.append(Note(level="info", message="the code uses the OpenAI SDK: Liara AI is OpenAI-compatible", code="liara_ai_available",
                               params={"base_url": AI_BASE_URL, "url": CONSOLE}))
    if signals := vm_signals(root):
        plan.notes.append(Note(level="warning", message="this project may not fit the app platform", code="vm_may_be_needed", params={"signals": signals, "url": CONSOLE}))
