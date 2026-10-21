"""An app's CPU and memory over time, from Liara's metrics endpoints (GET /v1/projects/{name}/metrics/{cpu|memory}?since=...).

The spec says each answer is {end, result: [{applet, values: [[timestamp, "value"], ...]}]}: one series per running instance (applet). They are summed
per timestamp. Units are not documented (memory is probably bytes, CPU probably cores), so the numbers are returned as they are, with no unit guessed here.
"""
from __future__ import annotations

import time
from collections import defaultdict

KINDS = ("cpu", "memory")


def parse_series(payload: dict) -> list[dict]:
    totals: dict[float, float] = defaultdict(float)
    for applet in (payload or {}).get("result") or []:
        for point in applet.get("values") or []:
            try:
                totals[float(point[0])] += float(point[1])
            except (TypeError, ValueError, IndexError):
                continue
    return [{"ts": t, "value": round(v, 6)} for t, v in sorted(totals.items())]


def thin(series: list[dict], limit: int = 120) -> list[dict]:
    """At most `limit` points, evenly spaced, keeping the last one."""
    if len(series) <= limit:
        return series
    step = len(series) / limit
    picked = [series[int(i * step)] for i in range(limit)]
    return [*picked[:-1], series[-1]]


def fetch(client, app: str, hours: int = 6, now: float | None = None) -> dict:
    since = int((now or time.time()) - hours * 3600)
    out: dict = {"hours": hours}
    for kind in KINDS:
        out[kind] = thin(parse_series(client.metrics(app, kind, since)))
    return out
