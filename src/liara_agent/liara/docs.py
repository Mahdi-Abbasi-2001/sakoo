"""Search Liara's own documentation (a snapshot made by scripts/gen_docs.py; nothing is fetched at run time). BM25 over chunks, no dependencies."""
from __future__ import annotations

import gzip
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "docs.generated.jsonl.gz"
BASE_URL = "https://docs.liara.ir/llms/"
TOKEN = re.compile(r"[\w؀-ۿ]{2,}")
K1, B = 1.4, 0.75


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text)]


class Index:
    def __init__(self, chunks: list[dict]):
        self.chunks = chunks
        self.tf = [Counter(_tokens(f"{c['title']} {c['heading']} {c['heading']} {c['text']}")) for c in chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if chunks else 1
        df: Counter = Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(chunks)
        self.idf = {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}

    def search(self, query: str, limit: int = 4) -> list[dict]:
        words = [w for w in dict.fromkeys(_tokens(query)) if w in self.idf]
        scored = []
        for i, tf in enumerate(self.tf):
            s = sum(self.idf[w] * tf[w] * (K1 + 1) / (tf[w] + K1 * (1 - B + B * self.len[i] / self.avg)) for w in words if w in tf)
            if s > 0:
                scored.append((s, i))
        out, seen = [], Counter()
        for s, i in sorted(scored, reverse=True):
            c = self.chunks[i]
            if seen[c["path"]] >= 2:  # one page must not fill the answer
                continue
            seen[c["path"]] += 1
            out.append({**c, "score": round(s, 2), "url": BASE_URL + c["path"]})
            if len(out) == limit:
                break
        return out


@lru_cache(maxsize=1)
def index() -> Index:
    if not SNAPSHOT.is_file():
        return Index([])
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as f:
        return Index([json.loads(line) for line in f])


def search(query: str, limit: int = 4) -> list[dict]:
    return index().search(query, limit)


def as_text(results: list[dict], max_chars: int = 1200) -> str:
    if not results:
        return "no matching documentation found"
    return "\n\n".join(f"[{r['title']}: {r['heading']}] {r['url']}\n{r['text'][:max_chars]}" for r in results)
