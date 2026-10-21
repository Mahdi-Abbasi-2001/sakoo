"""Which runtime version should a project be built with? Mirrors what the Liara CLI sends as `platformConfig`, and goes one step further:
a project that pins a version Liara no longer offers is moved to the nearest one that is offered, instead of being left to fail.

Sources (first match wins): node/next package.json engines.node, then .nvmrc; python/django/flask .python-version, runtime.txt, Pipfile, pyproject.toml;
php/laravel composer.json; go go.mod; dotnet *.csproj. When Liara's default runtime already satisfies what the project asks for, nothing is sent.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from liara_agent.liara import catalog as liara_catalog

Ver = tuple[int, int, int]
CONFIG_KEY = {"django": "pythonVersion", "flask": "pythonVersion", "laravel": "phpVersion"}  # every other platform uses "version"
NODE_LIKE, PYTHON_LIKE, PHP_LIKE = {"node", "next"}, {"python", "django", "flask"}, {"php", "laravel"}


@dataclass
class Detected:
    config: dict  # goes into the release as platformConfig
    version: str
    source: str  # the file and field the version came from
    wanted: str  # what the file asked for, verbatim


def _tup(s: str) -> Ver:
    p = [int(x) if x.isdigit() else 0 for x in re.split(r"[.]", s.strip())[:3]]
    a, b, c = (p + [0, 0, 0])[:3]
    return (a, b, c)


def _family(v: str) -> tuple[Ver, Ver]:
    """[lower, upper) of a catalog version: "16" covers 16.x.y, "3.12" covers 3.12.x."""
    n = len(v.split("."))
    lo = _tup(v)
    hi = (lo[0] + 1, 0, 0) if n == 1 else (lo[0], lo[1] + 1, 0)
    return lo, hi


def _interval(tok: str, composer: bool) -> tuple[Ver, Ver | None] | None:
    """One comparator as [lo, hi). hi None = unbounded."""
    tok = tok.strip().lstrip("v")
    if not tok or tok in ("*", "x", "latest"):
        return (0, 0, 0), None
    m = re.match(r"^(\^|~=|~|>=|<=|==|=|>|<)?\s*v?(\d+(?:\.(?:\d+|x|\*))?(?:\.(?:\d+|x|\*))?)", tok)
    if not m:
        return None
    op, ver = m.group(1) or "", m.group(2)
    parts = ver.split(".")
    n = len([p for p in parts if p.isdigit()])
    lo = _tup(".".join(p if p.isdigit() else "0" for p in parts))
    wild = any(p in ("x", "*") for p in parts)
    if op in (">=",):
        return lo, None
    if op == ">":
        return (lo[0], lo[1], lo[2] + 1), None
    if op in ("<", "<="):
        hi = lo if op == "<" else (lo[0], lo[1], lo[2] + 1)
        return (0, 0, 0), hi
    if op == "^":
        hi = (lo[0] + 1, 0, 0) if lo[0] else (0, lo[1] + 1, 0)
        return lo, hi
    if op in ("~", "~="):
        keep = 1 if (op == "~=" or composer) and n <= 2 else 2
        if op == "~=" and n >= 3:
            keep = 2
        hi = (lo[0] + 1, 0, 0) if keep == 1 else (lo[0], lo[1] + 1, 0)
        return lo, hi
    if wild or n == 1:
        hi = (lo[0] + 1, 0, 0) if n <= 1 else (lo[0], lo[1] + 1, 0)
        return lo, hi
    if n == 2:  # "3.9": any 3.9.x
        return lo, (lo[0], lo[1] + 1, 0)
    return lo, (lo[0], lo[1], lo[2] + 1)  # an exact x.y.z


def _ranges(spec: str, composer: bool) -> list[tuple[Ver, Ver | None]]:
    out = []
    for alt in re.split(r"\s*\|\|?\s*", spec.strip()):
        lo, hi = (0, 0, 0), None
        ok = False
        for tok in re.split(r"[,\s]+(?=[\^~<>=v\d*x])", alt.strip()):
            iv = _interval(tok, composer)
            if iv is None:
                continue
            ok = True
            lo = max(lo, iv[0])
            hi = iv[1] if hi is None else (min(hi, iv[1]) if iv[1] else hi)
        if ok:
            out.append((lo, hi))
    return out


def choose(spec: str, allowed: list[str], default: str, *, composer: bool = False, minimum: bool = False) -> str | None:
    """The catalog version to build with for a version requirement, or None when the platform default already fits.
    `minimum` treats a bare version as a floor (go.mod's `go 1.22` means 1.22 or newer)."""
    ranges = _ranges(spec, composer)
    if not ranges:
        return None
    if minimum:
        ranges = [(lo, None) for lo, _ in ranges]

    def fits(v: str) -> bool:
        flo, fhi = _family(v)
        return any(flo < (hi or (999, 0, 0)) and fhi > lo for lo, hi in ranges)

    if fits(default):
        return None
    ok = [v for v in allowed if fits(v)]
    if ok:
        return max(ok, key=_tup)
    lowest = min(lo for lo, _ in ranges)
    higher = [v for v in allowed if _family(v)[0] >= lowest]
    return min(higher, key=_tup) if higher else max(allowed, key=_tup)  # everything offered is older than asked: use the newest


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="ignore")
    except OSError:
        return ""


def _first(root: Path, pattern: str) -> Path | None:
    skip = {"node_modules", ".git", "bin", "obj", "vendor", ".venv", "venv", ".agent-state"}
    for f in sorted(root.glob(pattern)):
        if not skip & set(f.relative_to(root).parts):
            return f
    return None


def _requirement(platform: str, root: Path) -> tuple[str, str, bool, bool] | None:
    """(requirement, source, composer-style ranges, bare version means 'or newer') for a platform, or None when the project says nothing."""
    if platform in NODE_LIKE:
        try:
            eng = (json.loads(_read(root / "package.json")).get("engines") or {}).get("node")
        except ValueError:
            eng = None
        if eng:
            return str(eng), "package.json engines.node", False, False
        nv = _read(root / ".nvmrc").strip() or _read(root / ".node-version").strip()
        if re.match(r"^v?\d", nv):
            return nv, ".nvmrc", False, False
    elif platform in PYTHON_LIKE:
        if m := re.search(r"(\d+\.\d+(?:\.\d+)?)", _read(root / ".python-version")):
            return m.group(1), ".python-version", False, False
        if m := re.search(r"python-(\d+\.\d+(?:\.\d+)?)", _read(root / "runtime.txt")):
            return m.group(1), "runtime.txt", False, False
        if m := re.search(r'python_version\s*=\s*"(\d+\.\d+(?:\.\d+)?)"', _read(root / "Pipfile")):
            return m.group(1), "Pipfile", False, False
        try:
            py = tomllib.loads(_read(root / "pyproject.toml"))
        except tomllib.TOMLDecodeError:
            py = {}
        req = (py.get("project") or {}).get("requires-python") or ((py.get("tool") or {}).get("poetry") or {}).get("dependencies", {}).get("python")
        if isinstance(req, str):
            return req, "pyproject.toml", False, False
    elif platform in PHP_LIKE:
        try:
            comp = json.loads(_read(root / "composer.json"))
        except ValueError:
            comp = {}
        req = ((comp.get("config") or {}).get("platform") or {}).get("php") or (comp.get("require") or {}).get("php")
        if isinstance(req, str):
            return req, "composer.json", True, False
    elif platform == "go":
        if m := re.search(r"^go\s+(\d+\.\d+(?:\.\d+)?)\s*$", _read(_first(root, "go.mod") or root / "go.mod"), re.M):
            return m.group(1), "go.mod", False, True
    elif platform == "dotnet":
        csproj = _first(root, "**/*.csproj")
        if csproj and (m := re.search(r"<TargetFramework>(?:net(?:coreapp)?)(\d+\.\d+)</TargetFramework>", _read(csproj))):
            return m.group(1), csproj.name, False, False
    return None


def detect(platform: str, root: Path) -> Detected | None:
    info = liara_catalog.catalog()["platforms"].get(platform, {}).get("versions")
    req = _requirement(platform, Path(root))
    if not info or not req:
        return None
    spec, source, composer, minimum = req
    version = choose(spec, info["allVersions"], info["defaultVersion"], composer=composer, minimum=minimum)
    if version is None:
        return None
    return Detected({CONFIG_KEY.get(platform, "version"): version}, version, source, spec)


def engine_conflict(root: Path, runtime_major: int) -> str | None:
    """package.json's engines.node when yarn/npm would reject the runtime we are about to build with, else None.
    The exact patch level of Liara's image is unknown, so the newest possible one is assumed: a pin like 13.12.0, 16.13.0 or ~18.2 still conflicts."""
    try:
        eng = (json.loads(_read(Path(root) / "package.json")).get("engines") or {}).get("node")
    except ValueError:
        return None
    if not isinstance(eng, str) or not (ranges := _ranges(eng, False)):
        return None
    probe = (runtime_major, 99, 99)
    return None if any(lo <= probe < (hi or (999, 0, 0)) for lo, hi in ranges) else eng
