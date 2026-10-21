"""One conversation kept in the database (see CHAT_SCHEMA in db.py), plus the view of it the page draws."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

MAX_MESSAGES = 600   # a conversation this long is cleared by the person, not trimmed behind their back


def chat_id(db, user_id: int, project_id: int) -> int:
    row = db.one("SELECT id FROM chats WHERE user_id=? AND project_id=?", (user_id, project_id))
    if row:
        return row["id"]
    db.run("INSERT OR IGNORE INTO chats(user_id,project_id,created_at) VALUES (?,?,?)", (user_id, project_id, time.time()))
    return db.one("SELECT id FROM chats WHERE user_id=? AND project_id=?", (user_id, project_id))["id"]


def clear(db, cid: int) -> None:
    db.run("DELETE FROM chat_messages WHERE chat_id=?", (cid,))
    db.run("DELETE FROM chat_actions WHERE chat_id=?", (cid,))


class DbStore:
    def __init__(self, db, cid: int):
        self.db, self.cid = db, cid

    def history(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.db.q("SELECT body FROM chat_messages WHERE chat_id=? ORDER BY id", (self.cid,))]

    def times(self) -> list[float]:
        return [r["created_at"] for r in self.db.q("SELECT created_at FROM chat_messages WHERE chat_id=? ORDER BY id", (self.cid,))]

    def append(self, msg: dict) -> None:
        n = self.db.one("SELECT COUNT(*) n FROM chat_messages WHERE chat_id=?", (self.cid,))["n"]
        if n >= MAX_MESSAGES:
            raise ValueError("this conversation is full: clear it to start again")
        self.db.run("INSERT INTO chat_messages(chat_id,body,created_at) VALUES (?,?,?)", (self.cid, json.dumps(msg, ensure_ascii=False), time.time()))

    def new_action(self, tool: str, args: dict, tool_call_id: str, info: dict) -> str:
        aid = uuid.uuid4().hex[:12]
        self.db.run("INSERT INTO chat_actions(id,chat_id,tool,args,tool_call_id,info,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (aid, self.cid, tool, json.dumps(args), tool_call_id, json.dumps(info), "pending", time.time()))
        return aid

    def _action(self, r) -> dict:
        return {"id": r["id"], "tool": r["tool"], "args": json.loads(r["args"]), "tool_call_id": r["tool_call_id"], "info": json.loads(r["info"]),
                "status": r["status"], "result": json.loads(r["result"]) if r["result"] else None}

    def get_action(self, action_id: str) -> dict | None:
        r = self.db.one("SELECT * FROM chat_actions WHERE id=? AND chat_id=?", (action_id, self.cid))
        return self._action(r) if r else None

    def resolve_action(self, action_id: str, status: str, result: Any) -> None:
        self.db.run("UPDATE chat_actions SET status=?, result=? WHERE id=? AND chat_id=?", (status, json.dumps(result, default=str), action_id, self.cid))

    def pending(self) -> dict | None:
        r = self.db.one("SELECT * FROM chat_actions WHERE chat_id=? AND status='pending' ORDER BY created_at LIMIT 1", (self.cid,))
        return self._action(r) if r else None

    def actions(self) -> dict[str, dict]:
        return {r["id"]: self._action(r) for r in self.db.q("SELECT * FROM chat_actions WHERE chat_id=?", (self.cid,))}


def view(store: DbStore, labels: dict[str, str] | None = None) -> dict:
    """What the page draws: the person's and the agent's words, the things the agent looked at (as short labels) and the changes it asked about (as cards).
    Tool results and the model's raw tool calls stay out of the page."""
    labels = labels or {}
    acts = store.actions()
    by_call = {a["tool_call_id"]: a for a in acts.values()}
    out: list[dict] = []
    stamps = store.times()
    for i, m in enumerate(store.history()):
        at = stamps[i] if i < len(stamps) else None
        if m["role"] == "user":
            out.append({"id": i, "at": at, "role": "user", "text": m["content"], "hidden_secret": bool(m.get("_hidden"))})
        elif m["role"] == "assistant":
            looked = [labels.get(c["function"]["name"], c["function"]["name"]) for c in m.get("tool_calls") or [] if c["id"] not in by_call]
            cards = [by_call[c["id"]] for c in m.get("tool_calls") or [] if c["id"] in by_call]
            if m.get("content") or looked or cards:
                out.append({"id": i, "at": at, "role": "assistant", "text": m.get("content") or "", "looked": looked,
                            "actions": [{"id": a["id"], "tool": a["tool"], "status": a["status"], "summary": a["info"].get("summary", a["tool"]), "code": a["info"].get("code", a["tool"]), "params": a["info"].get("params") or {}, "files": a["info"].get("files"), "diff": a["info"].get("diff"), "needs_value": bool(a["info"].get("needs_value")),
                                         "value_label": a["info"].get("value_label"), "confirm_name": bool(a["info"].get("confirm_name")),
                                         "price_delta_hourly": a["info"].get("price_delta_hourly"), "warnings": a["info"].get("warnings") or [],
                                         "error": (a["result"] or {}).get("error") if a["status"] == "failed" else None, "link": (a["result"] or {}).get("link") if a["status"] == "done" else None}
                                        for a in cards]})
    pend = store.pending()
    return {"messages": out, "pending": pend["id"] if pend else None}
