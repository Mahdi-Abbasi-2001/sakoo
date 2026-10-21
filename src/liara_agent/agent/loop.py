"""The tool-calling loop: investigate with read-only tools until the model submits a validated plan."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from liara_agent.agent.prompts import digest, system_prompt
from liara_agent.llm.client import LLM, Usage
from liara_agent.plan.model import DeployPlan
from liara_agent.tools.project import ProjectTools, tool_schemas

Emit = Callable[[str, dict], None]


@dataclass
class AgentResult:
    plan: DeployPlan | None
    steps: int
    usage: Usage
    stopped_because: str  # submitted | max_steps | no_tool_calls


def run_agent(llm: LLM, root: str | Path, *, ask: Callable[[str], str] | None = None, emit: Emit = lambda k, d: None,
              max_steps: int = 25, max_plan_attempts: int = 3, language: str = "en") -> AgentResult:
    tools = ProjectTools(root, ask=ask)
    messages: list[dict] = [{"role": "system", "content": system_prompt(language)},
                            {"role": "user", "content": digest(root)}]
    schemas = tool_schemas()
    rejected = 0
    for step in range(1, max_steps + 1):
        msg = llm.chat(messages, schemas)
        calls = msg.get("tool_calls")
        messages.append({"role": "assistant", "content": msg.get("content"), **({"tool_calls": calls} if calls else {})})
        if msg.get("content"):
            emit("agent_message", {"text": msg["content"]})
        if not calls:
            messages.append({"role": "user", "content": "Continue by calling a tool; finish by calling submit_plan."})
            if step > 3 and not tools.submitted:
                return AgentResult(None, step, llm.usage, "no_tool_calls")
            continue
        for c in calls:
            name, args = c["function"]["name"], c["function"]["arguments"]
            emit("tool_call", {"name": name, "arguments": args if len(args) < 300 else args[:300] + "..."})
            result = tools.call(name, args)
            if name == "submit_plan" and result.startswith("error"):
                rejected += 1
                emit("plan_rejected", {"reason": result})
                if rejected >= max_plan_attempts:
                    return AgentResult(None, step, llm.usage, "plan_rejected")
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
        if tools.submitted:
            emit("plan_updated", {"apps": len(tools.submitted.apps), "databases": len(tools.submitted.databases)})
            return AgentResult(tools.submitted, step, llm.usage, "submitted")
    return AgentResult(None, max_steps, llm.usage, "max_steps")
