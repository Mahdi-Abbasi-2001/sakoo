"""Snapshot Liara's public docs (github.com/liara-cloud/docs, public/llms/*) into src/liara_agent/liara/docs.generated.jsonl.gz.

    python scripts/gen_docs.py

Chunks of about 1,500 characters, split at headings, each with its page path and title. The agent's `search_docs` tool reads this file; nothing is fetched at run time.
The one-click app pages and the AI SDK guides are left out (the first are in oneclick.generated.json, the second are not about deploying)."""
from __future__ import annotations

import gzip
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

REPO = "liara-cloud/docs"
SECTIONS = ("paas", "dbaas", "object-storage", "email-server", "dns-management-system", "wordpress", "overview", "references", "iaas", "mirrors")
OUT = Path(__file__).resolve().parent.parent / "src/liara_agent/liara/docs.generated.jsonl.gz"
LIMIT = 1500


def chunks(text: str) -> list[tuple[str, str]]:
    """(heading, body) pieces no longer than LIMIT, split at markdown headings and then at paragraph breaks."""
    out: list[tuple[str, str]] = []
    heading = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        while len(body) > LIMIT:
            cut = body.rfind("\n\n", 0, LIMIT)
            cut = cut if cut > LIMIT // 3 else LIMIT
            out.append((heading, body[:cut].strip()))
            body = body[cut:].strip()
        if body:
            out.append((heading, body))
        buf.clear()

    for line in text.splitlines():
        if re.match(r"#{1,4}\s", line):
            flush()
            heading = line.lstrip("# ").strip()
        buf.append(line)
    flush()
    return out


def main() -> None:
    with httpx.Client(timeout=30, follow_redirects=True) as c:
        tree = c.get(f"https://api.github.com/repos/{REPO}/git/trees/master?recursive=1").json()["tree"]
        files = [t["path"] for t in tree if t["type"] == "blob" and t["path"].endswith(".md") and t["path"].startswith("public/llms/")
                 and t["path"].split("/")[2] in SECTIONS]
        print(len(files), "pages", file=sys.stderr)

        def get(path: str) -> tuple[str, str]:
            for _ in range(3):
                try:
                    r = c.get(f"https://raw.githubusercontent.com/{REPO}/master/{path}")
                    if r.status_code == 200:
                        return path, r.text
                except httpx.HTTPError:
                    pass
            return path, ""

        with ThreadPoolExecutor(16) as pool:
            pages = list(pool.map(get, files))
    n = 0
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        for path, text in pages:
            if not text.strip():
                continue
            title = next((l.lstrip("# ").strip() for l in text.splitlines() if l.startswith("#")), path)
            for heading, body in chunks(text):
                f.write(json.dumps({"path": path.removeprefix("public/llms/"), "title": title, "heading": heading, "text": body}, ensure_ascii=False) + "\n")
                n += 1
    print(n, "chunks ->", OUT, OUT.stat().st_size // 1024, "KB", file=sys.stderr)


if __name__ == "__main__":
    main()
