"""How long will this project's build take, roughly? Liara stops a build at the tier's time limit (5 minutes on basic, 15 on silver, 60 on gold),
and a heavy JavaScript build on the cheapest plan does not fit in 5. Knowing that before the deploy lets the agent say so up front instead of after a failed build.

A rough estimate on purpose: an install costs about a minute plus a little per dependency, and every bundler run (webpack, react-scripts, next, ...) adds a few minutes on a small machine."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

INSTALL_BASE = 1.0       # minutes: the platform's own setup and the package manager's start
PER_DEPENDENCY = 0.08    # minutes per package in package.json (dependencies and devDependencies)
LAUNCHER = r"^\s*(?:(?:npx|yarn|pnpm\s+exec|cross-env\s+\S+|[A-Z_]+=\S+)\s+)*"   # the command itself, after `npx`, `cross-env X=1` or `NODE_ENV=production`
HEAVY = re.compile(LAUNCHER + r"(?:webpack|react-scripts\s+build|next\s+build|ng\s+build|nuxt\s+(?:build|generate)|gatsby\s+build|vue-cli-service\s+build|expo\s+export)\b")
LIGHT = re.compile(LAUNCHER + r"(?:vite\s+build|parcel\s+build|rollup|esbuild|tsc|tsup|swc)\b")
HEAVY_MINUTES, LIGHT_MINUTES = 3.0, 1.5
FRONTEND_DIRS = ("client", "frontend", "web", "ui", "app")   # a second package.json the root build script may install and build too
STEP_SCRIPTS = ("build", "postinstall", "heroku-postbuild", "prebuild")


@dataclass
class BuildEstimate:
    minutes: float
    dependencies: int
    bundler_runs: int

    def rounded(self) -> int:
        return max(1, round(self.minutes))


def _package(directory: Path) -> dict:
    try:
        data = json.loads((directory / "package.json").read_text(encoding="utf-8", errors="ignore"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _one(directory: Path, *, seen: frozenset[Path] = frozenset()) -> tuple[float, int, int]:
    pkg = _package(directory)
    if not pkg:
        return 0.0, 0, 0
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    steps = " && ".join(str(scripts.get(k, "")) for k in STEP_SCRIPTS if scripts.get(k))
    commands = [c for c in re.split(r"&&|\|\||;", steps) if c.strip()]
    deps = len(pkg.get("dependencies") or {}) + len(pkg.get("devDependencies") or {})
    heavy = sum(bool(HEAVY.match(c)) for c in commands)
    runs = heavy + sum(bool(LIGHT.match(c)) for c in commands if not HEAVY.match(c))
    minutes = INSTALL_BASE + PER_DEPENDENCY * deps + HEAVY_MINUTES * heavy + LIGHT_MINUTES * (runs - heavy)
    for name in FRONTEND_DIRS:   # `cd client && npm install && npm run build`: the other package is installed and built as well
        sub = directory / name
        if sub in seen or not (sub / "package.json").is_file() or not re.search(rf"\b{name}\b", steps):
            continue
        m, d, r = _one(sub, seen=seen | {directory})
        minutes, deps, runs = minutes + m, deps + d, runs + r
    return minutes, deps, runs


def estimate_build(directory: str | Path) -> BuildEstimate | None:
    """The build estimate of the package.json project in `directory`, or None when it is not a package.json project."""
    minutes, deps, runs = _one(Path(directory))
    return BuildEstimate(minutes, deps, runs) if deps or runs else None
