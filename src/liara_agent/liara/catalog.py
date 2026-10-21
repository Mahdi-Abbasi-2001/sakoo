"""Liara capabilities. Platform data is generated from the installed Liara CLI (scripts/gen_catalog.py);
database data comes from the Liara API at runtime, with an unverified offline fallback."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

_DIR = Path(__file__).parent


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((_DIR / "catalog.generated.json").read_text())


_live_databases: set[str] | None = None


def set_live_databases(types: list[str]) -> None:
    global _live_databases
    _live_databases = set(types)


def platforms() -> set[str]:
    return set(catalog()["platforms"])


def databases() -> set[str]:
    if _live_databases is not None:
        return _live_databases
    return set(yaml.safe_load((_DIR / "fallback.yaml").read_text())["databases"])


def default_port(platform: str) -> int:
    return catalog()["platforms"][platform]["port"]
