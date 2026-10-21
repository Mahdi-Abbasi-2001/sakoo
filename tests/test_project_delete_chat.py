import sqlite3

from test_chat_engine import call, say
from test_web import make, new_project, register  # noqa: F401  (make is a fixture)

from liara_agent.llm.client import MockLLM
from liara_agent.web.db import DB


def test_deleting_a_project_closes_its_waiting_change_and_forgets_its_chat(make):
    llm = MockLLM([call("edit_files", project="blog", summary="files", files=[{"path": "app.txt", "content": "hi\n"}]), say("Proposed."),
                   say("Its own chat."), say("Still here.")])
    c, app, _ = make(llm=llm)
    register(c)
    pid = new_project(c)
    out = c.post("/api/chat/messages", json={"text": "add a file"}).json()
    assert out["pending"]                                                              # the general chat waits for a decision about blog
    c.post("/api/chat/messages", json={"text": "hello", "project_id": pid})
    assert c.delete(f"/api/projects/{pid}", params={"confirm": "blog"}).status_code == 200
    out = c.get("/api/chat", params={"project_id": 0}).json()
    assert out["pending"] is None and out["messages"][-1]["actions"][0]["status"] == "declined"   # no card left that could only fail
    assert c.post("/api/chat/messages", json={"text": "anything else?"}).status_code == 200   # and the chat is not blocked
    assert app.state.db.one("SELECT COUNT(*) n FROM chats WHERE project_id=?", (pid,))["n"] == 0


def test_a_new_project_never_gets_a_deleted_projects_id(make):
    c, _, _ = make()
    register(c)
    first = new_project(c, "one")
    c.delete(f"/api/projects/{first}", params={"confirm": "one"})
    assert new_project(c, "two") != first


def test_an_older_database_is_rebuilt_without_losing_rows_and_stops_reusing_ids(tmp_path):
    path = tmp_path / "app.db"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE projects (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL,
          source_kind TEXT NOT NULL, git_url TEXT, created_at REAL NOT NULL, git_token_enc TEXT, git_branch TEXT);
        CREATE TABLE chats (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, project_id INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, UNIQUE(user_id, project_id));
        INSERT INTO users VALUES (1, 'alice', 'x', 0);
        INSERT INTO projects(id,user_id,name,source_kind,created_at,git_branch) VALUES (1, 1, 'kept', 'zip', 0, 'main');
        INSERT INTO chats VALUES (1, 1, 5, 0);   -- a chat of a project 5 that was deleted before this fix
    """)
    old.commit(); old.close()
    db = DB(path)
    assert [dict(r) for r in db.q("SELECT id, name, git_branch FROM projects")] == [{"id": 1, "name": "kept", "git_branch": "main"}]
    assert db.run("INSERT INTO projects(user_id,name,source_kind,created_at) VALUES (1,'new','zip',0)") == 6
    db.run("INSERT INTO runs(id,user_id,project_id,mode,status,created_at) VALUES ('r',1,1,'deploy','ok',0)")   # foreign keys still point at projects
    DB(path)   # opening it again changes nothing
    assert db.one("SELECT COUNT(*) n FROM projects")["n"] == 2


def test_a_deployment_started_from_the_general_chat_takes_the_conversation_to_the_projects_chat(make):
    from test_web import connect, wait_for
    llm = MockLLM([call("new_project", name="Todo"), call("start_run", project="todo", mode="deploy"), say("Starting."), say("Yes, the todo app."), say("ok")])
    c, _, _ = make(llm=llm)
    register(c)
    connect(c)
    out = c.post("/api/chat/messages", json={"text": "build me a todo app"}).json()
    assert out["pending"]                                                              # the deployment card waits
    pid = next(p["id"] for p in c.get("/api/projects").json()["projects"] if p["name"] == "todo")
    assert c.get("/api/chat", params={"project_id": pid}).json()["messages"] == []     # nothing there before the person approves
    out = c.post(f"/api/chat/actions/{out['pending']}", json={"decision": "approve"}).json()
    mine = c.get("/api/chat", params={"project_id": pid}).json()
    assert [m["text"] for m in mine["messages"] if m["role"] == "user"] == ["build me a todo app"]   # the request came along
    assert any(a["tool"] == "start_run" and a["status"] == "done" for m in mine["messages"] for a in (m.get("actions") or []))
    assert mine["pending"] is None
    reply = c.post("/api/chat/messages", json={"text": "what did I ask for?", "project_id": pid})
    assert reply.status_code == 200
    sent = [m.get("content") for m in llm.seen[-1] if m["role"] == "user"]
    assert sent == ["build me a todo app", "what did I ask for?"]                      # and the agent there is sent the whole conversation, not just the new question
    assert any(m["role"] == "tool" for m in llm.seen[-1])                               # including what it did (a call must keep its result)
    run = c.get("/api/runs").json()["runs"][0]
    c.post(f"/api/runs/{run['id']}/cancel")
