"""Is a domain's DNS really in place, and if not, why? The same record is asked of the zone's OWN nameservers (the truth) and of the big public
resolvers (what the world sees). The two differing is the usual story of a new domain: the record is right and caches have not caught up."""
from __future__ import annotations

from typing import Callable

import dns.exception
import dns.resolver

PUBLIC_RESOLVERS = {"Google": "8.8.8.8", "Cloudflare": "1.1.1.1", "Quad9": "9.9.9.9"}
TIMEOUT = 3.0

Lookup = Callable[[str | None, str, str], list[str]]  # (nameserver ip or None for the system resolver, name, type) -> answers; raises LookupFailed


class LookupFailed(Exception):
    """The resolver did not answer (timeout, refused...). Distinct from 'answered: no such record', which is an empty list."""


def _norm(v: str) -> str:
    return v.strip().rstrip(".").strip('"').lower()


def lookup(server: str | None, name: str, rtype: str) -> list[str]:
    r = dns.resolver.Resolver(configure=server is None)
    if server:
        r.nameservers = [server]
    r.lifetime = r.timeout = TIMEOUT
    try:
        answer = r.resolve(name, rtype, raise_on_no_answer=False)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers) as e:
        if isinstance(e, dns.resolver.NoNameservers):
            raise LookupFailed(str(e)) from e
        return []
    except (dns.exception.Timeout, dns.resolver.YXDOMAIN, dns.exception.DNSException, OSError) as e:
        raise LookupFailed(str(e)) from e
    out = []
    for rd in answer:
        if rtype == "TXT":
            out.append(_norm(b"".join(rd.strings).decode(errors="ignore")))
        elif rtype in ("CNAME", "NS", "MX"):
            out.append(_norm(str(rd.exchange if rtype == "MX" else rd.target)))
        else:
            out.append(_norm(rd.to_text()))
    return out


def nameservers_of(zone: str, look: Lookup = lookup) -> tuple[list[str], dict[str, str]]:
    """The zone's NS host names and an IP for each (looked up with the system resolver)."""
    try:
        hosts = look(None, zone, "NS")
    except LookupFailed:
        return [], {}
    ips: dict[str, str] = {}
    for h in hosts:
        try:
            a = look(None, h, "A")
        except LookupFailed:
            continue
        if a:
            ips[h] = a[0]
    return hosts, ips


def _status(look: Lookup, server: str | None, fqdn: str, rec: dict, target_ips: Callable[[], set[str]]) -> str:
    """ok | missing | mismatch | no_answer for one record asked of one resolver."""
    rtype, want = rec["type"], _norm(rec["value"]) if rec.get("value") else ""
    try:
        if rtype in ("ALIAS", "CNAME") and rec.get("purpose") != "acme":
            # a route may be answered as a CNAME or flattened to addresses; either is right when it ends at the target
            cn = look(server, fqdn, "CNAME")
            if cn:
                return "ok" if not want or _norm(cn[0]) == want else "mismatch"
            addrs = set(look(server, fqdn, "A"))
            if not addrs:
                return "missing"
            if not want:
                return "ok"
            return "ok" if addrs & target_ips() else "mismatch"
        got = look(server, fqdn, "CNAME" if rtype == "CNAME" else rtype)
    except LookupFailed:
        return "no_answer"
    if not got:
        return "missing"
    return "ok" if not want or any(want == g or want in g for g in got) else "mismatch"


def check_domain(domain: str, zone: str, records: list[dict], *, look: Lookup = lookup) -> dict:
    """The verdict for a domain's records. Only records whose value is known are compared; the rest are only asked whether they exist."""
    hosts, ips = nameservers_of(zone, look)
    cache: dict[str, set[str]] = {}

    def target_ips_for(rec: dict) -> Callable[[], set[str]]:
        def go() -> set[str]:
            v = _norm(rec.get("value", ""))
            if v not in cache:
                try:
                    cache[v] = set(look(None, v, "A")) if v else set()
                except LookupFailed:
                    cache[v] = set()
            return cache[v]
        return go

    rows = []
    for rec in records:
        # names are relative to the zone ('nodejs', '_acme-challenge.nodejs'); '@' is the zone itself
        fq = zone if rec["name"] in ("@", "") else f"{rec['name']}.{zone}"
        auth = {h: _status(look, ip, fq, rec, target_ips_for(rec)) for h, ip in ips.items()}
        pub = {n: _status(look, ip, fq, rec, target_ips_for(rec)) for n, ip in PUBLIC_RESOLVERS.items()}
        local = _status(look, None, fq, rec, target_ips_for(rec))
        rows.append({"type": rec["type"], "name": rec["name"], "fqdn": fq, "purpose": rec.get("purpose", ""), "authoritative": auth, "public": pub, "local": local})
    return {"zone": zone, "nameservers": hosts, "records": rows, "verdict": verdict(hosts, ips, rows)}


def verdict(hosts: list[str], ips: dict[str, str], rows: list[dict]) -> str:
    """One of:
    no_nameservers     the zone has no NS records the world can find (the domain is not set up at its registrar)
    not_at_liara       its nameservers are not Liara's, so records kept in a Liara zone are not served
    missing            a record is not at the authoritative nameservers (nothing the world can do)
    wrong_value        a record is there but points somewhere else
    propagating        everything is right at the source, and some resolvers have not caught up
    all_good           right everywhere
    unknown            nothing answered"""
    if not hosts:
        return "no_nameservers"
    if not rows:
        return "unknown"
    auth_states = [s for r in rows for s in r["authoritative"].values()]
    pub_states = [s for r in rows for s in [*r["public"].values(), r["local"]]]
    if not ips or not auth_states:
        return "unknown"
    if all(s == "no_answer" for s in auth_states):  # the zone's own nameservers cannot be reached: nothing can be said
        return "unknown"
    if "mismatch" in auth_states:
        return "wrong_value"
    if "missing" in auth_states:
        return "not_at_liara" if not any("liara" in h for h in hosts) else "missing"
    if all(s == "ok" for s in auth_states + pub_states):
        return "all_good"
    return "propagating"
