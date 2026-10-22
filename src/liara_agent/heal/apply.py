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


def npm_latest(package: str, get=None) -> str:
    """The current version of an npm package from the public registry. LookupError (in words) when the registry cannot say."""
    import httpx

    get = get or (lambda url: httpx.get(url, timeout=6, follow_redirects=True))
    try:
        r = get(f"https://registry.npmjs.org/{package.replace('/', '%2F')}/latest")
        if r.status_code == 404:
            raise LookupError(f"{package} does not exist on the npm registry (a typo, or a private package)")
        r.raise_for_status()
        version = r.json().get("version")
    except LookupError:
        raise
    except Exception as e:
        raise LookupError(f"could not look up {package} on the npm registry ({type(e).__name__})") from e
    if not isinstance(version, str) or not version:
        raise LookupError(f"the npm registry gave no version for {package}")
    return version


def apply_npm_dependency(copy_root: Path, package: str, version: str | None = None, lookup=npm_latest) -> str | None:
    """Declare `package` in package.json's dependencies (in the copy) with a caret range of its current version; returns a unified diff, None if already declared."""
    p = copy_root / "package.json"
    before = p.read_text()
    data = json.loads(before)
    if any(package in (data.get(k) or {}) for k in ("dependencies", "devDependencies", "optionalDependencies")):
        return None
    data.setdefault("dependencies", {})[package] = "^" + (version or lookup(package))
    data["dependencies"] = dict(sorted(data["dependencies"].items()))
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
