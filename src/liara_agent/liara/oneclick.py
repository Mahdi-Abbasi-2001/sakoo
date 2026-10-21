"""Match a project to Liara's one-click (ready-made) apps, and read their Liara Compose specs."""
from __future__ import annotations

import configparser
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

_GITHUB_RE = re.compile(r"github\.com[:/]+([\w.-]+)/([\w.-]+?)(?:\.git)?(?:[/#?].*)?$", re.I)


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    return json.loads((Path(__file__).parent / "oneclick.generated.json").read_text())


@dataclass(frozen=True)
class OneClickMatch:
    app: str
    confidence: float  # 0.9 = same upstream GitHub repo, 0.4 = name only (could be an unrelated project)
    reason: str
    is_vm: bool  # VM/IaaS apps have no compose spec: they need a cloud server, not the app platform


def github_slug(url: str) -> str | None:
    m = _GITHUB_RE.search(url.strip())
    return f"{m.group(1)}/{m.group(2)}".lower() if m else None


def project_identities(root: str | Path) -> tuple[set[str], set[str]]:
    """(github slugs, package names) found in the project: git remotes and package.json."""
    root = Path(root)
    slugs: set[str] = set()
    names: set[str] = set()
    git_config = root / ".git" / "config"
    if git_config.is_file():
        cp = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            cp.read(git_config)
            for section in cp.sections():
                if section.startswith("remote") and cp.has_option(section, "url") and (s := github_slug(cp.get(section, "url"))):
                    slugs.add(s)
        except configparser.Error:
            pass
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text())
        except json.JSONDecodeError:
            data = {}
        repo = data.get("repository")
        url = repo if isinstance(repo, str) else (repo or {}).get("url", "")
        url = re.sub(r"^github:", "github.com/", url)  # npm's shorthand for a GitHub repo
        if url and (s := github_slug(url if "github" in url else f"github.com/{url}")):
            slugs.add(s)
        if isinstance(data.get("name"), str):
            names.add(data["name"].split("/")[-1].lower())
    return slugs, names


def find_matches(root: str | Path) -> list[OneClickMatch]:
    slugs, names = project_identities(root)
    out: list[OneClickMatch] = []
    for name, app in catalog().items():
        is_vm = not app["spec"]
        if app["github"] and app["github"] in slugs:
            out.append(OneClickMatch(name, 0.9, f"same upstream repo {app['github']}", is_vm))
        elif name.lower() in names:
            out.append(OneClickMatch(name, 0.4, f"package name {name!r} (not verified as the same project)", is_vm))
    return sorted(out, key=lambda m: -m.confidence)


def compose_spec(app: str) -> dict | None:
    spec = catalog()[app]["spec"]
    return yaml.safe_load(spec) if spec else None
