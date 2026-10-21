#!/usr/bin/env python
"""Snapshot Liara's one-click app catalog (names, categories, upstream GitHub repo, Liara Compose spec)
into src/liara_agent/liara/oneclick.generated.json. Needs LIARA_API_TOKEN. Read-only GETs."""
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from liara_agent.config import get_settings  # noqa: E402

OUT = Path(__file__).parent.parent / "src" / "liara_agent" / "liara" / "oneclick.generated.json"


def main() -> None:
    cfg = get_settings()
    http = httpx.Client(base_url="https://api.liara.ir", headers={"Authorization": f"Bearer {cfg.liara_api_token}"}, timeout=30)
    listing, page = [], 1
    while batch := http.get("/v1/one-click-apps", params={"page": page}).json().get("oneClickApps", []):
        listing += batch
        page += 1
    apps = {}
    for a in listing:
        d = http.get(f"/v1/one-click-apps/{a['name']}").json()["oneClickApp"]
        apps[a["name"]] = {
            "about": d.get("about"), "categories": d.get("categories", []), "installs": d.get("numberOfInstall"),
            "github": f"{d['githubOrg']}/{d['githubRepo']}".lower() if d.get("githubOrg") and d.get("githubRepo") else None,
            "spec": d.get("spec"),  # absent for VM/IaaS apps
        }
    OUT.write_text(json.dumps(apps, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {OUT.name}: {len(apps)} apps, {sum(1 for v in apps.values() if v['spec'])} with a compose spec, "
          f"{sum(1 for v in apps.values() if v['github'])} with a GitHub repo")


if __name__ == "__main__":
    main()
