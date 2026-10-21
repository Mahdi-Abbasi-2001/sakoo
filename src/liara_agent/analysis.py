"""When no rule recognises the problem: let the language model read the evidence and propose causes, and keep it honest.

The model sees only what a person would paste to a colleague (the app's last log lines and the numbers the diagnosis measured, scrubbed of secrets; variable NAMES, never
values). Each hypothesis must quote log lines, and a quote that is not really in the log is dropped: a cause with no real quote stays but is marked ungrounded. The model
cannot say "high": this is a second opinion below the rules, and it never changes anything.
"""
from __future__ import annotations

import json

from liara_agent.agent.prompts import LANGUAGES, PERSIAN_STYLE
from liara_agent.events import scrub
from liara_agent.llm.client import LLM

MAX_LINES = 70
MAX_CHARS = 7000
REPORT = {"type": "function", "function": {"name": "report_causes", "description": "Report the likely causes of the problem, most likely first.", "parameters": {
    "type": "object", "properties": {"hypotheses": {"type": "array", "maxItems": 3, "items": {"type": "object", "properties": {
        "title": {"type": "string", "description": "the cause in one short sentence"},
        "explanation": {"type": "string", "description": "why the evidence points to it, 1 to 3 sentences"},
        "evidence": {"type": "array", "items": {"type": "string"}, "description": "log lines copied exactly from the log"},
        "next_step": {"type": "string", "description": "the one thing to do or look at next"},
        "confidence": {"type": "string", "enum": ["low", "medium"]}},
        "required": ["title", "explanation", "evidence", "next_step", "confidence"]}}}, "required": ["hypotheses"]}}}


def context(result: dict, logs: list[str], secrets: set[str] | None = None) -> str:
    """The text the model reads: what was measured, then the log tail. Scrubbed."""
    sg = result.get("signals") or {}
    facts = {"state": result.get("state"), "http": sg.get("http"), "release": sg.get("release"), "memory": sg.get("memory"), "cpu": sg.get("cpu"),
             "rules_found": [c["code"] for c in result.get("causes", [])]}
    tail = "\n".join(ln.strip()[:240] for ln in logs[-MAX_LINES:] if ln.strip())
    return str(scrub(f"MEASURED:\n{json.dumps(facts, default=str)}\n\nLOG (oldest first):\n{tail}", secrets or set()))[:MAX_CHARS]


def analyze(llm: LLM, result: dict, logs: list[str], language: str = "en", secrets: set[str] | None = None) -> list[dict]:
    """Up to three hypotheses [{title, explanation, evidence, next_step, confidence, grounded}], or [] when the model had nothing useful to say."""
    ctx = context(result, logs, secrets)
    reply = llm.chat([
        {"role": "system", "content": "You are a site reliability engineer helping a developer who does not know deployment. Below is what was measured on their running app on Liara "
         "(an Iranian PaaS) and the tail of its log. The built-in rules did not explain the problem. Propose at most three likely causes, most likely first, by calling report_causes. "
         "Use ONLY the evidence below: copy log lines exactly into `evidence`, never invent a line, a file or a number. If the evidence does not support a cause, say fewer causes. "
         "Never say you are certain. Never ask for secrets. Keep file names, commands and error text exactly as they are. "
         f"Write titles, explanations and next steps in {LANGUAGES.get(language, 'English')}." + (" " + PERSIAN_STYLE if language == "fa" else "")},
        {"role": "user", "content": ctx}], [REPORT])
    raw: list = []
    for call in reply.get("tool_calls") or []:
        if call["function"]["name"] == "report_causes":
            try:
                raw = json.loads(call["function"]["arguments"]).get("hypotheses") or []
            except (ValueError, AttributeError):
                raw = []
    log_text = "\n".join(logs)
    out: list[dict] = []
    for h in raw[:3]:
        if not isinstance(h, dict) or not str(h.get("title") or "").strip():
            continue
        quotes = [str(q).strip() for q in h.get("evidence") or [] if str(q).strip()]
        real = [q[:240] for q in quotes if q in log_text]    # a quote must really be in the log
        out.append({"title": str(scrub(str(h["title"]).strip(), secrets or set()))[:200], "explanation": str(scrub(str(h.get("explanation") or "").strip(), secrets or set()))[:600],
                    "evidence": real[:3], "next_step": str(scrub(str(h.get("next_step") or "").strip(), secrets or set()))[:300],
                    "confidence": "medium" if h.get("confidence") == "medium" and real else "low", "grounded": bool(real)})
    return out
