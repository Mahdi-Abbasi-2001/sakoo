"""Cheap, deterministic hints. Output is evidence-bearing *hints*, never final answers."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from liara_agent.plan.model import Evidence

SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", ".next", "vendor", ".agent-state"}   # .agent-state: this agent's own copies of files it patched, never the project
MANIFESTS = {"package.json", "requirements.txt", "pyproject.toml", "composer.json", "go.mod", "Dockerfile", "docker-compose.yml", "docker-compose.yaml", "Procfile", "manage.py", "artisan", "index.html"}

# dependency name -> (kind, value)
NODE_DEPS = {
    "next": ("platform", "next"), "express": ("platform", "node"), "fastify": ("platform", "node"), "koa": ("platform", "node"),
    "react-scripts": ("platform", "react"), "vue": ("platform", "vue"), "react": ("platform", "react"), "@angular/core": ("platform", "angular"),
    "pg": ("db", "postgres"), "postgres": ("db", "postgres"), "mysql2": ("db", "mysql"), "mongoose": ("db", "mongodb"),
    "mongodb": ("db", "mongodb"), "redis": ("db", "redis"), "ioredis": ("db", "redis"), "bullmq": ("db", "redis"),
    "@aws-sdk/client-s3": ("service", "object-storage"), "@aws-sdk/lib-storage": ("service", "object-storage"), "@aws-sdk/s3-request-presigner": ("service", "object-storage"),
    "multer-s3": ("service", "object-storage"), "minio": ("service", "object-storage"),   # S3 clients only: the generic aws-sdk also covers dozens of other AWS services
    "nodemailer": ("service", "mail"), "@nestjs-modules/mailer": ("service", "mail"), "node-cron": ("service", "cron"),
    "openai": ("service", "openai"),
}
PY_DEPS = {
    "django": ("platform", "django"), "flask": ("platform", "flask"), "fastapi": ("platform", "python"),
    "psycopg2": ("db", "postgres"), "psycopg": ("db", "postgres"), "asyncpg": ("db", "postgres"),
    "pymysql": ("db", "mysql"), "mysqlclient": ("db", "mysql"), "pymongo": ("db", "mongodb"),
    "redis": ("db", "redis"), "celery": ("db", "redis"), "boto3": ("service", "object-storage"), "django-storages": ("service", "object-storage"),
    "flask-mail": ("service", "mail"), "fastapi-mail": ("service", "mail"),
    "openai": ("service", "openai"), "langchain-openai": ("service", "openai"),
}
PHP_SERVICE_DEPS = {"league/flysystem-aws-s3-v3": ("service", "object-storage"), "aws/aws-sdk-php": ("service", "object-storage"),
                    "phpmailer/phpmailer": ("service", "mail"), "swiftmailer/swiftmailer": ("service", "mail")}
ENV_RE = [re.compile(p) for p in (
    r"process\.env\.([A-Z][A-Z0-9_]+)", r"process\.env\[['\"]([A-Z][A-Z0-9_]+)['\"]\]",
    r"os\.environ(?:\.get)?\(?\[?['\"]([A-Z][A-Z0-9_]+)['\"]", r"os\.getenv\(['\"]([A-Z][A-Z0-9_]+)['\"]",
    r"\benv\(['\"]([A-Z][A-Z0-9_]+)['\"]",  # PHP and Prisma schemas
    r"os\.Getenv\(\"([A-Z][A-Z0-9_]+)\"\)", r"os\.LookupEnv\(\"([A-Z][A-Z0-9_]+)\"\)",  # Go
    r"\bENV\[['\"]([A-Z][A-Z0-9_]+)['\"]\]",  # Ruby
)]
SCHEME_DB = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql", "mariadb": "mariadb", "mongodb": "mongodb", "mongodb+srv": "mongodb",
             "redis": "redis", "rediss": "redis", "amqp": "rabbitmq"}
LARAVEL_DB = {"pgsql": "postgres", "mysql": "mysql", "mariadb": "mariadb", "sqlsrv": "mssql", "mongodb": "mongodb"}
PRISMA_DB = {"postgresql": "postgres", "mysql": "mysql", "mongodb": "mongodb", "sqlserver": "mssql"}
SECRETISH = re.compile(r"SECRET|TOKEN|PASSWORD|KEY|PRIVATE", re.I)


@dataclass
class Hint:
    kind: str  # platform | db | service
    value: str
    path: str  # project-relative dir the hint applies to
    evidence: Evidence


@dataclass
class Fingerprint:
    root: str
    manifests: dict[str, list[str]] = field(default_factory=dict)  # dir -> manifest names
    hints: list[Hint] = field(default_factory=list)
    env_vars: dict[str, Evidence] = field(default_factory=dict)


def _walk(root: Path, max_depth: int = 3):
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or len(rel.parts) > max_depth + 1:
            continue
        yield p


def fingerprint(root: str | Path) -> Fingerprint:
    root = Path(root).resolve()
    fp = Fingerprint(root=str(root))
    for p in _walk(root):
        if p.is_file() and p.name in MANIFESTS:
            d = str(p.parent.relative_to(root)) or "."
            fp.manifests.setdefault(d, []).append(p.name)
            rel = str(p.relative_to(root))
            text = p.read_text(errors="ignore")
            if p.name == "package.json":
                _node(fp, d, rel, text)
            elif p.name in ("requirements.txt", "pyproject.toml"):
                _python(fp, d, rel, text)
            elif p.name == "composer.json":
                fp.hints.append(Hint("platform", "laravel" if "laravel/framework" in text else "php", d, Evidence(file=rel)))
                for dep, (kind, value) in PHP_SERVICE_DEPS.items():
                    if f'"{dep}"' in text:
                        fp.hints.append(Hint(kind, value, d, Evidence(file=rel, note=dep)))
            elif p.name == "go.mod":
                fp.hints.append(Hint("platform", "go", d, Evidence(file=rel)))
            elif p.name == "manage.py":
                fp.hints.append(Hint("platform", "django", d, Evidence(file=rel, note="manage.py")))
            elif p.name == "Dockerfile":
                fp.hints.append(Hint("platform", "docker", d, Evidence(file=rel)))
        elif p.is_file() and (p.name in EXAMPLE_ENV_FILES or p.suffix == ".prisma") and p.stat().st_size < 100_000:
            _config_file(fp, root, p)
        elif p.is_file() and p.suffix in {".js", ".ts", ".tsx", ".mjs", ".py", ".php", ".go", ".rb"} and p.stat().st_size < 200_000:
            for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                for rx in ENV_RE:
                    for m in rx.finditer(line):
                        fp.env_vars.setdefault(m.group(1), Evidence(file=str(p.relative_to(root)), line=i))
    return fp


EXAMPLE_ENV_FILES = {".env.example", ".env.sample", ".env.template", ".env.dist"}  # never the real .env: its values are secrets


def _config_file(fp, root: Path, p: Path) -> None:
    """Names (never values) from an example env file, and the database type a Prisma schema or Laravel/URL-style setting names."""
    rel, d = str(p.relative_to(root)), str(p.parent.relative_to(root)) or "."
    for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        if p.suffix == ".prisma":
            if m := re.search(r'provider\s*=\s*"(\w+)"', line):
                if (kind := PRISMA_DB.get(m.group(1))):
                    fp.hints.append(Hint("db", kind, d, Evidence(file=rel, line=i, note="prisma datasource")))
            for rx in ENV_RE:
                for m in rx.finditer(line):
                    fp.env_vars.setdefault(m.group(1), Evidence(file=rel, line=i))
            continue
        key, _, value = line.strip().partition("=")
        if not key or key.startswith("#") or "=" not in line:
            continue
        key = key.removeprefix("export ").strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]+", key):
            fp.env_vars.setdefault(key, Evidence(file=rel, line=i))
        value = value.strip().strip("\"'")
        if key == "DB_CONNECTION" and (kind := LARAVEL_DB.get(value)):
            fp.hints.append(Hint("db", kind, d, Evidence(file=rel, line=i, note="DB_CONNECTION")))
        elif (m := re.match(r"([a-z+]+)://", value)) and (kind := SCHEME_DB.get(m.group(1))):
            fp.hints.append(Hint("db", kind, d, Evidence(file=rel, line=i, note=f"{key} scheme")))


def _node(fp, d, rel, text):
    try:
        pkg = json.loads(text)
    except json.JSONDecodeError:
        return
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    for name, (kind, value) in NODE_DEPS.items():
        if name in deps:
            fp.hints.append(Hint(kind, value, d, Evidence(file=rel, note=f"dependency {name}")))


def _python(fp, d, rel, text):
    low = text.lower()
    if not any(h.kind == "platform" and h.path == d and h.value in ("django", "flask", "python") for h in fp.hints):
        fp.hints.append(Hint("platform", "python", d, Evidence(file=rel, note="python project")))
    for name, (kind, value) in PY_DEPS.items():
        if re.search(rf"(^|[\s\"'\[,]){re.escape(name)}([\s=<>~!\"'\],;-]|$)", low, re.M):
            fp.hints.append(Hint(kind, value, d, Evidence(file=rel, note=f"dependency {name}")))
