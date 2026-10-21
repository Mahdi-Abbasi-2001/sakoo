import re
from pathlib import Path

from liara_agent.events import EVENT_KINDS

TS = (Path(__file__).parent.parent / "docs" / "events.d.ts").read_text()


def test_every_event_kind_has_a_typescript_type():
    declared = set(re.findall(r'Ev<"([a-z_]+)"', TS))
    assert EVENT_KINDS == declared, f"missing from events.d.ts: {sorted(EVENT_KINDS - declared)}; unknown there: {sorted(declared - EVENT_KINDS)}"


def test_samples_only_use_declared_kinds_and_the_envelope():
    import json
    for f in (Path(__file__).parent.parent / "docs" / "samples").glob("events_*.json"):
        for e in json.loads(f.read_text()):
            assert set(e) == {"id", "ts", "kind", "data"} and e["kind"] in EVENT_KINDS, (f.name, e.get("kind"))


def test_every_payload_field_in_the_samples_is_named_in_the_types():
    import json
    missing = set()
    for f in (Path(__file__).parent.parent / "docs" / "samples").glob("events_*.json"):
        for e in json.loads(f.read_text()):
            for key in e["data"]:
                if not re.search(rf"\b{re.escape(key)}\??:", TS):
                    missing.add((e["kind"], key))
    assert not missing, f"fields in real events but not in docs/events.d.ts: {sorted(missing)}"
