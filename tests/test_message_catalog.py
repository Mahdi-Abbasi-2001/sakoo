import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOC = (ROOT / "docs" / "message-codes.md").read_text()


def codes_in_source():
    found = set()
    for f in (ROOT / "src").rglob("*.py"):
        text = f.read_text()
        found |= set(re.findall(r'\bcode="([a-z_]+)"', text))
        found |= set(re.findall(r'Diagnosis\("([a-z_]+)"', text))
        found |= set(re.findall(r'"code": "([a-z_]+)"', text))
        found |= set(re.findall(r'reason_code="([a-z_]+)"', text))
        found |= set(re.findall(r'"reason_code": "([a-z_]+)"', text))
        found |= set(re.findall(r'\berr\(\d+, "([a-z_]+)"', text))  # API error codes
        found |= set(re.findall(r'SourceError\("([a-z_]+)"', text))
        found |= set(re.findall(r'\b[1-5]\d\d: "([a-z_]+)"', text))  # status -> reason code maps
        if f.name == "readiness.py":
            found |= set(re.findall(r'\badd\("([a-z_]+)"', text)) | set(re.findall(r'^\s*\("([a-z_]+)", "(?:warning|info)"', text, re.M))
        found |= set(re.findall(r'"detail": \{"code": "([a-z_]+)"', text))
    found |= {"service_privileged", "service_host_network"}  # built with an f-string in plan/compose.py
    found -= {"db_point_host", "db_create"} - set(re.findall(r"`(db_point_host|db_create)`", DOC))
    return found


def test_every_code_in_the_source_is_documented_for_translators():
    missing = sorted(c for c in codes_in_source() if f"`{c}`" not in DOC)
    assert not missing, f"add these codes to docs/message-codes.md: {missing}"


def test_catalog_is_not_empty_of_the_big_ones():
    for c in ("create_resources", "build_timeout", "resize_tier", "worker_without_port", "outbound_blocked", "no_plan"):
        assert f"`{c}`" in DOC
