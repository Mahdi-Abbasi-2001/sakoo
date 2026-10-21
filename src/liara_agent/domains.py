"""Connecting the developer's own domain to a deployed app.

The flow mirrors Liara's console, split into explicit steps so a person approves each real change:

 1. optionally put the domain on Liara's DNS service (create the zone, then the developer sets Liara's nameservers at their registrar);
 2. attach the domain to the app: Liara answers with the DNS records the domain needs (a routing ALIAS/CNAME, an ownership TXT and a
    CNAME for the SSL challenge). On an active Liara zone Liara adds them itself; anywhere else the developer sets them and re-checks;
 3. request the free SSL certificate.

Only apps this agent created can get a domain, and only domains this agent attached can be changed (see the client's Journal).
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from liara_agent.liara.client import LiaraAPIError, LiaraClient

UNSUPPORTED_TLDS = {"gq", "cf", "ga", "ml", "tk"}  # Liara refuses these (free TLDs that are abused)
# registrable domains live one label below these; a short list of the common ones, since the zone is not always the last two labels
SECOND_LEVEL = {"co.ir", "ac.ir", "org.ir", "gov.ir", "net.ir", "sch.ir", "id.ir", "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.jp", "com.br", "com.tr"}
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,20}$")
RECORD_TYPES = {"A", "AAAA", "CNAME", "ALIAS", "TXT", "MX", "SRV", "CAA", "NS"}
NAME_KEYS, VALUE_KEYS = ("name", "host", "record", "subdomain", "key"), ("value", "content", "target", "data", "host", "text", "contents")
ACTIVE_STATUS = {"ACTIVE", "READY", "VERIFIED", "OK", "ENABLED", "ISSUED", "VALID", "DONE"}


class DomainError(Exception):
    """`code` + `params` are the translatable form (the UI owns the wording); `status` is the HTTP status the web API answers with."""

    def __init__(self, code: str, message: str, status: int = 422, **params):
        super().__init__(message)
        self.code, self.status, self.params = code, status, params


def normalize(raw: str) -> str:
    """'https://WWW.Example.com/path' -> 'example.com'. The www form is a separate subdomain, so it is stripped for the main domain."""
    name = (raw or "").strip().lower()
    name = re.sub(r"^[a-z]+://", "", name).split("/")[0].split("?")[0].split("#")[0].split(":")[0].rstrip(".")
    if name.startswith("www."):
        name = name[4:]
    if not DOMAIN_RE.match(name) or re.fullmatch(r"[\d.]+", name):
        raise DomainError("domain_invalid", f"{raw!r} is not a domain name (example: mydomain.com)")
    if name.endswith(".liara.run") or name == "liara.run":
        raise DomainError("domain_is_liara_address", "that is Liara's free address; connect a domain you own")
    tld = name.rsplit(".", 1)[1]
    if tld in UNSUPPORTED_TLDS:
        raise DomainError("domain_unsupported_tld", f".{tld} domains are not supported by Liara", tld=tld)
    return name


def zone_for(name: str, override: str | None = None) -> str:
    """The DNS zone (the registrable domain) a name lives in: 'shop.example.co.ir' -> 'example.co.ir'."""
    if override:
        return normalize(override)
    labels = name.split(".")
    keep = 3 if ".".join(labels[-2:]) in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


# -- reading what Liara asks for ----------------------------------------------------------------------------------------
def _looks_like_record(d: dict) -> bool:
    rtype = str(d.get("type") or d.get("recordType") or "").upper().replace(" ", "")
    return any(t in RECORD_TYPES for t in re.split(r"[/,]", rtype)) and any(k in d for k in VALUE_KEYS)


def _record(d: dict, hint: str) -> dict:
    rtype = str(d.get("type") or d.get("recordType") or "").upper().replace(" ", "")
    name = next((str(d[k]) for k in NAME_KEYS if d.get(k) and k != "host"), "")
    value = next((d[k] for k in VALUE_KEYS if d.get(k)), "")
    if isinstance(value, list):
        value = ", ".join(str((v.get("host") or v.get("ip") or v.get("text") or v) if isinstance(v, dict) else v) for v in value)
    name = name or "@"
    if "/" in rtype:  # "ALIAS/CNAME": an ALIAS is how a bare domain points elsewhere, anything below it is a CNAME
        rtype = "ALIAS" if name in ("@", "") or name.count(".") <= 1 else "CNAME"
    return {"type": rtype, "name": name, "value": str(value), "purpose": hint}


def _relative_to_zone(name: str) -> str:
    """'nodejs.mydomain.ir' -> 'nodejs'; the bare domain -> '@' (what DNS panels expect, and what the Liara console shows)."""
    zone = zone_for(name)
    return "@" if name == zone else name[: -len(zone) - 1]


def _is_status(v: Any) -> bool:
    """Liara's `CNameRecord` and `acmeRecord` fields hold the VERIFICATION STATUS of a record ("VALID" / "INVALID"), not its value (seen on a real domain)."""
    return isinstance(v, str) and v.strip().isupper() and "." not in v


def liara_records(d: dict, route_target: str = "") -> list[dict] | None:
    """The three records Liara asks for, from the fields it really returns (None when the object has none of them):
      route  ALIAS (bare domain) or CNAME -> a Liara host; its status is `CNameRecord`; the value is not in the API, so `route_target` (derived) is used
      TXT    `liara-challenge.<label>`    -> `verificationCode` (in the answer of the check call)
      CNAME  `_acme-challenge.<label>`    -> follows the pattern `<name-with-dashes>._acme-challenge.liara.cloud`; its status is `acmeRecord`.
    `derived` marks a value this agent worked out instead of reading it, so it is never written to a DNS zone for anybody."""
    if not ({"CNameRecord", "acmeRecord", "verificationCode"} & set(d)):
        return None
    domain = str(d.get("name") or "")
    rel = _relative_to_zone(domain) if domain else "@"
    pre = lambda p: p if rel == "@" else f"{p}.{rel}"  # noqa: E731
    route, acme = d.get("CNameRecord"), d.get("acmeRecord")
    base = str(d.get("punycodeName") or domain).replace(".", "-")
    out = [{"type": "ALIAS" if rel == "@" else "CNAME", "name": rel,
            "value": route_target if _is_status(route) or not route else str(route), "purpose": "route", "status": route if _is_status(route) else None,
            "derived": bool(route_target) and (_is_status(route) or not route)},
           {"type": "TXT", "name": pre("liara-challenge"), "value": str(d.get("verificationCode") or ""), "purpose": "ownership", "status": None, "derived": False},
           {"type": "CNAME", "name": pre("_acme-challenge"), "value": str(acme) if acme and not _is_status(acme) else f"{base}._acme-challenge.liara.cloud",
            "purpose": "acme", "status": acme if _is_status(acme) else None, "derived": False}]  # the pattern is confirmed on two real domains
    return out


def extract_records(obj: Any, route_target: str = "") -> list[dict]:
    """The DNS records in a Liara domain object. The fields Liara really returns come first (see liara_records); for anything else the public API
    does not document, any nested object that looks like a record (a record type plus a value) is collected."""
    if isinstance(obj, dict) and (r := liara_records(obj, route_target)) is not None:
        return r
    found: list[dict] = []

    def walk(x: Any, hint: str) -> None:
        if isinstance(x, list):
            for item in x:
                walk(item, hint)
        elif isinstance(x, dict):
            if _looks_like_record(x):
                found.append({**_record(x, hint), "status": None, "derived": False})
                return
            for k, v in x.items():
                walk(v, str(k))

    walk(obj, "")
    seen, out = set(), []
    for r in found:
        key = (r["type"], r["name"], r["value"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _status_ok(value: Any) -> bool:
    return str(value or "").upper() in ACTIVE_STATUS


def domain_view(d: dict, zone: dict | None = None, route_target: str = "") -> dict:
    records = extract_records(d, route_target)
    known = [str(r["status"]).upper() for r in records if r.get("status")]
    return {"id": d.get("_id") or d.get("id") or "", "name": d.get("name", ""), "status": d.get("status") or None,
            "certificates_status": d.get("certificatesStatus") or None, "redirect_to": d.get("redirectTo") or None,
            "ready": _status_ok(d.get("status")) and all(k == "VALID" for k in known),  # ACTIVE alone is not enough: a record can still be INVALID
            "ssl_active": _status_ok(d.get("certificatesStatus")),
            "records": records, "zone": zone_view(zone) if zone else None, "detail_keys": sorted(d.keys()),
            "details": d, "console_url": f"https://console.liara.ir/domains/{d['name']}" if d.get("name") else None}


def zone_view(z: dict | None) -> dict | None:
    if not z:
        return None
    return {"name": z.get("name"), "status": z.get("status"), "active": str(z.get("status", "")).upper() == "ACTIVE",
            "name_servers": z.get("nameServers") or [], "current_name_servers": z.get("currentNameServers") or []}


# -- steps -----------------------------------------------------------------------------------------------------------------
def plan_domain(client: LiaraClient, app: str, raw: str, *, use_liara_dns: bool = False, zone: str | None = None, www: bool = False) -> dict:
    """Everything that would happen, read-only. Nothing is created."""
    name = normalize(raw)
    attached = [d for d in client.app_domains(app) if d.get("name") == name]
    zone_name = zone_for(name, zone) if use_liara_dns else None
    z = client.zone(zone_name) if zone_name else None
    steps: list[dict] = []
    if use_liara_dns and not z:
        steps.append({"code": "zone_create", "params": {"zone": zone_name}})
    if use_liara_dns and not (z and str(z.get("status", "")).upper() == "ACTIVE"):
        steps.append({"code": "zone_nameservers", "params": {"zone": zone_name}})
    if not attached:
        steps.append({"code": "domain_attach", "params": {"domain": name, "app": app}})
    steps.append({"code": "records_added" if use_liara_dns else "records_manual", "params": {"domain": name}})  # records_added: Liara adds them to its own zone
    if www:
        steps.append({"code": "www_redirect", "params": {"domain": name, "www": f"www.{name}"}})
    steps.append({"code": "ssl_enable", "params": {"domain": name}})
    return {"domain": name, "app": app, "mode": "liara_dns" if use_liara_dns else "own_dns", "already_attached": bool(attached),
            "zone": zone_view(z) if z else ({"name": zone_name, "status": None, "active": False, "name_servers": [], "current_name_servers": []} if zone_name else None),
            "steps": steps}


def connect_domain(client: LiaraClient, app: str, raw: str, *, use_liara_dns: bool = False, zone: str | None = None, www: bool = False) -> dict:
    """Create what the plan described. Idempotent: an already attached domain is just re-read."""
    name = normalize(raw)
    zone_name = zone_for(name, zone) if use_liara_dns else None
    z = None
    if zone_name:
        z = client.zone(zone_name)
        if not z:
            z = client.create_zone(zone_name)
    domain = next((d for d in client.app_domains(app) if d.get("name") == name), None)
    created = domain is None
    if created:
        try:
            client.create_domain(app, name)
        except LiaraAPIError as e:
            if e.status == 409:
                raise DomainError("domain_taken", f"{name} is already attached to an app on Liara", status=409, domain=name) from e
            raise
        domain = next((d for d in client.app_domains(app) if d.get("name") == name), {"name": name})
    domain = _with_check(client, domain)
    view = domain_view(domain, z, route_target(app))
    view["created"] = created
    if www:
        add_www(client, app, name)
    return view  # on an active Liara DNS zone Liara adds the records itself (seen on a real zone); this agent never writes DNS records


def add_www(client: LiaraClient, app: str, name: str) -> None:
    """Attach www.<name> too and make it redirect to <name> (301). Liara redirects it to the main domain by default; the redirect is only set when it is not already."""
    wname = f"www.{name}"
    existing = next((d for d in client.app_domains(app) if d.get("name") == wname), None)
    if existing is None:
        try:
            client.create_domain(app, wname)
        except LiaraAPIError as e:
            if e.status == 409:
                raise DomainError("domain_taken", f"{wname} is already attached to an app on Liara", status=409, domain=wname) from e
            raise
        existing = next((d for d in client.app_domains(app) if d.get("name") == wname), None) or {}
    if existing and not existing.get("redirectTo"):
        did = existing.get("_id") or existing.get("id")
        if did:
            client.set_domain_redirect(did, name, 301)


def refresh_domain(client: LiaraClient, app: str, domain_id: str) -> dict:
    """Re-check the records at Liara ('check records' in the console) and re-read the domain and its zone."""
    try:
        d = client.domain_check(domain_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise DomainError("domain_not_found", "that domain is not attached to the app", status=404) from e
        raise
    if not d.get("name"):
        d = next((x for x in client.app_domains(app) if (x.get("_id") or x.get("id")) == domain_id), d)
    z = zone_or_none(client, zone_for(d["name"])) if d.get("name") else None
    view = domain_view(d, z, route_target(app))
    if z and str(z.get("status", "")).upper() != "ACTIVE":
        view["zone_check"] = check_zone(client, z["name"])
    return view


def _with_check(client: LiaraClient, domain: dict) -> dict:
    """The list answer carries record statuses; the check answer adds the verification code. Merge them (a failing check must not hide the domain)."""
    did = domain.get("_id") or domain.get("id")
    if not did:
        return domain
    try:
        return {**domain, **client.domain_check(did)}
    except (httpx.HTTPError, LiaraAPIError):
        return domain


def route_target(app: str) -> str:
    """The host the app's free address points to, which is what a custom domain must point to as well. Not in Liara's API, so it is read from DNS:
    the canonical name of <app>.liara.run. Empty when it cannot be resolved; the console then has the value."""
    import socket

    host = f"{app}.liara.run"
    try:
        canon = socket.getaddrinfo(host, None, flags=socket.AI_CANONNAME)[0][3]
    except OSError:
        return ""
    return canon if canon and canon.lower() != host else ""


LIARA_HOST = re.compile(r"\.(liara\.cloud|liara\.run|liara\.ir)\.?$", re.I)


def _content(record: dict) -> str:
    c = (record.get("contents") or [{}])[0]
    return str(next((c[k] for k in ("host", "ip", "text") if c.get(k)), "")) if isinstance(c, dict) else ""


def records_for_domain(client: LiaraClient, domain: str) -> tuple[dict | None, list[dict]]:
    """The DNS records in the domain's Liara zone that Liara added for it: the route (an ALIAS/CNAME to a Liara host), the SSL challenge CNAME and the
    ownership TXT. A record with one of those names that points somewhere else is the developer's own and is never listed."""
    zone = zone_or_none(client, zone_for(domain))
    if not zone:
        return None, []
    rel = _relative_to_zone(domain)
    pre = lambda p: p if rel == "@" else f"{p}.{rel}"  # noqa: E731
    names = {"route": {rel, domain}, "acme": {pre("_acme-challenge"), f"{pre('_acme-challenge')}.{zone['name']}"}, "ownership": {pre("liara-challenge"), f"{pre('liara-challenge')}.{zone['name']}"}}
    kinds = {"route": ("ALIAS", "CNAME"), "acme": ("CNAME",), "ownership": ("TXT",)}
    out = []
    for r in client.zone_records(zone["name"]):
        name, rtype, value = str(r.get("name", "")).rstrip("."), str(r.get("type", "")).upper(), _content(r)
        for purpose in ("route", "acme", "ownership"):
            if name in names[purpose] and rtype in kinds[purpose] and (purpose == "ownership" or LIARA_HOST.search(value)):
                out.append({"id": r["id"], "type": rtype, "name": r["name"], "value": value, "purpose": purpose})
    return zone, out


def removal_plan(client: LiaraClient, app: str, domain_id: str) -> dict:
    """What 'remove domain' would also be able to clean up in the developer's Liara DNS zone. Reads only."""
    d = next((x for x in client.app_domains(app) if (x.get("_id") or x.get("id")) == domain_id), None)
    if not d:
        raise DomainError("domain_not_found", "that domain is not attached to the app", status=404)
    zone, records = records_for_domain(client, d["name"])
    return {"domain": d["name"], "zone": zone_view(zone), "records": records}


def remove_domain(client: LiaraClient, app: str, domain_id: str, remove_records: bool = False) -> int:
    """Detach a domain this agent attached. With `remove_records` the records Liara added for it to a Liara DNS zone are deleted too (recomputed here, so only
    records that pass records_for_domain can ever go). Returns how many records were removed."""
    client._owned_domain(domain_id)  # a domain this agent did not attach is refused before anything is read
    plan = removal_plan(client, app, domain_id)
    client.delete_domain(domain_id)
    removed = 0
    if remove_records and plan["zone"]:
        for r in plan["records"]:
            client.journal.records.append({"zone": plan["zone"]["name"], "id": r["id"]})  # claimed for this domain, so the client's ownership rule allows the delete
            client.delete_record(plan["zone"]["name"], r["id"])
            removed += 1
    return removed


def zone_or_none(client: LiaraClient, name: str) -> dict | None:
    """The zone if the account has one; a DNS service that cannot be reached must not break showing the domain."""
    try:
        return client.zone(name)
    except httpx.HTTPError:
        return None


def check_zone(client: LiaraClient, zone: str) -> str:
    """'checked' | 'busy' (Liara is already checking, try later) | 'not_pending'."""
    try:
        client.check_zone(zone)
        return "checked"
    except LiaraAPIError as e:
        if e.status == 406:
            return "busy"
        if e.status == 404:
            return "not_pending"
        raise
