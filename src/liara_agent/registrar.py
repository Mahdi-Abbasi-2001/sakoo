"""What the person has to do at the place their domain is registered, step by step. The assistant cannot log in there, so this is the part it can only explain.

A `.ir` domain (and its second levels: co.ir, ac.ir, org.ir ...) lives at IRNIC, whose panel is the registrar: nameservers are changed there. The steps for it were taken from
two independent Persian tutorials that agree with each other (panel address, "My domains" > the domain > the DNS part > edit the nameserver rows, the secret answer, "Apply",
minutes to a few days); a panel can be renamed, so the page says what to look for rather than where to click blindly. Other domains get the same shape in general words.
"""
from __future__ import annotations

IRNIC_PANEL = "https://new.nic.ir/panel/auth/sign-in"
IRNIC_SOURCES = ["https://mihanwp.com/change-ir-domain-dns/", "https://blog.iranserver.com/setting-up-dns-on-ir-domains-on-the-irnic-site/"]


def is_irnic(domain: str) -> bool:
    return domain.lower().rstrip(".").endswith(".ir")


def guide(view: dict) -> dict:
    """`view` is a domain as the domains route returns it: {name, zone: {name_servers, active} | None, records: [{type, name, value}], ready, ssl_active}."""
    name = str(view.get("name") or "")
    zone = view.get("zone") or None
    mode = "liara_dns" if zone else "own_dns"
    ns = [n for n in (zone or {}).get("name_servers") or [] if n]
    records = [{"type": r.get("type"), "name": r.get("name"), "value": r.get("value")} for r in view.get("records") or [] if r.get("type") and r.get("value")]
    irnic = is_irnic(name)
    steps: list[dict] = []

    def add(code: str, **params) -> None:
        steps.append({"code": code, "params": params})

    if mode == "liara_dns" and zone and zone.get("active"):
        add("done_nameservers", domain=name)                     # Liara already serves the zone: nothing is left at the registrar
    elif irnic:
        add("irnic_login", url=IRNIC_PANEL)
        add("irnic_domains", domain=name)
        add("irnic_manage")
        add("irnic_dns_section")
        add("irnic_edit_rows")
        if mode == "liara_dns":
            add("irnic_enter_ns", nameservers=", ".join(ns)) if ns else add("ns_pending")
            add("irnic_ip_empty")
        else:
            add("irnic_enter_own_ns")
        add("irnic_secret")
        add("irnic_apply")
    else:
        add("reg_login")
        add("reg_find_ns")
        if mode == "liara_dns":
            add("reg_set_ns", nameservers=", ".join(ns)) if ns else add("ns_pending")
        else:
            add("reg_own_ns")
    if mode == "own_dns" and not (zone and zone.get("active")):
        add("records_at_provider", domain=name, count=len(records))
    add("wait")
    add("verify")
    return {"domain": name, "kind": "irnic" if irnic else "generic", "mode": mode, "panel_url": IRNIC_PANEL if irnic else None, "nameservers": ns, "records": records,
            "steps": steps, "wait_hours": {"min": 0, "max": 72}, "sources": IRNIC_SOURCES if irnic else []}
