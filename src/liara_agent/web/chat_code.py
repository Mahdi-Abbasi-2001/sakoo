"""Reading and editing a project's source for the chat's build mode.

Everything is confined to the project's own source folder. Nothing written here is ever executed on this server: the code only travels to Liara, which builds it.
An edit is PLANNED first (what each file would become, as a diff the person reads on a card) and APPLIED only after approval, with the old contents kept so
the last edit can be undone.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from pathlib import Path

from liara_agent.chat.engine import SECRET_PATTERNS, ToolError

SKIP_DIRS = {".git", ".agent-state", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".cache", "vendor"}
SECRET_FILE = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|\.npmrc|\.netrc|credentials(\.json)?)$", re.I)
MAX_FILE_BYTES = 200_000      # one file the chat writes or reads
MAX_CHANGE_BYTES = 800_000    # one proposal
MAX_CHANGE_FILES = 30
READ_CHARS = 5000             # a read returns this much at a time (the engine cuts tool results at 6000 characters)
MAX_LIST = 300
MAX_HITS = 30
# keys in known vendor formats and passwords inside URLs; the engine's looser rule (`password = ...`) would flag ordinary code
CODE_SECRETS = [p for i, p in enumerate(SECRET_PATTERNS) if i != 6]


def has_secret(text: str) -> bool:
    return any(p.search(text) for p in CODE_SECRETS)


def _rel(root: Path, path: str) -> Path:
    """A path inside the project, or a ToolError in words. Absolute paths, `..`, hidden state folders and symlinks are refused."""
    clean = re.sub(r"^(\./)+", "", (path or "").strip().replace("\\", "/"))
    if not clean or clean.startswith("/") or ".." in Path(clean).parts or "\x00" in clean:
        raise ToolError(f"{path!r} is not a path inside the project")
    parts = Path(clean).parts
    if parts[0] in SKIP_DIRS or any(p in (".git", ".agent-state") for p in parts):
        raise ToolError(f"{path!r} is not part of the project's code")
    if SECRET_FILE.search(clean):
        raise ToolError(f"{path!r} may hold secrets, so it is never read or written here (use environment variables)")
    target = (root / clean)
    cur = root
    for part in parts:                       # no step of the way may be a symlink
        cur = cur / part
        if cur.is_symlink():
            raise ToolError(f"{path!r} goes through a link, which is not allowed")
    if root.resolve() not in target.resolve().parents:
        raise ToolError(f"{path!r} is not a path inside the project")
    return target


def list_files(root: Path, under: str = "") -> dict:
    base = root if not under else _rel(root, under)
    out: list[str] = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(dirpath) / d).is_symlink())
        for f in sorted(files):
            p = Path(dirpath) / f
            rel = p.relative_to(root).as_posix()
            if p.is_symlink() or SECRET_FILE.search(rel):
                continue
            out.append(rel)
            if len(out) >= MAX_LIST:
                return {"files": out, "note": f"only the first {MAX_LIST} files are listed: pass `under` to look inside a folder"}
    return {"files": out}


def read_file(root: Path, path: str, start: int = 1) -> dict:
    target = _rel(root, path)
    if not target.is_file():
        raise ToolError(f"{path!r} does not exist")
    if target.stat().st_size > 2_000_000:
        raise ToolError(f"{path!r} is too large to read here")
    raw = target.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolError(f"{path!r} is not a text file")
    lines = raw.decode("utf-8", "replace").splitlines()
    start = max(1, int(start or 1))
    shown, size = [], 0
    for i in range(start - 1, len(lines)):
        if size + len(lines[i]) + 1 > READ_CHARS and shown:
            break
        shown.append(lines[i])
        size += len(lines[i]) + 1
    end = start - 1 + len(shown)
    return {"path": path, "total_lines": len(lines), "from_line": start, "to_line": end, "text": "\n".join(shown),
            **({"next_start": end + 1} if end < len(lines) else {})}


def search(root: Path, query: str) -> dict:
    q = (query or "").strip()
    if len(q) < 2:
        raise ToolError("search for at least two characters")
    hits: list[dict] = []
    for rel in list_files(root)["files"]:
        p = root / rel
        try:
            if p.stat().st_size > 400_000:
                continue
            for n, line in enumerate(p.read_text("utf-8", "ignore").splitlines(), 1):
                if q.lower() in line.lower():
                    hits.append({"path": rel, "line": n, "text": line.strip()[:160]})
                    if len(hits) >= MAX_HITS:
                        return {"hits": hits, "note": "only the first matches are shown"}
        except OSError:
            continue
    return {"hits": hits}


# -- planning and applying an edit --------------------------------------------------------------------------------------------------
def plan_changes(root: Path, changes: list[dict]) -> dict:
    """What each file would become. A change is {path, content} (write the whole file) or {path, replace: [{find, with}]} (exact, unique text) or {path, delete: true}.
    Returns {files: [{path, status, added, removed}], diff, new: {path: text | None}} and writes nothing."""
    if not isinstance(changes, list) or not changes:
        raise ToolError("give at least one file change")
    if len(changes) > MAX_CHANGE_FILES:
        raise ToolError(f"at most {MAX_CHANGE_FILES} files per proposal: split the work")
    new: dict[str, str | None] = {}
    files, diffs, total = [], [], 0
    for ch in changes:
        if not isinstance(ch, dict):
            raise ToolError("each change must be an object with a path")
        path = str(ch.get("path") or "")
        target = _rel(root, path)
        rel = target.relative_to(root).as_posix()
        if rel in new:
            raise ToolError(f"{rel} appears twice: put all its changes in one entry")
        old = target.read_text("utf-8", "replace") if target.is_file() else None
        if ch.get("delete"):
            if old is None:
                raise ToolError(f"{rel} does not exist, so it cannot be deleted")
            text = None
        elif "content" in ch and ch["content"] is not None:
            text = str(ch["content"])
        elif ch.get("replace"):
            if old is None:
                raise ToolError(f"{rel} does not exist: write it with `content`")
            text = old
            for r in ch["replace"]:
                find, rep = str((r or {}).get("find", "")), str((r or {}).get("with", ""))
                n = text.count(find) if find else 0
                if n != 1:
                    raise ToolError(f"in {rel} the text to replace {'was not found' if n == 0 else 'appears ' + str(n) + ' times: include more around it'}: {find[:80]!r}")
                text = text.replace(find, rep, 1)
        else:
            raise ToolError(f"{rel}: give `content`, `replace` or `delete`")
        if text is not None:
            if len(text.encode()) > MAX_FILE_BYTES:
                raise ToolError(f"{rel} would be larger than {MAX_FILE_BYTES // 1000} KB")
            if has_secret(text) and (old is None or not has_secret(old)):
                raise ToolError(f"{rel} contains something that looks like a secret (a key, token or password): read it from an environment variable instead")
            total += len(text.encode())
        if total > MAX_CHANGE_BYTES:
            raise ToolError("this proposal is too large: split it")
        if text == old:
            continue
        new[rel] = text
        a, b = (old or "").splitlines(), (text or "").splitlines()
        d = list(difflib.unified_diff(a, b, f"a/{rel}" if old is not None else "/dev/null", f"b/{rel}" if text is not None else "/dev/null", lineterm="", n=2))
        diffs.append("\n".join(d))
        files.append({"path": rel, "status": "new" if old is None else "deleted" if text is None else "changed",
                      "added": sum(1 for x in d if x.startswith("+") and not x.startswith("+++")), "removed": sum(1 for x in d if x.startswith("-") and not x.startswith("---"))})
    if not new:
        raise ToolError("those changes would not change anything")
    return {"files": files, "diff": "\n".join(diffs)[:30_000], "new": new}


def apply(root: Path, plan: dict, backup: Path) -> list[str]:
    """Write a plan, keeping what was there so `undo` can put it back."""
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)
    manifest: dict[str, bool] = {}   # path -> existed before
    for rel, text in plan["new"].items():
        target = _rel(root, rel)
        existed = target.is_file()
        manifest[rel] = existed
        if existed:
            dest = backup / "files" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, dest)
    (backup / "manifest.json").write_text(json.dumps(manifest))
    for rel, text in plan["new"].items():
        target = _rel(root, rel)
        if text is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, "utf-8")
    return sorted(plan["new"])


def undo(root: Path, backup: Path) -> list[str]:
    if not (backup / "manifest.json").is_file():
        raise ToolError("there is no edit to undo")
    manifest = json.loads((backup / "manifest.json").read_text())
    for rel, existed in manifest.items():
        target = _rel(root, rel)
        if existed:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup / "files" / rel, target)
        else:
            target.unlink(missing_ok=True)
    shutil.rmtree(backup)
    return sorted(manifest)
