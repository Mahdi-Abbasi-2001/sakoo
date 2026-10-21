"""Read-only access to the Liara API: the live catalog (types, versions, plans, bundles) and account listings.

Everything here is GET-only. Mutating calls live elsewhere and always go through an explicit confirmation.
"""
from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from liara_agent.liara import catalog

API_URL = "https://api.liara.ir"
# The console calls the three feature-bundle tiers basic / silver / gold; the API calls them basic / standard / pro.
TIER_LABELS = {"basic": "basic", "standard": "silver", "pro": "gold"}


class Resources(BaseModel):
    amount: float
    shared: bool = False


class ProjectPlan(BaseModel):
    id: str = ""
    available: bool
    price: float  # toman per hour, before VAT
    volume: float  # disk, GB
    ram: Resources = Field(alias="RAM")
    cpu: Resources = Field(alias="CPU")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class DatabasePlan(ProjectPlan):
    supports: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    """A feature bundle for one plan and tier. Limits matter to the agent (build timeout, source size...)."""

    available: bool
    price: float  # toman per hour, before VAT
    build_timeout: float = Field(alias="buildTimeout", default=0)  # minutes
    max_source_size: float = Field(alias="maxSourceSize", default=0)  # MB
    max_deployments_per_day: float | str = Field(alias="maxDeploymentCountInDay", default=0)
    max_domains: float | str = Field(alias="maxDomainsCount", default=0)
    zero_downtime: bool = Field(alias="zeroDowntime", default=False)
    free_ssl: bool = Field(alias="freeSSL", default=False)
    backup_intervals: list[str] = Field(alias="availableBackupInterval", default_factory=list)  # empty = no backups

    model_config = {"populate_by_name": True, "extra": "ignore"}


class StoragePlan(BaseModel):
    """An object-storage (bucket) plan. `price` is toman per HOUR before VAT, like every other resource."""

    id: str = ""
    available: bool
    price: float
    space: float  # GB
    storage_class: str = Field(alias="storageClass", default="")

    model_config = {"populate_by_name": True, "extra": "ignore"}


class MailPlan(BaseModel):
    """An email-server plan. `price` is toman per MONTH before VAT (what the console shows; the CLI prints it as it is)."""

    id: str = ""
    available: bool
    price: float
    max_outbound_per_day: float | None = Field(alias="maxOutboundPerDay", default=None)
    max_outbound_per_month: float | None = Field(alias="maxOutboundPerMonth", default=None)
    max_account: float | None = Field(alias="maxAccount", default=None)

    model_config = {"populate_by_name": True, "extra": "ignore"}


class LiveCatalog(BaseModel):
    database_versions: dict[str, list[str]]
    default_database_versions: dict[str, str]
    project_plans: dict[str, ProjectPlan]
    database_plans: dict[str, DatabasePlan]
    project_bundles: dict[str, dict[str, Bundle]]
    database_bundles: dict[str, dict[str, Bundle]]
    storage_plans: dict[str, StoragePlan] = Field(default_factory=dict)
    mail_plans: dict[str, MailPlan] = Field(default_factory=dict)

    @classmethod
    def from_me(cls, me: dict[str, Any]) -> "LiveCatalog":
        plans = me["plans"]

        def with_ids(d):
            return {k: {**v, "id": k} for k, v in d.items()}

        def bundles(d):
            # g1 (legacy plans) uses a single "default" tier; skip nothing, callers check .available
            return {plan: {tier: Bundle(**b) for tier, b in tiers.items()} for plan, tiers in d.items()}

        return cls(
            database_versions={k: [v["value"] if isinstance(v, dict) else v for v in vs] for k, vs in me["databaseVersions"].items()},
            default_database_versions=me["defaultDatabaseVersions"],
            project_plans={k: ProjectPlan(**v) for k, v in with_ids(plans["projects"]).items()},
            database_plans={k: DatabasePlan(**v) for k, v in with_ids(plans["databases"]).items()},
            project_bundles=bundles(plans["projectBundlePlans"]),
            database_bundles=bundles(plans["databaseBundlePlans"]),
            storage_plans={k: StoragePlan(**v) for k, v in with_ids(plans.get("objectStorage", {})).items()},
            mail_plans={k: MailPlan(**v) for k, v in with_ids(plans.get("mail", {})).items()},
        )


def snapshot_catalog() -> LiveCatalog:
    """Offline copy of the public catalog from GET /v1/me (2026-10-17). Prices and plans may be stale."""
    import json
    from pathlib import Path

    return LiveCatalog.from_me(json.loads((Path(__file__).parent / "me_snapshot.json").read_text()))


class LiaraAPI:
    def __init__(self, token: str, team_id: str = "", base_url: str = API_URL, client: httpx.Client | None = None):
        if not token:
            raise ValueError("LIARA_API_TOKEN is not set")
        self.team_id = team_id  # empty = personal account; apps owned by a team are only visible with its id
        self._http = client or httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def _get(self, path: str) -> Any:
        r = self._http.get(path, params={"teamID": self.team_id} if self.team_id else None)
        r.raise_for_status()
        return r.json()

    def me(self) -> dict[str, Any]:
        return self._get("/v1/me")

    def live_catalog(self) -> LiveCatalog:
        cat = LiveCatalog.from_me(self.me())
        catalog.set_live_databases(list(cat.database_versions))
        return cat

    def list_teams(self) -> list[dict[str, Any]]:
        return self._get("/v2/teams")["teams"]

    def list_networks(self) -> list[dict[str, Any]]:
        return self._get("/v1/networks")["networks"]

    def list_app_ids(self) -> list[str]:
        """Names of existing apps, so we never reuse an id or touch an app we did not create."""
        return [p["project_id"] for p in self._get("/v1/projects")["projects"]]
