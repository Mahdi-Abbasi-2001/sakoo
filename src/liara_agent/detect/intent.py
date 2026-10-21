"""Layer 2: read what the developer already wrote down (compose, Dockerfile, Procfile, .env.example)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

COMPOSE_NAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


@dataclass
class ComposeService:
    name: str
    image: str | None = None
    build_context: str | None = None
    dockerfile: str | None = None
    ports: list[int] = field(default_factory=list)  # container-side ports (published or exposed)
    env: dict[str, str] = field(default_factory=dict)
    volumes: list[tuple[str, str]] = field(default_factory=list)  # (source, container path); source may be a name or a host path
    depends_on: list[str] = field(default_factory=list)
    command: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)  # privileged, host_network, profiles ...


@dataclass
class ComposeProject:
    file: str
    services: dict[str, ComposeService]


def find_compose(root: str | Path) -> Path | None:
    root = Path(root)
    return next((root / n for n in COMPOSE_NAMES if (root / n).is_file()), None)


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?-([^}]*))?\}")


def _interpolate(value: str) -> str:
    """Resolve ${VAR:-default} to its default; leave variables without a default for the caller to ask about."""
    return _VAR.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(0), value)


def _env(raw) -> dict[str, str]:
    if isinstance(raw, dict):
        return {str(k): "" if v is None else _interpolate(str(v)) for k, v in raw.items()}
    out = {}
    for item in raw or []:
        k, _, v = str(item).partition("=")
        out[k] = _interpolate(v)
    return out


def _port(spec) -> int | None:
    if isinstance(spec, dict):
        return int(spec["target"]) if spec.get("target") else None
    m = re.match(r"^(?:[\d.:]*?)(?:(\d+)(?:-\d+)?:)?(\d+)(?:-\d+)?(?:/\w+)?$", str(spec).strip())
    return int(m.group(2)) if m else None


def _volume(spec) -> tuple[str, str] | None:
    if isinstance(spec, dict):
        return (spec.get("source") or "", spec.get("target") or "") if spec.get("target") else None
    parts = str(spec).split(":")
    return (parts[0], parts[1]) if len(parts) >= 2 else ("", parts[0])


def parse_compose(path: str | Path) -> ComposeProject:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    services: dict[str, ComposeService] = {}
    for name, d in (data.get("services") or {}).items():
        d = d or {}
        build = d.get("build")
        svc = ComposeService(
            name=name,
            image=d.get("image"),
            build_context=(build if isinstance(build, str) else (build or {}).get("context", ".")) if build else None,
            dockerfile=(build or {}).get("dockerfile") if isinstance(build, dict) else None,
            env=_env(d.get("environment")),
            depends_on=list(d["depends_on"]) if isinstance(d.get("depends_on"), (list, dict)) else [],
        )
        for p in (d.get("ports") or []) + (d.get("expose") or []):
            if (port := _port(p)) and port not in svc.ports:
                svc.ports.append(port)
        svc.volumes = [v for raw in (d.get("volumes") or []) if (v := _volume(raw))]
        cmd = d.get("command")
        svc.command = cmd if isinstance(cmd, list) else (cmd.split() if isinstance(cmd, str) else [])
        if d.get("privileged"):
            svc.flags.add("privileged")
        if d.get("network_mode") == "host":
            svc.flags.add("host_network")
        if d.get("profiles"):
            svc.flags.add("profiles")
        # env_file entries are read too, so DATABASE_URL etc. defined there are not missed
        for ef in [d["env_file"]] if isinstance(d.get("env_file"), str) else (d.get("env_file") or []):
            f = path.parent / (ef if isinstance(ef, str) else ef.get("path", ""))
            if f.is_file():
                svc.env = {**parse_env_file(f), **svc.env}
        services[name] = svc
    return ComposeProject(file=path.name, services=services)


def parse_env_file(path: str | Path) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.removeprefix("export ").strip()] = v.strip().strip("\"'")
    return out


def dockerfile_info(path: str | Path) -> dict:
    """EXPOSEd ports, final CMD/ENTRYPOINT and base images of a Dockerfile."""
    info = {"expose": [], "cmd": None, "entrypoint": None, "from": []}
    for raw in Path(path).read_text(errors="ignore").splitlines():
        line = raw.strip()
        if m := re.match(r"(?i)^EXPOSE\s+(.+)$", line):
            info["expose"] += [int(p.split("/")[0]) for p in m.group(1).split() if p.split("/")[0].isdigit()]
        elif m := re.match(r"(?i)^(CMD|ENTRYPOINT)\s+(.+)$", line):
            info[m.group(1).lower()] = m.group(2)
        elif m := re.match(r"(?i)^FROM\s+(\S+)", line):
            info["from"].append(m.group(1))
    return info


def procfile(path: str | Path) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(errors="ignore").splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out
