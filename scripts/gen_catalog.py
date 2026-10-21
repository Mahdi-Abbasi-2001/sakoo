#!/usr/bin/env python
"""Regenerate src/liara_agent/liara/catalog.generated.json from the installed Liara CLI.

Database types/versions and plans are NOT in the CLI; they come from the Liara API (`GET v1/me`,
`liara plan list`) at runtime and are filled by liara_agent.liara.live (needs an API token).
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE.parent / "src" / "liara_agent" / "liara" / "catalog.generated.json"


def cli_root() -> Path:
    exe = shutil.which("liara")
    if not exe:
        sys.exit("liara CLI not found on PATH (npm i -g @liara/cli)")
    root = Path(exe).resolve().parent.parent  # .../@liara/cli/bin/run.js -> .../@liara/cli
    if not (root / "package.json").exists():
        root = Path(subprocess.check_output(["npm", "root", "-g"], text=True).strip()) / "@liara" / "cli"
    return root


def generate() -> dict:
    root = cli_root()
    raw = subprocess.check_output(["node", str(HERE / "dump_cli_catalog.mjs"), str(root)], text=True)
    return json.loads(raw)


if __name__ == "__main__":
    data = generate()
    OUT.write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {OUT} (CLI {data['cli_version']}, {len(data['platforms'])} platforms)")
