"""Apply code patches to a COPY of the project. The developer's own files are never edited in place."""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

IGNORE = shutil.ignore_patterns("node_modules", ".git", ".venv", "venv", "__pycache__", ".agent-state", ".next")


def patched_copy(project: Path, state_dir: Path, app: str) -> Path:
    dest = state_dir / "patched" / app
    if not dest.exists():
        shutil.copytree(project, dest, ignore=IGNORE)
    return dest


def apply_append(copy_root: Path, file: str, line: str) -> str | None:
    """Append a line to a text file in the copy; returns a unified diff (None if it was already there)."""
    p = copy_root / file
    before = p.read_text() if p.exists() else ""
    if any(ln.strip().split("==")[0].lower() == line.strip().lower() for ln in before.splitlines()):
        return None
    after = before + ("" if before.endswith("\n") or not before else "\n") + line + "\n"
    p.write_text(after)
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), f"a/{file}", f"b/{file}"))


def apply_engines_node(copy_root: Path, value: str) -> str | None:
    """Set package.json engines.node in the copy; returns a unified diff (None if it already has that value)."""
    p = copy_root / "package.json"
    before = p.read_text()
    data = json.loads(before)
    if (data.get("engines") or {}).get("node") == value:
        return None
    data.setdefault("engines", {})["node"] = value
    after = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    p.write_text(after)
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), "a/package.json", "b/package.json"))


def save_patches(state_dir: Path, diffs: list[str]) -> Path | None:
    if not diffs:
        return None
    out = state_dir / "fixes.patch"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(diffs))
    return out
