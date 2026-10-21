import json

from test_chat_engine import call, say
from test_heal import READY
from test_history import Releases
from test_web import connect, deploy_and_approve, dockerfile_zip, fast, make, register  # noqa: F401  (make and fast are fixtures)

from liara_agent.llm.client import MockLLM

LISTED = [{"_id": "r2", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img2", "gitInfo": {"message": "newer"}},
          {"_id": "r1", "state": "READY", "port": 3000, "projectType": "docker", "imageName": "img1"}]


def deployed(make, script):
    fake = Releases(LISTED, "r2", [READY])
    llm = MockLLM(script)
    c, app, deps = make(fake, llm=llm)
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    return c, deps, pid, llm


def test_chat_needs_a_language_model(make):
    c, _, _ = make()
    register(c)
    assert c.get("/api/chat").json()["available"] is False
    assert c.post("/api/chat/messages", json={"text": "hi"}).json()["detail"]["code"] == "llm_unavailable"


def test_a_question_is_answered_from_a_read_tool(make):
    c, deps, pid, llm = deployed(make, [call("get_releases"), say("You are on release r2; r1 is the one before.")])
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "which versions do I have?"}).json()
    assert [m["role"] for m in out["messages"]] == ["user", "assistant", "assistant"]
    assert out["messages"][1]["looked"] == ["get_releases"] and out["messages"][2]["text"].startswith("You are on release r2")
    assert out["pending"] is None
    assert c.get("/api/chat", params={"project_id": pid}).json()["messages"] == out["messages"]      # kept


def test_a_rollback_waits_for_the_card_and_runs_after_approval(make):
    c, deps, pid, llm = deployed(make, [call("rollback", release_id="r1"), say("Done, you are back on r1.")])
    uploads = len(deps.fake.uploads)
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "go back to the previous version"}).json()
    card = out["messages"][-1]["actions"][0]
    assert card["status"] == "pending" and "r1" in card["summary"] and out["pending"] == card["id"]
    assert len(deps.fake.uploads) == uploads                                                       # nothing happened yet
    assert c.post("/api/chat/messages", json={"project_id": pid, "text": "hello?"}).json()["detail"]["code"] == "action_pending"
    done = c.post(f"/api/chat/actions/{card['id']}", json={"project_id": pid, "decision": "approve"}).json()
    import time
    for _ in range(100):                                   # the rollback runs in the background
        if len(deps.fake.uploads) > uploads and c.get(f"/api/projects/{pid}/deploys").json()["deploys"][0]["state"] != "running":
            break
        time.sleep(0.1)
    assert len(deps.fake.uploads) == uploads + 1
    assert c.get(f"/api/projects/{pid}/deploys").json()["deploys"][0]["state"] == "succeeded"
    assert done["pending"] is None and done["messages"][-1]["text"] == "Done, you are back on r1."
    assert next(m for m in done["messages"] if m.get("actions"))["actions"][0]["status"] == "done"


def test_declining_changes_nothing(make):
    c, deps, pid, llm = deployed(make, [call("rollback", release_id="r1"), say("Okay, nothing changed.")])
    uploads = len(deps.fake.uploads)
    card = c.post("/api/chat/messages", json={"project_id": pid, "text": "roll back"}).json()["messages"][-1]["actions"][0]
    out = c.post(f"/api/chat/actions/{card['id']}", json={"project_id": pid, "decision": "decline"}).json()
    assert len(deps.fake.uploads) == uploads and out["messages"][-1]["text"] == "Okay, nothing changed."


def test_an_impossible_change_is_refused_before_a_card_exists(make):
    c, deps, pid, llm = deployed(make, [call("rollback", release_id="r2"), say("That one is already running.")])
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "roll back to r2"}).json()
    assert out["pending"] is None and not any(m.get("actions") for m in out["messages"])
    assert any("already the running one" in json.dumps(s) for s in llm.seen[1])                   # the model was told why


def test_a_secret_typed_in_the_chat_never_reaches_the_model_or_the_page(make):
    c, deps, pid, llm = deployed(make, [say("That looked like a secret, so I hid it.")])
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "my key is sk-abcdefghijklmnop123456"}).json()
    assert out["hidden_secret"] is True and "sk-abc" not in json.dumps(out) and "sk-abc" not in json.dumps(llm.seen)


def test_a_dangerous_action_needs_the_project_name(make):
    c, deps, pid, llm = deployed(make, [call("delete_everything"), say("Deleted.")])
    card = c.post("/api/chat/messages", json={"project_id": pid, "text": "remove it all"}).json()["messages"][-1]["actions"][0]
    assert card["confirm_name"] is True
    bad = c.post(f"/api/chat/actions/{card['id']}", json={"project_id": pid, "decision": "approve", "confirm_name": "nope"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "action_invalid"


def test_conversations_are_private_and_can_be_cleared(make):
    c, deps, pid, llm = deployed(make, [say("hi")])
    c.post("/api/chat/messages", json={"project_id": pid, "text": "hello"})
    c.delete("/api/chat", params={"project_id": pid})
    assert c.get("/api/chat", params={"project_id": pid}).json()["messages"] == []
    c.post("/api/auth/logout")
    register(c, "bob")
    assert c.get("/api/chat", params={"project_id": pid}).status_code == 404                       # not bob's project


def test_the_general_chat_can_list_projects_and_needs_a_name_for_one(make):
    c, deps, pid, llm = deployed(make, [call("list_projects"), call("get_costs", id="c2"), say("ok")])
    out = c.post("/api/chat/messages", json={"text": "what do I have?"}).json()
    assert out["messages"][-1]["text"] == "ok"
    tool_results = [json.loads(m["content"]) for m in llm.seen[-1] if m["role"] == "tool"]
    assert tool_results[0][0]["name"] == "blog" and "say which project" in tool_results[1]["error"]


def test_deploying_chat_edits_keeps_the_fixes_the_agent_made_earlier(make, tmp_path):
    c, deps, pid, llm = deployed(make, [
        call("edit_files", summary="Change the port", files=[{"path": "Dockerfile", "replace": [{"find": "EXPOSE 3000", "with": "EXPOSE 8080"}]}]), say("Changed."),
        call("deploy_edits"), say("Releasing it."),
    ])
    state = tmp_path / "data" / "projects" / str(pid) / "src" / ".agent-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "fixes.patch").write_text("--- /dev/null\n+++ b/agent-fix.txt\n@@ -0,0 +1 @@\n+fixed by the agent\n")   # what the agent had to change to make the app run
    uploads = len(deps.fake.uploads)
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "use 8080"}).json()
    c.post(f"/api/chat/actions/{out['messages'][-1]['actions'][0]['id']}", json={"project_id": pid, "decision": "approve"})
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "put it online"}).json()
    c.post(f"/api/chat/actions/{out['pending']}", json={"project_id": pid, "decision": "approve"})
    import io, tarfile, time
    for _ in range(50):
        if len(deps.fake.uploads) > uploads:
            break
        time.sleep(0.1)
    blob = deps.fake.uploads[-1]
    tar = tarfile.open(fileobj=io.BytesIO(blob.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]))
    assert tar.extractfile("agent-fix.txt").read().strip() == b"fixed by the agent" and b"EXPOSE 8080" in tar.extractfile("Dockerfile").read()
    for _ in range(50):                                   # the page follows the release through this list: its state and the last build lines
        row = c.get(f"/api/projects/{pid}/deploys").json()["deploys"][0]
        if row["state"] != "running":
            break
        time.sleep(0.1)
    assert row["message"] == "edits made in the chat" and "tail" in row


def test_build_mode_new_project_edit_undo_and_deploy_edits(make):
    files = [{"path": "Dockerfile", "content": "FROM python:3.12\nEXPOSE 3000\n"}, {"path": "app.py", "content": "print('hello')\n"}]
    c, deps, pid, llm = deployed(make, [
        call("edit_files", summary="Change the greeting", files=[{"path": "Dockerfile", "replace": [{"find": "EXPOSE 3000", "with": "EXPOSE 8080"}]}]),
        say("Changed the port."),
        call("deploy_edits"), say("Releasing it."),
    ])
    uploads = len(deps.fake.uploads)
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "use port 8080"}).json()
    card = out["messages"][-1]["actions"][0]
    assert card["code"] == "edit_files" and card["files"][0]["status"] == "changed" and "+EXPOSE 8080" in card["diff"]
    done = c.post(f"/api/chat/actions/{card['id']}", json={"project_id": pid, "decision": "approve"}).json()
    assert done["messages"][-1]["text"] == "Changed the port."
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "put it online"}).json()
    c.post(f"/api/chat/actions/{out['pending']}", json={"project_id": pid, "decision": "approve"})
    import time
    for _ in range(50):                                   # the release runs in the background
        if len(deps.fake.uploads) > uploads:
            break
        time.sleep(0.1)
    assert len(deps.fake.uploads) == uploads + 1


def test_a_path_outside_the_project_never_gets_a_card(make):
    c, deps, pid, llm = deployed(make, [call("edit_files", summary="x", files=[{"path": "../../etc/x", "content": "y"}]), say("I cannot do that.")])
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "write outside"}).json()
    assert out["pending"] is None and any("not a path inside" in json.dumps(m) for m in llm.seen[1])


def test_the_general_chat_can_start_an_empty_project_and_fill_it(make):
    c, deps, pid, llm = deployed(make, [call("new_project", name="Todo App"), say("Created."),
                                        call("edit_files", project="todo-app", summary="files", files=[{"path": "Dockerfile", "content": "FROM node:20\n"}]), say("Written.")])
    out = c.post("/api/chat/messages", json={"text": "build me a todo app"}).json()
    assert out["pending"] is None and out["messages"][1]["looked"] == ["new_project"]        # a project record costs nothing and touches nothing on Liara: no card for it
    out = c.post("/api/chat/messages", json={"text": "write the files"}).json()
    assert out["messages"][-1]["actions"][0]["files"][0]["path"] == "Dockerfile"
    c.post(f"/api/chat/actions/{out['pending']}", json={"decision": "approve"})
    projects = c.get("/api/projects").json()["projects"]
    new = next(p for p in projects if p["name"] == "todo-app")
    assert new["source_kind"] == "zip"


def read_stream(r):
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_an_answer_can_be_streamed_and_ends_with_the_saved_conversation(make):
    c, deps, pid, llm = deployed(make, [call("get_releases"), say("You are on release r2, the newest one.")])
    r = c.post("/api/chat/messages/stream", json={"project_id": pid, "text": "which versions?"})
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = read_stream(r)
    assert [e["type"] for e in ev][:2] == ["step", "tool"] and ev[1]["name"] == "get_releases"
    assert "".join(e["text"] for e in ev if e["type"] == "delta") == "You are on release r2, the newest one."
    assert sum(e["type"] == "delta" for e in ev) > 3                         # in pieces, not all at once
    done = ev[-1]
    assert done["type"] == "done" and done["messages"][-1]["text"] == "You are on release r2, the newest one."
    saved = c.get("/api/chat", params={"project_id": pid}).json()["messages"]
    assert [(m["role"], m["text"]) for m in saved] == [(m["role"], m["text"]) for m in done["messages"]]


def test_a_streamed_change_proposal_and_its_decision(make):
    c, deps, pid, llm = deployed(make, [call("rollback", release_id="r1"), say("Done.")])
    ev = read_stream(c.post("/api/chat/messages/stream", json={"project_id": pid, "text": "go back"}))
    assert any(e["type"] == "card" for e in ev) and ev[-1]["pending"]
    again = c.post("/api/chat/messages/stream", json={"project_id": pid, "text": "hello"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "action_pending"
    ev = read_stream(c.post(f"/api/chat/actions/{ev[-1]['pending']}/stream", json={"project_id": pid, "decision": "decline"}))
    assert ev[-1]["type"] == "done" and ev[-1]["messages"][-1]["text"] == "Done." and ev[-1]["pending"] is None


def test_a_failure_while_streaming_is_reported_as_an_event(make):
    c, deps, pid, llm = deployed(make, [])
    llm.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("provider down"))
    ev = read_stream(c.post("/api/chat/messages/stream", json={"project_id": pid, "text": "hi"}))
    assert ev[-1] == {"type": "error", "code": "llm_failed", "message": "the language model could not be reached"}
    assert c.post("/api/chat/messages/stream", json={"project_id": pid, "text": "again"}).status_code == 200   # the lock was released


def test_the_assistant_is_unavailable_when_the_model_cannot_be_reached(make):
    class Blocked(MockLLM):
        def reachable(self, timeout=4.0):
            return False
    c, _, _ = make(llm=Blocked([]))
    register(c)
    assert c.get("/api/chat").json()["available"] is False


def test_an_online_project_is_never_deployed_again_from_scratch(make):
    c, deps, pid, llm = deployed(make, [call("start_run", mode="deploy"), say("I will release the edit instead.")])
    out = c.post("/api/chat/messages", json={"project_id": pid, "text": "deploy again"}).json()
    assert out["pending"] is None and any("already online" in json.dumps(m) for m in llm.seen[1])   # no card: the model is told to use deploy_edits
