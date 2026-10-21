"""'Explain this error': the tail of a run's log (and what the agent already diagnosed), scrubbed, put to the language model for a plain-language
explanation. Nothing but log text leaves the machine; secrets are masked by the same scrubber the event log uses, and the model has no tools here."""
from __future__ import annotations

from liara_agent.agent.prompts import LANGUAGES, PERSIAN_STYLE
from liara_agent.events import Event, scrub
from liara_agent.liara import docs
from liara_agent.llm.client import LLM

MAX_LINES = 80
MAX_CHARS = 6000


class ExplainError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def log_tail(events: list[Event], secrets: set[str] | None = None) -> str:
    """The last lines of build output and failures, oldest first, scrubbed, capped."""
    lines: list[str] = []
    for e in events:
        d = e.data
        if e.kind in ("build", "build_error", "warning", "diagnosis", "fix_declined", "fix_applied", "resource_updated", "test_result"):
            text = d.get("line") or d.get("message") or d.get("summary") or d.get("code") or ""
            if e.kind == "test_result":
                text = f"{d.get('name')} {'passed' if d.get('ok') else 'FAILED'} {d.get('detail') or ''}".strip()  # a passing check matters too: it says the problem ended
            elif e.kind == "resource_updated":
                text = f"{d.get('kind')} {d.get('id')} moved from {(d.get('previous') or {}).get('tier')} to {d.get('tier')} ({d.get('tier_label')})"
            elif e.kind == "fix_applied":
                text = f"fix applied: {d.get('code') or d.get('kind')}"
            if text:
                lines.append(f"[{e.kind}] {text}")
        elif e.kind == "run_finished" and d.get("error"):
            lines.append(f"[run_finished] {d['error']}")
    out = str(scrub("\n".join(lines[-MAX_LINES:]), secrets or set()))
    return out[-MAX_CHARS:]


def explain(llm: LLM | None, events: list[Event], language: str = "en", secrets: set[str] | None = None) -> str:
    if llm is None:
        raise ExplainError("llm_unavailable", "no language model is configured on this server", 503)
    tail = log_tail(events, secrets)
    if not tail.strip():
        raise ExplainError("nothing_to_explain", "there is nothing in this run's log to explain yet")
    background = docs.as_text(docs.search(tail[-600:], 3), 900)  # what Liara's own docs say about words in the log: the model may quote it, not invent
    reply = llm.chat([
        {"role": "system", "content": "You help a developer who does not know deployment. Below is the tail of the log of an attempt to deploy their project to Liara (an Iranian PaaS). "
         "Say in 3 to 6 short sentences what went wrong, the most likely cause, and what they should do next. If the log shows no error, say so. "
         "Do not invent facts that are not in the log. Never ask for secrets. Keep file names, commands, variable names and error text exactly as they are. "
         f"Write in {LANGUAGES.get(language, 'English')}. Plain text, no markdown headings." + (" " + PERSIAN_STYLE if language == "fa" else "")},
        {"role": "user", "content": f"LOG:\n{tail}\n\nLIARA DOCUMENTATION THAT MAY BE RELEVANT (ignore it if it is not):\n{background}"},
    ])
    text = (reply.get("content") or "").strip()
    if not text:
        raise ExplainError("llm_empty", "the language model gave no answer", 502)
    return str(scrub(text, secrets or set()))
