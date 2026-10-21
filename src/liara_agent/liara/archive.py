"""Build the source archive we upload. Mirrors the Liara CLI's default ignores and never ships .env files."""
from __future__ import annotations

import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pathspec

DEFAULT_IGNORES = [".git", ".idea", ".vscode", ".next", ".dockerignore", "*.*~", "liara.json", "node_modules",
                   "bower_components", ".DS_Store", "venv", ".venv", "__pycache__", "*.py[cod]", ".agent-state"]
# Secrets stay out of the image: the agent sets them as environment variables instead.
SECRET_FILES = [".env", ".env.*", "!.env.example", "!.env.sample", "!.env.template", "*.pem", "*.key", "id_rsa*"]


@dataclass
class Archive:
    path: Path
    size: int
    files: int
    excluded_secrets: list[str]


def build_archive(project: str | Path, *, max_mb: float | None = None, dest_dir: str | Path | None = None) -> Archive:
    root = Path(project).resolve()
    lines = list(DEFAULT_IGNORES)
    for name in (".gitignore", ".liaraignore"):
        f = root / name
        if f.is_file():
            lines += f.read_text(errors="ignore").splitlines()
    ignore = pathspec.PathSpec.from_lines("gitignore", lines)
    secrets = pathspec.PathSpec.from_lines("gitignore", SECRET_FILES)
    out = Path(tempfile.mkstemp(suffix=".tar.gz", dir=dest_dir)[1])
    count, excluded = 0, []
    with tarfile.open(out, "w:gz") as tar:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if p.is_dir() or p.is_symlink() or ignore.match_file(rel + ("/" if p.is_dir() else "")) or any(
                    ignore.match_file("/".join(p.relative_to(root).parts[:i]) + "/") for i in range(1, len(p.relative_to(root).parts))):
                continue
            if secrets.match_file(rel):
                excluded.append(rel)
                continue
            tar.add(p, arcname=rel, recursive=False)
            count += 1
    size = out.stat().st_size
    if max_mb is not None and size > max_mb * 1024 * 1024:
        out.unlink()
        raise ValueError(f"source is {size / 1048576:.0f} MB compressed; this plan's limit is {max_mb:g} MB (add a .gitignore or upgrade the tier)")
    return Archive(out, size, count, excluded)
