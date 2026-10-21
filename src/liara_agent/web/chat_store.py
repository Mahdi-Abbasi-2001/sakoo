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


def forget_project(db, user_id: int, project_id: int, same_project) -> None:
    """A deleted project leaves no conversation behind: its own chat goes, and a change for it still waiting in the general chat is closed, as if declined,
    so the general chat is not blocked by a card that could only fail. `same_project(name)` says whether a project name given to a tool means this project."""
    db.run("DELETE FROM chats WHERE user_id=? AND project_id=?", (user_id, project_id))
    row = db.one("SELECT id FROM chats WHERE user_id=? AND project_id=0", (user_id,))
    if not row:
        return
    store = DbStore(db, row["id"])
    for a in store.actions().values():
        if a["status"] == "pending" and same_project(str(a["args"].get("project") or "")):
            result = {"declined": True, "message": "The project was deleted, so this change was not made."}
            store.resolve_action(a["id"], "declined", result)
            store.append({"role": "tool", "tool_call_id": a["tool_call_id"], "content": json.dumps(result)})


def hand_over(db, user_id: int, project_id: int) -> None:
    """The conversation that led to a project goes with it: when a deployment starts from the general chat, the project's own chat (the one on its run page)
    gets a copy of what was said, so the agent there still knows the request and the changes already made. Nothing is copied over an existing conversation,
    and not while a change is still waiting (its card would block the project's chat)."""
    src = db.one("SELECT id FROM chats WHERE user_id=? AND project_id=0", (user_id,))
    if not src:
        return
    dst = chat_id(db, user_id, project_id)
    if db.one("SELECT 1 FROM chat_messages WHERE chat_id=?", (dst,)) or db.one("SELECT 1 FROM chat_actions WHERE chat_id=? AND status='pending'", (src["id"],)):
        return
    for r in db.q("SELECT body, created_at FROM chat_messages WHERE chat_id=? ORDER BY id", (src["id"],)):
        db.run("INSERT INTO chat_messages(chat_id,body,created_at) VALUES (?,?,?)", (dst, r["body"], r["created_at"]))
    for r in db.q("SELECT * FROM chat_actions WHERE chat_id=? ORDER BY created_at", (src["id"],)):
        db.run("INSERT INTO chat_actions(id,chat_id,tool,args,tool_call_id,info,status,result,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (uuid.uuid4().hex[:12], dst, r["tool"], r["args"], r["tool_call_id"], r["info"], r["status"], r["result"], r["created_at"]))


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
