import pytest
from test_domains import FakeLiara as DomainFake
from test_heal import READY, FakeLiara
from test_web import connect, deploy_and_approve, fast, make, register  # noqa: F401  (make and fast are fixtures)


class Liara(FakeLiara):
    """The deploy fake plus the domain and DNS endpoints."""

    def __init__(self, zone_status=None):
        super().__init__([READY])
        self.inner = DomainFake(zone_status)
        self.dns = self.inner.dns

    def __call__(self, req):
        return self.inner.api(req) if req.url.path.startswith("/v1/domains") else super().__call__(req)


def deployed(make, zone_status=None):
    c, app, deps = make(Liara(zone_status))
    register(c)
    connect(c)
    pid, rid = deploy_and_approve(c, "blog")
    return c, deps, pid, rid


def test_a_domain_needs_a_running_app(make):
    c, _, _ = make(Liara())
    register(c)
    connect(c)
    pid = c.post("/api/projects", data={"name": "fresh"}, files={"file": ("p.zip", __import__("test_web").dockerfile_zip(), "application/zip")}).json()["id"]
    r = c.get(f"/api/projects/{pid}/domains")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_live_app"


def test_plan_then_connect_then_check_then_ssl_then_detach(make):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    plan = c.post(base + "/plan", json={"domain": "https://www.MyDomain.com/"}).json()
    assert plan["domain"] == "mydomain.com" and plan["app"] == "blog-web" and plan["mode"] == "own_dns"
    assert [s["code"] for s in plan["steps"]] == ["domain_attach", "records_manual", "ssl_enable"]
    assert [x for x in deps.fake.inner.calls if x[1] == "POST"] == []  # planning created nothing

    assert c.post(base, json={"domain": "mydomain.com"}).json()["detail"]["code"] == "confirm_required"
    made = c.post(base, json={"domain": "mydomain.com", "confirm": True})
    assert made.status_code == 201
    d = made.json()
    assert d["id"] == "d1" and d["managed"] and d["created"] and len(d["records"]) == 3 and d["records"][0]["type"] == "ALIAS"
    assert c.get(base).json()["domains"][0]["managed"] is True
    assert c.get(base).json()["console_url"] == "https://console.liara.ir/apps/blog-web/domains"

    checked = c.post(base + "/d1/check").json()
    assert checked["status"] == "ACTIVE" and checked["ready"] is True
    assert c.post(base + "/d1/ssl").status_code == 200
    assert any(x[2] == "/v1/domains/provision-ssl-certs" for x in deps.fake.inner.calls)
    assert c.delete(base + "/d1").json() == {"ok": True, "records_removed": 0}


def test_errors_are_translatable_codes(make):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    bad = c.post(base + "/plan", json={"domain": "shop.liara.run"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "domain_is_liara_address"
    assert c.post(base + "/plan", json={"domain": "x.tk"}).json()["detail"] == {"code": "domain_unsupported_tld", "message": ".tk domains are not supported by Liara", "params": {"tld": "tk"}}
    deps.fake.inner.fail[("POST", "/v1/domains")] = 409
    taken = c.post(base, json={"domain": "mydomain.com", "confirm": True})
    assert taken.status_code == 409 and taken.json()["detail"]["code"] == "domain_taken"
    deps.fake.inner.fail[("GET", "/v1/domains")] = 401
    assert c.get(base).json()["detail"]["code"] == "liara_token_invalid"


def test_only_domains_this_agent_attached_can_be_changed(make):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    assert c.post(base + "/someone-elses/ssl").status_code == 403
    assert c.delete(base + "/someone-elses").json()["detail"]["code"] == "not_owned"
    # another user cannot reach this project's domains at all
    c.post("/api/auth/logout")
    register(c, "mallory")
    assert c.get(base).status_code == 404


def test_cleanup_also_detaches_the_domain_and_never_touches_the_zone(make):
    c, deps, pid, rid = deployed(make, zone_status="ACTIVE")
    base = f"/api/projects/{pid}/domains"
    d = c.post(base, json={"domain": "mydomain.com", "use_liara_dns": True, "confirm": True}).json()
    assert d["zone"]["active"] and "records_added" not in d
    r = c.post(f"/api/runs/{rid}/cleanup", json={"confirm": "blog"})
    assert r.status_code == 200 and r.json()["deleted"]["domains"] == ["mydomain.com"] and r.json()["deleted"]["apps"] == ["blog-web"]
    deletes = [(x[0], x[2]) for x in deps.fake.inner.calls if x[1] == "DELETE"]
    assert deletes == [("api", "/v1/domains/d1"), ("api", "/v1/projects/blog-web")] or ("api", "/v1/domains/d1") in deletes
    assert not any(k == "dns" for k, _ in deletes)  # the zone and its records are the user's: never deleted


def zone_with_liara_records(fake, label="shop"):
    fake.records[:] = [
        {"id": "r1", "type": "ALIAS", "name": f"{label}.mydomain.com", "contents": [{"host": "hotaka.liara.cloud"}]},
        {"id": "r2", "type": "CNAME", "name": f"_acme-challenge.{label}", "contents": [{"host": f"{label}-mydomain-com._acme-challenge.liara.cloud"}]},
        {"id": "r3", "type": "TXT", "name": f"liara-challenge.{label}", "contents": [{"text": "4a2ce85d-a3e7-4b75-a1cb-20daa648ad6a"}]},
        {"id": "own1", "type": "CNAME", "name": "www", "contents": [{"host": "site.example.org"}]},        # the developer's own records stay
        {"id": "own2", "type": "A", "name": "@", "contents": [{"ip": "1.2.3.4"}]},
    ]


def test_removing_a_domain_can_also_clean_up_the_records_liara_added_but_only_those(make):
    c, deps, pid, _ = deployed(make, zone_status="ACTIVE")
    base = f"/api/projects/{pid}/domains"
    c.post(base, json={"domain": "shop.mydomain.com", "use_liara_dns": True, "confirm": True})
    zone_with_liara_records(deps.fake.inner)
    plan = c.get(base + "/d1/removal").json()
    assert plan["domain"] == "shop.mydomain.com" and plan["zone"]["name"] == "mydomain.com"
    assert sorted(r["purpose"] for r in plan["records"]) == ["acme", "ownership", "route"] and "own1" not in str(plan)
    # a route record that points somewhere else is the developer's own, even with the same name
    deps.fake.inner.records.append({"id": "own3", "type": "CNAME", "name": "shop", "contents": [{"host": "elsewhere.example.org"}]})
    assert "own3" not in str(c.get(base + "/d1/removal").json())
    calls_before = len(deps.fake.inner.calls)
    r = c.delete(base + "/d1?remove_records=true")
    assert r.json() == {"ok": True, "records_removed": 3}
    deleted = [x[2] for x in deps.fake.inner.calls[calls_before:] if x[1] == "DELETE"]
    assert "/v1/domains/d1" in deleted and sorted(d.rsplit("/", 1)[1] for d in deleted if d.startswith("/api/v1/zones/")) == ["r1", "r2", "r3"]


def test_without_the_option_no_dns_record_is_touched(make):
    c, deps, pid, _ = deployed(make, zone_status="ACTIVE")
    base = f"/api/projects/{pid}/domains"
    c.post(base, json={"domain": "shop.mydomain.com", "use_liara_dns": True, "confirm": True})
    zone_with_liara_records(deps.fake.inner)
    assert c.delete(base + "/d1").json() == {"ok": True, "records_removed": 0}
    assert not [x for x in deps.fake.inner.calls if x[0] == "dns" and x[1] == "DELETE"]


def test_domains_on_other_dns_have_nothing_to_clean_up(make):
    c, deps, pid, _ = deployed(make)  # no Liara zone at all
    base = f"/api/projects/{pid}/domains"
    c.post(base, json={"domain": "shop.mydomain.com", "confirm": True})
    plan = c.get(base + "/d1/removal").json()
    assert plan["zone"] is None and plan["records"] == []
    assert c.delete(base + "/d1?remove_records=true").json() == {"ok": True, "records_removed": 0}


def test_dns_check_reports_what_nameservers_and_resolvers_say(make, monkeypatch):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    c.post(base, json={"domain": "shop.mydomain.com", "confirm": True})
    seen = {}
    def fake_check(domain, zone, records):
        seen.update(domain=domain, zone=zone, purposes=sorted(r["purpose"] for r in records))
        return {"zone": zone, "nameservers": ["ns1.liara.zone"], "verdict": "propagating",
                "records": [{"type": "CNAME", "name": "shop", "fqdn": "shop.mydomain.com", "purpose": "route", "authoritative": {"ns1.liara.zone": "ok"}, "public": {"Google": "ok", "Cloudflare": "missing"}, "local": "missing"}]}
    monkeypatch.setattr("liara_agent.web.app.dnscheck.check_domain", fake_check)
    out = c.get(base + "/d1/dns-check").json()
    assert out["verdict"] == "propagating" and out["records"][0]["public"]["Cloudflare"] == "missing"
    assert seen["domain"] == "shop.mydomain.com" and seen["zone"] == "mydomain.com"
    assert c.get(base + "/nope/dns-check").json()["detail"]["code"] == "domain_not_found"


def test_connecting_with_www_attaches_both(make):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    assert c.post(base + "/plan", json={"domain": "mydomain.com", "www": True}).json()["steps"][-2]["code"] == "www_redirect"
    assert c.post(base, json={"domain": "mydomain.com", "www": True, "confirm": True}).status_code == 201
    assert [d["name"] for d in c.get(base).json()["domains"]] == ["mydomain.com", "www.mydomain.com"]


def test_the_registrar_guide_for_a_domain_attached_to_the_app(make):
    c, deps, pid, _ = deployed(make)
    base = f"/api/projects/{pid}/domains"
    assert c.get(base + "/guide").json() == {"guides": []}
    assert c.post(base, json={"domain": "shop.ir", "confirm": True}).status_code == 201
    g = c.get(base + "/guide?domain=SHOP.ir").json()["guides"]
    assert len(g) == 1 and g[0]["kind"] == "irnic" and g[0]["panel_url"].startswith("https://new.nic.ir/") and g[0]["steps"][0]["code"] == "irnic_login"
    assert g[0]["mode"] == "own_dns" and g[0]["records"] and g[0]["steps"][-3]["code"] == "records_at_provider"
    assert c.get(base + "/guide?domain=other.ir").json() == {"guides": []}
