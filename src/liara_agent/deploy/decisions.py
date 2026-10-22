"""Decisions the agent should not take alone because they spend money or change where data lives: object storage, email and WordPress.

Each is a question with stable option ids (the UI words them from `msg.question.<code>` and `msg.option.<code>.<id>`), asked in the plan phase before
anything is created. The code only ever adds to the plan what the person agreed to."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from liara_agent.deploy.services import bucket_name, wire_mail_env, wire_storage_env
from liara_agent.detect.wordpress import MANAGED_CHEAPEST_MONTHLY_INCL_VAT
from liara_agent.domains import DomainError, normalize
from liara_agent.detect.buildweight import estimate_build
from liara_agent.liara.live import TIER_LABELS, LiveCatalog
from liara_agent.liara.pricing import (HOURS_PER_MONTH, VAT, choose_database_plan, choose_mail_plan, choose_project_plan, choose_storage_plan, database_cost, mail_cost,
                                       project_cost, storage_cost)
from liara_agent.plan.model import DeployPlan, MailSpec, Note, StorageSpec

# channel.question(text, options, code=, params=) -> the chosen option id, or the typed text when there are no options
Ask = Callable[..., str]


def _month(cost) -> int:
    return round(cost.monthly_with_vat)


def decide_storage(plan: DeployPlan, hints: set[tuple[str, str]], detected: set[str], cat: LiveCatalog, ask: Ask) -> None:
    """The code uses an S3-compatible client. A Liara bucket costs as much as a small app, so it is offered, never assumed: the person may be using
    another storage service on purpose (and a plain local uploads folder is better served by a disk)."""
    if ("service", "object-storage") not in hints or plan.storage or not plan.apps:
        return
    try:
        monthly = _month(storage_cost(cat, choose_storage_plan(cat)))
    except LookupError:
        return
    answer = ask("Your app uses an S3-compatible storage library. Create a Liara storage bucket and connect it, or keep your current storage settings?",
                 ["create_bucket", "keep_current"], code="storage_choice", params={"monthly": monthly})
    if answer != "create_bucket":
        plan.notes.append(Note(level="info", message="storage settings are left as they are", code="storage_kept"))
        return
    spec = StorageSpec(id=bucket_name(plan.apps[0].name))
    plan.storage.append(spec)
    names = wire_storage_env(plan, spec, detected)
    plan.notes.append(Note(level="info", message=f"{plan.apps[0].name} reaches the bucket {spec.id} through {', '.join(sorted(names))}", code="storage_wired",
                           params={"bucket": spec.id, "names": sorted(names)}))


BUILD_MARGIN = 1.25   # a build estimate is rough: ask for a tier that allows a quarter more than it


def decide_build_tier(plan: DeployPlan, root: str | Path, cat: LiveCatalog, ask: Ask) -> None:
    """A heavy JavaScript build does not finish within the cheapest tier's time limit, and a build that runs out of time still costs that whole time. When the estimate says so,
    the person chooses before anything is created: the tier that fits (the agent's recommendation) or the cheapest one anyway. A failed build still gets the usual upgrade offer."""
    for app in plan.apps:
        if app.platform not in ("node", "docker") or app.image or app.min_build_minutes:
            continue
        est = estimate_build(Path(root) / app.path)
        if not est:
            continue
        need = est.minutes * BUILD_MARGIN
        try:
            cheap = choose_project_plan(cat, min_ram_gb=app.min_ram_gb)
            fit = choose_project_plan(cat, min_ram_gb=app.min_ram_gb, min_build_minutes=need)
        except LookupError:
            continue
        cheap_limit, fit_limit = cat.project_bundles[cheap[0]][cheap[1]].build_timeout, cat.project_bundles[fit[0]][fit[1]].build_timeout
        if cheap_limit >= need or fit == cheap:
            continue
        answer = ask(f"{app.name}: the build looks heavy (about {est.rounded()} minutes) and the cheapest tier stops builds after {cheap_limit:g} minutes. Use the {TIER_LABELS.get(fit[1], fit[1])} tier "
                     f"(builds up to {fit_limit:g} minutes) or the cheapest anyway?", ["recommended", "smallest"], code="build_tier_choice",
                     params={"app": app.name, "minutes": est.rounded(), "smallest_tier": TIER_LABELS.get(cheap[1], cheap[1]), "smallest_minutes": cheap_limit,
                             "smallest_monthly": _month(project_cost(cat, *cheap)), "recommended_tier": TIER_LABELS.get(fit[1], fit[1]), "recommended_minutes": fit_limit,
                             "recommended_monthly": _month(project_cost(cat, *fit))})
        if answer != "smallest":
            app.min_build_minutes = need


def decide_mail(plan: DeployPlan, hints: set[tuple[str, str]], detected: set[str], cat: LiveCatalog, ask: Ask) -> None:
    """The code sends email. Liara's email server needs a domain the developer owns and DNS records at their DNS provider, and its cheapest available
    plan is expensive, so it is one option among three; the other two cost nothing and leave the app's own SMTP settings alone."""
    if ("service", "mail") not in hints or plan.mail or not plan.apps:
        return
    try:
        monthly = _month(mail_cost(cat, choose_mail_plan(cat)))
    except LookupError:
        return
    answer = ask("Your app sends email. Use Liara's email server for a domain you own, keep using your own SMTP provider, or leave email for later?",
                 ["liara_mail", "own_smtp", "skip"], code="mail_choice", params={"monthly": monthly})
    if answer == "own_smtp":
        plan.notes.append(Note(level="info", message="email keeps using your own SMTP provider; set its variables yourself", code="mail_own_smtp"))
        return
    if answer != "liara_mail":
        plan.notes.append(Note(level="info", message="email is left for later", code="mail_skipped"))
        return
    domain = ask("Which domain should the emails be sent from? (for example mydomain.com)", [], code="mail_domain", params={})
    try:
        name = normalize(domain)
    except DomainError:
        plan.notes.append(Note(level="warning", message=f"{domain!r} is not a domain, so no email server is created", code="mail_domain_invalid", params={"domain": domain}))
        return
    plan.mail = MailSpec(domain=name)  # created in Liara's DEV mode: emails stay in the sent folder until the person switches the server to live
    names = wire_mail_env(plan, plan.mail, detected)
    plan.notes.append(Note(level="info", message=f"{plan.apps[0].name} sends email through the email server of {name} ({', '.join(sorted(names))})", code="mail_wired",
                           params={"domain": name, "names": sorted(names)}))


def wordpress_estimates(cat: LiveCatalog) -> dict[str, int]:
    """Monthly cost incl. VAT of the two ways to run WordPress: Liara's managed product, and a PHP app plus a MariaDB database."""
    generic = 0
    try:
        pid, tier = choose_project_plan(cat)
        did, dtier = choose_database_plan(cat, "mariadb")
        generic = round((project_cost(cat, pid, tier).hourly + database_cost(cat, did, dtier).hourly) * HOURS_PER_MONTH * (1 + VAT), -3)
    except LookupError:
        pass
    prices = {}
    for key, name in WORDPRESS_APPS.items():  # Liara's own WordPress image on the app platform, sized as its spec asks (an app, a MariaDB database and a disk; the disk's price is not in the API)
        try:
            from liara_agent.liara.pricing import estimate_plan, total
            from liara_agent.plan.oneclick import plan_from_spec
            spec = plan_from_spec(name, "wordpress", ram_of=lambda pid: cat.project_plans[pid].ram.amount if pid in cat.project_plans else 0.5)
            prices[key] = int(round(total(estimate_plan(cat, spec)).monthly_with_vat, -3))
        except Exception:
            prices[key] = 0
    return {"managed": MANAGED_CHEAPEST_MONTHLY_INCL_VAT, "generic": int(generic), "app": prices["openlitespeed"], "app_apache": prices["apache"]}


WORDPRESS_APPS = {"openlitespeed": "wordpress-openlitespeed-new", "apache": "wordpress-new"}  # Liara's two ready-made WordPress apps: each is an app + MariaDB + a disk, all creatable through the API
WORDPRESS_APP = WORDPRESS_APPS["openlitespeed"]  # the one the console recommends


def decide_wordpress(cat: LiveCatalog, marker: str, ask: Ask) -> str:
    """How to run a WordPress project: `wordpress_app` (Liara's WordPress image through the API, then the project's wp-content over FTPS: no console steps),
    `generic_php` (a PHP app plus MariaDB) or `managed_console` (the managed product, which only the console can create: the run then stops, with the way to it)."""
    est = wordpress_estimates(cat)
    options = ["wordpress_app", "generic_php", "managed_console"] if est["app"] else ["generic_php", "managed_console"]
    answer = ask("This is a WordPress site. Liara's ready-made WordPress can be installed through the API and your wp-content sent to it, a PHP app plus a database also works, "
                 "and there is a managed product that only the console can create. Which do you want?", options, code="wordpress_choice", params={**est, "marker": marker})
    return answer if answer in options else "generic_php"


def decide_wordpress_server(cat: LiveCatalog, root, ask: Ask) -> str:
    """Which of Liara's two WordPress apps: OpenLiteSpeed (faster, what the console recommends, but it only partly understands .htaccess) or Apache (a smaller and
    cheaper plan, runs .htaccess as is). Returns the ready-made app's name."""
    from pathlib import Path
    est = wordpress_estimates(cat)
    htaccess = any(Path(root).rglob(".htaccess")) if Path(root).is_dir() else False
    options = ["openlitespeed", "apache"]
    answer = ask("Which web server should the WordPress site run on? OpenLiteSpeed is faster and what Liara recommends; Apache runs .htaccess rules exactly and is cheaper.",
                 options, code="wordpress_server", params={"openlitespeed": est["app"], "apache": est["app_apache"], "htaccess": bool(htaccess)})
    return WORDPRESS_APPS[answer if answer in options else "openlitespeed"]
