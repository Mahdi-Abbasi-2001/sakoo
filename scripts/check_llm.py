#!/usr/bin/env python3
"""Is this model endpoint good enough for the agent? Run it before switching providers (for example to BoxAPI):

    LLM_BASE_URL=https://.../v1 LLM_API_KEY=... LLM_MODEL=... python scripts/check_llm.py [project-folder]

It goes through the same client the agent uses and checks what the agent depends on, then plans a real project with the model. From Iran add the proxy variables for
a provider that blocks the country. Nothing here touches Liara. Exit code 1 when a required check fails.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from liara_agent.agent.planner import run_agent  # noqa: E402
from liara_agent.config import get_settings  # noqa: E402
from liara_agent.llm.client import LLMError, OpenAICompatLLM  # noqa: E402

TOOLS = [{"type": "function", "function": {"name": "read_file", "description": "Read a project file.",
                                           "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
ROOT = Path(__file__).resolve().parents[1]


def check_models(llm, s):
    n = len(llm._client.models.list().data)
    return f"{n} models listed"


def check_chat(llm, s):
    t = time.time()
    r = llm.chat([{"role": "user", "content": "Reply with the single word: ok"}])
    if not (r.get("content") or "").strip():
        raise AssertionError("empty answer")
    return f"answered in {time.time() - t:.1f}s: {r['content'].strip()[:30]!r}"


def check_stream(llm, s):
    parts: list[str] = []
    r = llm.chat([{"role": "user", "content": "Count from one to five in words."}], None, on_text=parts.append)
    if not parts:
        raise AssertionError("the answer did not arrive in pieces (no streaming)")
    return f"{len(parts)} pieces; text {len(r['content'] or '')} chars"


def check_tool_call(llm, s):
    r = llm.chat([{"role": "system", "content": "Look before you answer: use the tools."}, {"role": "user", "content": "What does server/index.js do?"}], TOOLS)
    calls = r.get("tool_calls") or []
    if not calls:
        raise AssertionError("no tool call returned")
    args = json.loads(calls[0]["function"]["arguments"])
    if "path" not in args:
        raise AssertionError(f"arguments without the required field: {args}")
    return f"{calls[0]['function']['name']}({args})"


def check_round_trip(llm, s):
    msgs = [{"role": "user", "content": "What does app.py do? Read it first."}]
    first = llm.chat(msgs, TOOLS)
    calls = first.get("tool_calls") or []
    if not calls:
        raise AssertionError("no tool call to continue from")
    msgs += [{"role": "assistant", "content": first.get("content"), "tool_calls": calls},
             {"role": "tool", "tool_call_id": calls[0]["id"], "content": "print('hello from the shop')"}]
    final = llm.chat(msgs, TOOLS)
    if not (final.get("content") or "").strip():
        raise AssertionError("empty answer after the tool result")
    return final["content"].strip()[:60]


def check_parallel(llm, s):
    r = llm.chat([{"role": "user", "content": "Read both a.txt and b.txt before answering."}], TOOLS)
    n = len(r.get("tool_calls") or [])
    if n == 0:
        raise AssertionError("no tool call")
    return f"{n} call(s) in one turn"


def check_persian(llm, s):
    r = llm.chat([{"role": "user", "content": "در یک جمله کوتاه به فارسی بگو دیپلوی یعنی چه."}])
    text = r.get("content") or ""
    if len(re.findall(r"[؀-ۿ]", text)) < 10:
        raise AssertionError(f"not Persian: {text[:60]!r}")
    return text.strip()[:70]


def make_plan_check(folder: Path):
    def check(llm, s):
        t = time.time()
        res = run_agent(llm, folder, ask=lambda q, options=None: (options or ["yes"])[0], emit=lambda k, d: None, max_steps=s.max_agent_steps, language="en")
        if not res.plan or not res.plan.apps:
            raise AssertionError("the agent ended without a plan")
        a = res.plan.apps[0]
        return f"{len(res.plan.apps)} app(s): {a.name} {a.platform} :{a.port} in {time.time() - t:.0f}s, {llm.usage.calls} model calls"
    return check


def main(argv: list[str]) -> int:
    s = get_settings()
    if not s.llm_api_key:
        print("set LLM_API_KEY (and LLM_BASE_URL, LLM_MODEL)")
        return 2
    folder = Path(argv[1]) if len(argv) > 1 else ROOT / "demo" / "payments-shop"
    llm = OpenAICompatLLM(s)
    print(f"endpoint {s.llm_base_url}  model {s.llm_model}")
    rows = [("lists models", False, check_models), ("plain answer", True, check_chat), ("streaming", True, check_stream), ("tool call", True, check_tool_call),
            ("tool result round trip", True, check_round_trip), ("several tool calls at once", False, check_parallel), ("Persian answer", True, check_persian),
            (f"plans {folder.name}", True, make_plan_check(folder))]
    failed = 0
    for name, required, fn in rows:
        try:
            print(f"  PASS  {name:<28} {fn(llm, s)}")
        except (LLMError, Exception) as e:   # noqa: B014
            why = getattr(e, "why", None) or f"{type(e).__name__}: {str(e)[:140]}"
            print(f"  {'FAIL' if required else 'warn'}  {name:<28} {why}")
            failed += 1 if required else 0
    print(f"tokens used: {llm.usage.prompt_tokens} in, {llm.usage.completion_tokens} out over {llm.usage.calls} calls")
    print("RESULT:", "ready for the agent" if not failed else f"{failed} required check(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
