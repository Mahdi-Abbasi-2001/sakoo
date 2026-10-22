"""Cost estimates and plan selection from the live catalog. Prices are toman/hour before 10% VAT."""
from __future__ import annotations

from dataclasses import dataclass

from liara_agent.liara.live import LiveCatalog

HOURS_PER_MONTH = 720  # the console bills monthly as 30 days
VAT = 0.10


@dataclass(frozen=True)
class Cost:
    hourly: float  # before VAT

    @property
    def hourly_with_vat(self) -> float:
        return self.hourly * (1 + VAT)

    @property
    def monthly(self) -> float:
        # Liara's monthly prices are round numbers; the API's hourly figures are rounded to 2 decimals,
        # so snap to the nearest 100 toman to recover the number the console shows.
        return round(self.hourly * HOURS_PER_MONTH, -2)

    @property
    def monthly_with_vat(self) -> float:
        return self.monthly * (1 + VAT)

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(self.hourly + other.hourly)


def project_cost(cat: LiveCatalog, plan: str, tier: str = "basic") -> Cost:
    return Cost(cat.project_plans[plan].price + cat.project_bundles[plan][tier].price)


def database_cost(cat: LiveCatalog, plan: str, tier: str = "basic") -> Cost:
    return Cost(cat.database_plans[plan].price + cat.database_bundles[plan][tier].price)


def storage_cost(cat: LiveCatalog, plan: str) -> Cost:
    return Cost(cat.storage_plans[plan].price)


def choose_storage_plan(cat: LiveCatalog, *, min_gb: float = 0) -> str:
    """Cheapest available bucket plan with enough space. On a price tie the shorter id wins ('20g', which the API documents, over '20g-g2')."""
    ok = [p for p in cat.storage_plans.values() if p.available and p.space >= min_gb]
    if not ok:
        raise LookupError("no available Liara storage plan")
    return min(ok, key=lambda p: (p.price, len(p.id), p.id)).id


def mail_cost(cat: LiveCatalog, plan: str) -> Cost:
    """Mail plans are priced per month: an hourly figure is derived so every resource adds up the same way."""
    return Cost(cat.mail_plans[plan].price / HOURS_PER_MONTH)


def choose_mail_plan(cat: LiveCatalog, *, min_per_day: float = 0) -> str:
    ok = [p for p in cat.mail_plans.values() if p.available and (p.max_outbound_per_day or 0) >= min_per_day]
    if not ok:
        raise LookupError("no available Liara email plan")
    return min(ok, key=lambda p: (p.price, p.id)).id


def choose_project_plan(cat: LiveCatalog, *, min_ram_gb: float = 0.5, min_build_minutes: float = 0,
                        min_source_mb: float = 0) -> tuple[str, str]:
    """Cheapest available (plan, tier) that meets the app's needs. Newer "-g2" plans only: they are cheaper
    than the legacy ones and have tiered bundles."""
    best: tuple[float, str, str] | None = None
    for plan_id, plan in cat.project_plans.items():
        if not plan.available or not plan_id.endswith("-g2") or plan.ram.amount < min_ram_gb:
            continue
        for tier, b in cat.project_bundles.get(plan_id, {}).items():
            if not b.available or b.build_timeout < min_build_minutes or b.max_source_size < min_source_mb:
                continue
            cost = project_cost(cat, plan_id, tier).hourly
            if best is None or cost < best[0]:
                best = (cost, plan_id, tier)
    if best is None:
        raise LookupError("no available Liara plan satisfies the requirements")
    return best[1], best[2]


def choose_database_plan(cat: LiveCatalog, kind: str, *, min_ram_gb: float = 0.5, min_disk_gb: float = 0,
                         need_backups: bool = False) -> tuple[str, str]:
    """Cheapest available (plan, tier) for a database type. Not every plan supports every type
    (e.g. Elasticsearch needs 1 GB+, SQL Server and ClickHouse need 2 GB+), and the basic tier has no backups."""
    if kind not in cat.database_versions:
        raise LookupError(f"Liara does not offer {kind!r}; available: {sorted(cat.database_versions)}")
    best: tuple[float, str, str] | None = None
    for plan_id, plan in cat.database_plans.items():
        if (not plan.available or not plan_id.endswith("-g2") or kind not in plan.supports
                or plan.ram.amount < min_ram_gb or plan.volume < min_disk_gb):
            continue
        for tier, b in cat.database_bundles.get(plan_id, {}).items():
            if not b.available or (need_backups and not b.backup_intervals):
                continue
            cost = database_cost(cat, plan_id, tier).hourly
            if best is None or cost < best[0]:
                best = (cost, plan_id, tier)
    if best is None:
        raise LookupError(f"no available Liara plan can host {kind}")
    return best[1], best[2]


@dataclass
class PlanCostLine:
    kind: str  # app | database | bucket | mail
    name: str
    plan: str
    tier: str
    cost: Cost


def estimate_plan(cat: LiveCatalog, plan, *, min_build_minutes: float = 0) -> list[PlanCostLine]:
    """Cheapest sensible plan per resource. Defaults to the basic tier; databases and big builds may need more."""
    lines: list[PlanCostLine] = []
    for app in plan.apps:
        pid, tier = choose_project_plan(cat, min_ram_gb=app.min_ram_gb, min_build_minutes=max(min_build_minutes, getattr(app, "min_build_minutes", 0)))
        lines.append(PlanCostLine("app", app.name, pid, tier, project_cost(cat, pid, tier)))
    for db in plan.databases:
        pid, tier = choose_database_plan(cat, db.kind, min_ram_gb=db.min_ram_gb)
        lines.append(PlanCostLine("database", db.id, pid, tier, database_cost(cat, pid, tier)))
    for st in getattr(plan, "storage", []):
        pid = choose_storage_plan(cat)
        lines.append(PlanCostLine("bucket", st.id, pid, "", storage_cost(cat, pid)))
    if getattr(plan, "mail", None):
        pid = choose_mail_plan(cat)
        lines.append(PlanCostLine("mail", plan.mail.domain, pid, "", mail_cost(cat, pid)))
    return lines


def total(lines: list[PlanCostLine]) -> Cost:
    return Cost(sum(l.cost.hourly for l in lines))
