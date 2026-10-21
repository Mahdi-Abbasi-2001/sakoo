"""What Liara's API limits, said out loud: the daily number of deployments of an app (by feature tier), and a list of things the agent cannot do because the API offers no way to.

The daily count is estimated from the app's releases of the last 24 hours (Liara answers HTTP 428 `max_deployment_count_in_day` when it is used up, seen on a real account);
how Liara counts the day is not documented, so the number is shown as an estimate and the 428 is still explained when it comes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from liara_agent.liara.live import LiveCatalog

# Each one was met on the real service (or read from its API); the wording lives in the page's catalog under `lim.known.<code>`.
KNOWN = [
    "daily_deployments",        # an app may be released only so many times a day (basic 20, silver 50, gold unlimited); the next one is answered with HTTP 428
    "networks_not_deletable",   # a private network cannot be deleted through the API: an empty one stays until the person removes it in the console
    "tier_fixed_at_creation",   # the feature tier (basic/silver/gold) cannot be changed on an existing app; changing it means creating the app again
    "release_not_cancellable",  # a release that is building cannot be cancelled: stopping a run stops the agent, not Liara's build
    "app_names_global",         # an app name is unique across all of Liara: a taken name is answered with 409 and the agent picks another
    "logs_lag",                 # runtime logs and metrics trail the app by some seconds, and are empty for a container that has not started
    "free_address_expires",     # the free liara.run address stops working 7 days after the first deploy: a custom domain is needed to keep the site
    "cpu_unit_unknown",         # Liara does not document the unit of its CPU metric: it is read as cores
]


def cap_of(cat: LiveCatalog | None, plan_id: str, tier: str) -> int | None:
    """The deployments a day this plan's tier allows (None = unlimited or unknown)."""
    if not cat:
        return None
    b = cat.project_bundles.get(plan_id, {}).get(tier)
    if b is None:
        return None
    v = b.max_deployments_per_day
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None   # "infinite"
    return int(n) if n == n and n != float("inf") and n > 0 else None


def daily_deployments(releases: list[dict], cap: int | None, now: datetime | None = None) -> dict:
    """{used, cap, remaining, unlimited}: the releases created in the last 24 hours against the cap."""
    now = now or datetime.now(timezone.utc)
    used = 0
    for r in releases:
        try:
            t = datetime.fromisoformat(str(r.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        if now - t <= timedelta(hours=24):
            used += 1
    return {"used": used, "cap": cap, "remaining": None if cap is None else max(0, cap - used), "unlimited": cap is None}
