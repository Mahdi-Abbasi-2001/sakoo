"""Pre-deploy readiness check: things a first-time deployer would not know to look for. Deterministic, no network.

Findings carry a stable code and parameters (translated by the UI) and a file:line. They NEVER contain the secret itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from liara_agent.detect.fingerprints import SKIP_DIRS

CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".php", ".go", ".rb", ".java", ".cs", ".env", ".yml", ".yaml", ".json", ".toml", ".ini", ".cfg"}
NOT_CODE_FOR_SECRETS = {"test", "tests", "__tests__", "spec", "fixtures", "examples", "example", "docs", "mocks"}
MAX_PER_CODE = 5

SECRET_PATTERNS = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("api_key", re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{30,})")),
    ("assigned_secret", re.compile(r"""(?ix)(?<![a-z0-9])(?:password|passwd|secret(?:_key)?|api[_-]?key|token|private[_-]?key)\w*\s*[=:]\s*['"][^'"\s$]{10,}['"]""")),
]
PLACEHOLDER = re.compile(r"(?i)(changeme|change_me|your[_-]|example|placeholder|xxxx|<.*>|dummy|test|todo|secret_here|\*{3,})")

CHECKS: list[tuple[str, str, re.Pattern, dict]] = [
    ("debug_enabled", "warning", re.compile(r"^\s*DEBUG\s*=\s*True\b|\bapp\.run\([^)]*debug\s*=\s*True|\bdebug\s*:\s*true\b", re.I), {}),
    ("allowed_hosts_wildcard", "info", re.compile(r"ALLOWED_HOSTS\s*=\s*\[\s*['\"]\*['\"]"), {}),
    ("sqlite_data_loss", "warning", re.compile(r"sqlite3?[:/.]|django\.db\.backends\.sqlite3|better-sqlite3|require\(['\"]sqlite3['\"]\)|import sqlite3"), {}),
    ("binds_localhost", "warning", re.compile(r"""(?:listen\s*\([^)]*['"](?:127\.0\.0\.1|localhost)['"]|host\s*[=:]\s*['"](?:127\.0\.0\.1|localhost)['"]\s*[,)])"""), {}),
    ("writes_local_files", "info", re.compile(r"multer\s*\(\s*\{[^}]*dest|os\.makedirs\([^)]*upload|mkdirSync\([^)]*upload|MEDIA_ROOT\s*=|UPLOAD_FOLDER\s*=", re.I), {}),
]
FOREIGN = [
    ("telegram", re.compile(r"api\.telegram\.org|python-telegram-bot|\btelegraf\b|node-telegram-bot-api|\baiogram\b|from telegram\b|import telegram\b", re.I)),
    ("openai", re.compile(r"api\.openai\.com|require\(['\"]openai['\"]\)|from ['\"]openai['\"]|^\s*(?:import|from) openai\b|\"openai\"\s*:", re.I | re.M)),
    ("anthropic", re.compile(r"api\.anthropic\.com|@anthropic-ai/sdk|^\s*(?:import|from) anthropic\b", re.I | re.M)),
    ("google_ai", re.compile(r"generativelanguage\.googleapis\.com|@google/generative-ai|google\.generativeai", re.I)),
    ("discord", re.compile(r"discord\.com/api|discord\.js|discord\.py", re.I)),
]


@dataclass
class Finding:
    code: str
    level: str  # info | warning
    file: str
    line: int | None = None
    params: dict = field(default_factory=dict)


@dataclass
class Readiness:
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict:
        counts: dict[str, int] = {}
        for f in self.findings:
            counts[f.level] = counts.get(f.level, 0) + 1
        return {"counts": counts, "findings": [{"code": f.code, "level": f.level, "file": f.file, "line": f.line, "params": f.params} for f in self.findings]}


def _files(root: Path):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or p.is_symlink() or any(part in SKIP_DIRS for part in rel.parts) or p.stat().st_size > 300_000:
            continue
        if p.suffix.lower() in CODE_SUFFIXES or p.name.startswith(".env") or p.name in ("Dockerfile", "Procfile"):
            yield p, rel


def check_readiness(root: str | Path) -> Readiness:
    root = Path(root)
    out, seen = Readiness(), {}

    def add(code: str, level: str, file: str, line: int | None, **params) -> None:
        key = (code, params.get("service") or params.get("kind"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] <= MAX_PER_CODE:
            out.findings.append(Finding(code, level, file, line, params))

    for p, rel in _files(root):
        rels = rel.as_posix()
        in_tests = bool(set(rel.parts[:-1]) & NOT_CODE_FOR_SECRETS)
        if p.name.startswith(".env") and p.name not in (".env.example", ".env.sample", ".env.template", ".env.dist"):
            try:
                names = [ln.split("=", 1)[0].strip() for ln in p.read_text(errors="ignore").splitlines() if "=" in ln and not ln.lstrip().startswith("#") and ln.split("=", 1)[1].strip()]
            except OSError:
                names = []
            if names:
                add("env_file_present", "warning", rels, None, count=len(names))  # values deliberately not read into the finding
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        is_manifest = p.name in ("package.json", "requirements.txt", "pyproject.toml", "composer.json")
        for i, line in enumerate(lines, 1):
            if not in_tests and p.suffix.lower() not in (".md",) and not is_manifest:
                for name, rx in SECRET_PATTERNS:
                    if rx.search(line) and not PLACEHOLDER.search(line):
                        add("hardcoded_secret", "warning", rels, i, kind=name)
                        break
            if is_manifest or in_tests:
                pass
            else:
                for code, level, rx, params in CHECKS:
                    if rx.search(line):
                        add(code, level, rels, i, **params)
            for service, rx in FOREIGN:
                if rx.search(line) and not in_tests:
                    add("foreign_service_dependency", "warning", rels, i, service=service)
        # manifests: dependency on foreign-service SDKs
        if is_manifest:
            text = "\n".join(lines)
            for service, rx in FOREIGN:
                if rx.search(text):
                    add("foreign_service_dependency", "warning", rels, None, service=service)
    # one finding per (code, service) location pair is enough to warn; keep the list short and stable
    out.findings.sort(key=lambda f: (f.level != "warning", f.code, f.file, f.line or 0))
    return out
