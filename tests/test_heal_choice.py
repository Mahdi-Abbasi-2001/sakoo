"""The choice for a code problem (Sakoo fixes it / your own agent fixes it) and the hand-off to the developer's agent."""
import threading
import time
from pathlib import Path

import pytest

from liara_agent.events import Run, RunCancelled
from liara_agent.handoff import CodeFixOptions, Handoff, HandoffError, make_chooser
from liara_agent.heal.loop import HealIO
from liara_agent.heal.repair import repair_fix
from liara_agent.interact import ScriptedChannel
from liara_agent.llm.client import MockLLM
from tests.test_heal import READY, FakeLiara, no_real_http, run  # noqa: F401
from tests.test_repair import SYNTAX, call

CRASH = {"state": "UNHEALTHY"}
FILES = {"Dockerfile": "FROM node\n", "server.js": "const port = ;\n"}
FIX = [{"path": "server.js", "content": "const port = 3000;\n"}]


def in_background(fn, delay=0.05):
    t = threading.Thread(target=lambda: (time.sleep(delay), fn()), daemon=True)
    t.start()
    return t


def agent_after_wait(handoff, action):
    """The developer's agent: waits until the run is waiting, then does `action(handoff)`."""
    def go():
        for _ in range(200):
            if handoff.waiting:
                return action(handoff)
            time.sleep(0.02)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t


def io_for(handoff, choice, llm=None, **kw):
    io = HealIO(choose_code_fix=lambda d, c: choice, emit=lambda k, d: io.seen.append((k, d)),
                await_handoff=lambda d, f, c, root: handoff.wait(c.app, {"code": d.code}, {"log_tail": f.runtime_lines}, root),
                repair=lambda d, f, c, root: repair_fix(llm, d, f, c, root) if llm else None, **kw)
    io.seen = []
    return io


def test_the_developers_agent_sends_the_fix_and_the_same_run_redeploys_and_checks(tmp_path):
    handoff = Handoff(Run(), mcp_url="https://sakoo.test/mcp", timeout=30)
    seen = {}
    agent_after_wait(handoff, lambda h: seen.update(waiting=h.waiting, answer=h.submit_fix(FIX, "fixed the semicolon")))
    fake = FakeLiara([CRASH, READY], runtime=[SYNTAX])
    io = io_for(handoff, "own_agent", confirm=lambda q, **kw: pytest.fail("nothing to confirm: the developer's own agent acted"))
    out, _, _ = run(tmp_path, fake, io=io, files=FILES)
    assert out.ok and len(fake.uploads) == 2
    assert (tmp_path / "server.js").read_text() == "const port = ;\n"
    assert (tmp_path / ".agent-state" / "patched" / "t-web" / "server.js").read_text() == "const port = 3000;\n"
    assert seen["waiting"]["diagnosis"]["code"] == "code_error" and seen["answer"]["accepted"] is True
    kinds = [e.kind for e in handoff.run.events]
    assert kinds == ["handoff_waiting", "handoff_received"]
    assert "+const port = 3000;" in (tmp_path / ".agent-state" / "fixes.patch").read_text()   # the pull request / download still has it


def test_a_bad_fix_from_the_agent_is_refused_at_once_so_it_can_correct_itself(tmp_path):
    handoff = Handoff(Run(), timeout=30)
    errors = []

    def agent(h):
        for bad in ([{"path": "../x.js", "content": "1"}], [{"path": "server.js", "content": "k='sk-abcdefghijklmnopqrstuvwxyz123456'"}], []):
            try:
                h.submit_fix(bad)
            except HandoffError as e:
                errors.append(str(e))
        h.submit_fix(FIX)
    agent_after_wait(handoff, agent)
    out, _, _ = run(tmp_path, FakeLiara([CRASH, READY], runtime=[SYNTAX]), io=io_for(handoff, "own_agent"), files=FILES)
    assert out.ok and len(errors) == 3 and "not a path inside the project" in errors[0] and "looks like a secret" in errors[1]


def test_a_pushed_fix_is_released_from_the_newest_code(tmp_path):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "Dockerfile").write_text("FROM node\n")
    (fresh / "server.js").write_text("const port = 3000;\n")
    handoff = Handoff(Run(), timeout=30, fetch_latest=lambda: fresh)
    agent_after_wait(handoff, lambda h: h.redeploy())
    fake = FakeLiara([CRASH, READY], runtime=[SYNTAX])
    out, _, _ = run(tmp_path / "p" if (tmp_path / "p").mkdir() is None else tmp_path, fake, io=io_for(handoff, "own_agent"), files=FILES)
    assert out.ok and len(fake.uploads) == 2
    assert [e.data["how"] for e in handoff.run.events if e.kind == "handoff_received"] == ["redeploy"]


def test_redeploy_is_refused_for_a_project_that_did_not_come_from_git():
    handoff = Handoff(Run(), timeout=30)
    refused = []

    def agent(h):
        refused.append(_message(h.redeploy))
        h.choose_sakoo()   # lets the waiting run end
    agent_after_wait(handoff, agent)
    handoff.wait("a", {}, {}, Path("."))
    assert "nothing to download" in refused[0]


def _message(fn) -> str:
    try:
        fn()
    except HandoffError as e:
        return str(e)
    return ""


def test_the_person_can_stop_waiting_and_let_sakoo_fix_it(tmp_path):
    handoff = Handoff(Run(), timeout=30)
    agent_after_wait(handoff, lambda h: h.choose_sakoo())
    llm = MockLLM([call("propose_fix", summary="s", changes=FIX)])
    out, _, _ = run(tmp_path, FakeLiara([CRASH, READY], runtime=[SYNTAX]), io=io_for(handoff, "own_agent", llm=llm, confirm=lambda q, **kw: True), files=FILES)
    assert out.ok and [e.data["how"] for e in handoff.run.events if e.kind == "handoff_received"] == ["sakoo"]


def test_nobody_answering_in_time_reports_the_failure_honestly(tmp_path):
    handoff = Handoff(Run(), timeout=1)
    fake = FakeLiara([CRASH], runtime=[SYNTAX])
    out, _, _ = run(tmp_path, fake, io=io_for(handoff, "own_agent"), files=FILES)
    assert not out.ok and out.diagnosis.code == "code_error" and len(fake.uploads) == 1
    assert [e.kind for e in handoff.run.events] == ["handoff_waiting", "request_expired"]


def test_stopping_the_run_ends_the_wait():
    run_ = Run()
    handoff = Handoff(run_, timeout=30)
    in_background(run_.cancel, 0.2)
    with pytest.raises(RunCancelled):
        handoff.wait("a", {}, {}, Path("."))


def test_nothing_can_be_sent_to_a_run_that_is_not_waiting():
    handoff = Handoff(Run())
    for fn in (lambda: handoff.submit_fix(FIX), handoff.redeploy):
        with pytest.raises(HandoffError, match="not waiting"):
            fn()
    assert handoff.choose_sakoo() is False


# -- the question --------------------------------------------------------------------------------------------------------------
class D:
    code, summary, evidence, params, fixes = "code_error", "an error", ["SyntaxError"], {"location": "server.js:3"}, []


class Ctx:
    app = "t-web"


def test_the_question_has_two_answers_and_remembering_them_is_optional():
    remembered = []
    opts = CodeFixOptions(Handoff(Run(), mcp_url="https://sakoo.test/mcp"), remember=remembered.append)
    ch = ScriptedChannel(Run(), answers=["own_agent_remember", "sakoo", "something unexpected"])
    choose = make_chooser(ch, opts, model_available=True)
    assert choose(D, Ctx) == "own_agent" and remembered == ["own_agent"]
    assert choose(D, Ctx) == "sakoo" and remembered == ["own_agent"]
    assert choose(D, Ctx) == "sakoo"   # no usable answer: the careful default
    q = [e for e in ch.run.events if e.kind == "question"][0].data
    assert q["code"] == "code_fix_choice" and q["options"] == ["sakoo", "own_agent", "sakoo_remember", "own_agent_remember"]
    assert q["params"]["location"] == "server.js:3" and q["params"]["mcp_url"] == "https://sakoo.test/mcp" and q["params"]["sakoo_can_fix"] is True


def test_a_remembered_choice_is_not_asked_again_and_without_a_model_the_card_says_sakoo_cannot():
    ch = ScriptedChannel(Run(), answers=[])
    assert make_chooser(ch, CodeFixOptions(Handoff(Run()), remembered="own_agent"), model_available=False)(D, Ctx) == "own_agent"
    assert not [e for e in ch.run.events if e.kind == "question"]
    make_chooser(ch, CodeFixOptions(Handoff(Run())), model_available=False)(D, Ctx)
    assert [e for e in ch.run.events if e.kind == "question"][0].data["params"]["sakoo_can_fix"] is False
