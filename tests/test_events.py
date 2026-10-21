import json
import threading
import time

import pytest

from liara_agent.events import Run, scrub
from liara_agent.interact import QueueChannel, ScriptedChannel


def test_events_get_increasing_ids_and_can_be_replayed_from_any_point():
    run = Run()
    for i in range(5):
        run.emit("build", {"line": f"l{i}"})
    assert [e.id for e in run.events] == [1, 2, 3, 4, 5]
    assert [e.data["line"] for e in run.since(3)] == ["l3", "l4"]


def test_unknown_event_kinds_are_rejected():
    with pytest.raises(ValueError):
        Run().emit("made_up", {})


def test_log_is_persisted_and_replayable(tmp_path):
    run = Run(log_path=tmp_path / "r1.jsonl")
    run.emit("phase_started", {"phase": "analyze"})
    run.emit("phase_finished", {"phase": "analyze"})
    again = Run.replay(tmp_path / "r1.jsonl")
    assert [e.kind for e in again.events] == ["phase_started", "phase_finished"] and again.events[0].data == {"phase": "analyze"}


def test_scrub_masks_secret_keys_urls_and_assignments_but_keeps_references():
    data = {"env": {"JWT_SECRET": "abc123456789", "PORT": "3000", "DATABASE_URL": "postgres://app:hunter22@db:5432/app",
                    "PASS": "$DB_main_PASSWORD"},
            "line": "export API_KEY=sk-live-abcdefghij1234", "secret": True, "name": "JWT_SECRET"}
    out = scrub(data)
    assert out["env"]["JWT_SECRET"] == "***" and out["env"]["PORT"] == "3000"
    assert out["env"]["DATABASE_URL"] == "postgres://app:***@db:5432/app"
    assert out["env"]["PASS"] == "$DB_main_PASSWORD"  # a reference to a value, not the value
    assert "sk-live" not in out["line"] and out["secret"] is True and out["name"] == "JWT_SECRET"


def test_a_registered_secret_is_masked_even_when_a_log_line_echoes_it():
    run = Run()
    run.register_secret("my-very-secret-value")
    run.emit("build", {"line": "Error: connection failed using my-very-secret-value on host"})
    assert "my-very-secret-value" not in json.dumps([e.as_dict() for e in run.events])


def test_subscribers_see_scrubbed_events():
    seen = []
    run = Run()
    run.subscribe(lambda e: seen.append(e))
    run.emit("warning", {"message": "x", "env": {"DB_PASSWORD": "pw123456"}})
    assert seen[0].data["env"]["DB_PASSWORD"] == "***"


def test_queue_channel_blocks_until_answered_and_never_logs_secret_values():
    run = Run()
    ch = QueueChannel(run, timeout=5)
    result = {}

    def worker():
        result["ok"] = ch.confirm("Apply?", price_delta_hourly=306)
        result["secret"] = ch.secret("JWT_SECRET")

    t = threading.Thread(target=worker)
    t.start()

    def wait_for_new_request(not_ids=()):
        for _ in range(200):
            fresh = [i for i in ch.pending_ids() if i not in not_ids]
            if fresh:
                return fresh[0]
            time.sleep(0.02)
        raise AssertionError("no new request appeared")

    first = wait_for_new_request()
    assert ch.respond(first, True)
    second = wait_for_new_request(not_ids=(first,))
    ch.respond(second, "super-secret-typed-value")
    t.join(5)
    assert result == {"ok": True, "secret": "super-secret-typed-value"}
    kinds = [e.kind for e in run.events]
    assert kinds == ["confirm_request", "confirm_response", "secret_request", "secret_provided"]
    assert "super-secret-typed-value" not in json.dumps([e.as_dict() for e in run.events])
    assert run.events[0].data["price_delta_hourly"] == 306


def test_timeouts_decline_safely():
    run = Run()
    ch = QueueChannel(run, timeout=0.05)
    assert ch.confirm("Spend money?") is False and ch.secret("X") is None
    assert "conservative" in ch.question("which db?")
    assert [e.kind for e in run.events].count("request_expired") == 3


def test_responding_to_an_unknown_request_is_ignored():
    assert QueueChannel(Run()).respond("nope", True) is False


def test_scripted_channel_emits_the_same_events():
    run = Run()
    ch = ScriptedChannel(run, confirm=False, answers=["mysql"], secrets={"A": "valuevalue"})
    assert ch.confirm("p") is False and ch.question("q") == "mysql" and ch.secret("A") == "valuevalue" and ch.secret("B") is None
    assert [e.kind for e in run.events] == ["confirm_request", "confirm_response", "question", "answered", "secret_request", "secret_provided",
                                            "secret_request", "secret_provided"]


def test_payload_fields_named_id_or_kind_never_overwrite_the_envelope():
    run = Run()
    run.emit("build", {"line": "x"})
    ev = run.emit("confirm_request", {"id": "req-7", "kind": "plan", "prompt": "p"})
    d = ev.as_dict()
    assert d["id"] == 2 and d["kind"] == "confirm_request" and d["data"]["id"] == "req-7" and d["data"]["kind"] == "plan"


def test_callback_channel_records_prompts_and_masks_typed_secrets():
    from liara_agent.interact import CallbackChannel

    run = Run()
    ch = CallbackChannel(run, lambda prompt, kind: kind == "plan", lambda t, o: "mysql", lambda n: "typed-secret-value-9")
    assert ch.confirm("create?", price_delta_hourly=1146, kind="plan") is True and ch.confirm("fix?") is False
    assert ch.question("which db?") == "mysql" and ch.secret("JWT_SECRET") == "typed-secret-value-9"
    run.emit("build", {"line": "boot with typed-secret-value-9"})
    assert [e.kind for e in run.events][:4] == ["confirm_request", "confirm_response", "confirm_request", "confirm_response"]
    assert "typed-secret-value-9" not in json.dumps([e.as_dict() for e in run.events])
    assert run.events[1].data["accepted"] is True and run.events[3].data["accepted"] is False


def test_options_are_enforced_and_free_text_questions_accept_anything():
    run = Run()
    ch = QueueChannel(run, timeout=5)
    box = {}
    t = threading.Thread(target=lambda: box.update(a=ch.question("worker?", ["worker", "needs_port"], code="worker_without_port")))
    t.start()
    for _ in range(100):
        if ch.pending_ids():
            break
        time.sleep(0.02)
    rid = ch.pending_ids()[0]
    assert ch.check(rid, "Yes, it is a background worker") == "bad_option" and ch.respond(rid, "Yes, it is a background worker") is False
    assert ch.check("nope", "x") == "unknown_request"
    assert ch.respond(rid, "worker") is True
    t.join(5)
    assert box["a"] == "worker" and run.events[-1].data == {"id": rid, "value": "worker"}
    t2 = threading.Thread(target=lambda: box.update(b=ch.question("which db?")))
    t2.start()
    for _ in range(100):
        if ch.pending_ids():
            break
        time.sleep(0.02)
    assert ch.respond(ch.pending_ids()[0], "anything at all")  # no options: free text
    t2.join(5)


def test_web_requests_carry_a_server_side_expiry_and_cli_requests_do_not():
    import time as _t
    run = Run()
    ch = QueueChannel(run, timeout=60)
    t = threading.Thread(target=lambda: ch.confirm("ok?"), daemon=True)
    t.start()
    for _ in range(100):
        if ch.pending_ids():
            break
        _t.sleep(0.02)
    exp = run.events[0].data["expires_at"]
    assert abs(exp - (_t.time() + 60)) < 5
    ch.respond(ch.pending_ids()[0], True)
    t.join(5)
    assert "expires_at" not in ScriptedChannel(Run()).__dict__  # scripted/CLI channels do not invent an expiry


def test_a_cancelled_run_stops_at_its_next_event_but_can_still_say_it_finished(tmp_path):
    import pytest
    from liara_agent.events import Run, RunCancelled
    run = Run(log_path=tmp_path / "r.jsonl")
    run.emit("phase_started", {"phase": "plan"})
    run.cancel()
    with pytest.raises(RunCancelled):
        run.emit("phase_started", {"phase": "deploy"})
    run._cancelled = False
    run.emit("run_finished", {"ok": False})
    assert [e.kind for e in run.events] == ["phase_started", "run_finished"]


def test_cancelling_wakes_a_question_that_nobody_will_answer(tmp_path):
    import threading
    import pytest
    from liara_agent.events import Run, RunCancelled
    from liara_agent.interact import QueueChannel
    run = Run()
    ch = QueueChannel(run, timeout=30)
    seen = []

    def worker():
        try:
            ch.confirm("create?", kind="plan")
        except RunCancelled:
            seen.append("stopped")
    t = threading.Thread(target=worker)
    t.start()
    while not ch.pending_ids():
        pass
    run.cancel()
    ch.wake_all()
    t.join(5)
    assert seen == ["stopped"] and not t.is_alive()
