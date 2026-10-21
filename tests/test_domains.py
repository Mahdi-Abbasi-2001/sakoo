import json

import httpx
import pytest

from liara_agent.domains import DomainError, connect_domain, extract_records, normalize, plan_domain, refresh_domain, zone_for
from liara_agent.liara.client import DNS_URL, Journal, LiaraAPIError, LiaraClient, NotOwnedError

# what a Liara domain object might look like; the public spec does not show where the records sit, so the extractor must not depend on one shape
DOMAIN = {"_id": "d1", "name": "mydomain.com", "type": "PROJECT", "status": "PENDING", "certificatesStatus": "PENDING",
          "records": [{"type": "ALIAS/CNAME", "name": "@", "value": "shop.liara.run"},
                      {"type": "TXT", "name": "_liara.mydomain.com", "value": "liara-verify=abc123"},
                      {"type": "CNAME", "name": "_acme-challenge.mydomain.com", "value": "x.acme.liara.ir"}]}


class FakeLiara:
    def __init__(self, zone_status=None):
        self.domains, self.zone_status, self.records, self.calls, self.fail = [], zone_status, [], [], {}

    def api(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.calls.append(("api", m, path, json.loads(req.content) if req.content else None))
        if (m, path) in self.fail:
            return httpx.Response(self.fail[(m, path)], text="nope")
        if path == "/v1/domains" and m == "GET":
            return httpx.Response(200, json={"domains": self.domains})
        if path == "/v1/domains" and m == "POST":
            body = json.loads(req.content)
            did = f"d{len(self.domains) + 1}"
            self.domains.append({**DOMAIN, "name": body["name"], "_id": did})
            return httpx.Response(201, json={"domain": {"_id": did}})
        if m == "POST" and path.endswith("/set-redirect"):
            body = json.loads(req.content)
            next(d for d in self.domains if d["_id"] == path.split("/")[3])["redirectTo"] = body["redirectTo"]
            return httpx.Response(200, json={})
        if path.endswith("/check"):
            stored = next((d for d in self.domains if d["_id"] == path.split("/")[3]), DOMAIN)
            return httpx.Response(200, json={"domain": {**stored, "status": "ACTIVE"}})
        if path == "/v1/domains/provision-ssl-certs":
            return httpx.Response(200, json={"code": "ok"})
        if m == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def dns(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        self.calls.append(("dns", m, path, json.loads(req.content) if req.content else None))
        if path == "/api/v1/zones/mydomain.com" and m == "GET":
            return httpx.Response(404) if self.zone_status is None else httpx.Response(200, json={"data": {"name": "mydomain.com", "status": self.zone_status, "nameServers": ["ns1.liara.ir", "ns2.liara.ir"]}})
        if path == "/api/v1/zones" and m == "POST":
            self.zone_status = "PENDING"
            return httpx.Response(201, json={"data": {"name": "mydomain.com", "status": "PENDING", "nameServers": ["ns1.liara.ir", "ns2.liara.ir"]}})
        if path.endswith("/dns-records") and m == "GET":
            return httpx.Response(200, json={"data": self.records})
        if path.endswith("/dns-records") and m == "POST":
            body = json.loads(req.content)
            self.records.append({"id": f"r{len(self.records)}", "type": body["type"], "name": body["name"]})
            return httpx.Response(201, json={"data": {"id": f"r{len(self.records) - 1}"}})
        if path.endswith("/check"):
            return httpx.Response(406 if self.fail.get("busy") else 201, json={"data": "submitted"})
        if m == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)


def make(fake, owned=("shop",)):
    j = Journal()
    j.apps = list(owned)
    return LiaraClient("tok", dry_run=False, journal=j, http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake.api)),
                       dns_http=httpx.Client(base_url=DNS_URL, transport=httpx.MockTransport(fake.dns)))


@pytest.mark.parametrize("raw,name", [("MyDomain.com", "mydomain.com"), ("https://www.mydomain.com/path?x=1", "mydomain.com"), ("shop.mydomain.co.ir", "shop.mydomain.co.ir"), ("  mydomain.com.  ", "mydomain.com")])
def test_normalize(raw, name):
    assert normalize(raw) == name


@pytest.mark.parametrize("raw,code", [("", "domain_invalid"), ("localhost", "domain_invalid"), ("1.2.3.4", "domain_invalid"), ("my domain.com", "domain_invalid"),
                                      ("shop.liara.run", "domain_is_liara_address"), ("site.tk", "domain_unsupported_tld")])
def test_normalize_rejects(raw, code):
    with pytest.raises(DomainError) as e:
        normalize(raw)
    assert e.value.code == code


def test_zone_is_the_registrable_domain():
    assert zone_for("mydomain.com") == "mydomain.com" and zone_for("shop.mydomain.com") == "mydomain.com"
    assert zone_for("shop.mydomain.co.ir") == "mydomain.co.ir" and zone_for("a.b.mydomain.ir") == "mydomain.ir"
    assert zone_for("shop.mydomain.com", "shop.mydomain.com") == "shop.mydomain.com"  # an explicit zone wins


def test_records_are_found_wherever_liara_puts_them():
    assert [(r["type"], r["name"]) for r in extract_records(DOMAIN)] == [("ALIAS", "@"), ("TXT", "_liara.mydomain.com"), ("CNAME", "_acme-challenge.mydomain.com")]
    nested = {"domain": {"name": "x.com", "type": "PROJECT", "dns": {"txt": {"type": "TXT", "host": "_v", "content": "abc"}, "route": [{"recordType": "CNAME", "name": "www", "target": "t.liara.run"}]}}}
    found = extract_records(nested)
    assert {(r["type"], r["value"]) for r in found} == {("TXT", "abc"), ("CNAME", "t.liara.run")}
    assert extract_records({"name": "x.com", "type": "PROJECT", "status": "ACTIVE"}) == []  # the domain's own `type` is not a record


def test_plan_is_read_only_and_lists_the_steps():
    fake = FakeLiara()
    plan = plan_domain(make(fake), "shop", "mydomain.com", use_liara_dns=True)
    assert [s["code"] for s in plan["steps"]] == ["zone_create", "zone_nameservers", "domain_attach", "records_added", "ssl_enable"]
    assert all(c[1] == "GET" for c in fake.calls)  # nothing was created
    own = plan_domain(make(FakeLiara()), "shop", "mydomain.com")
    assert [s["code"] for s in own["steps"]] == ["domain_attach", "records_manual", "ssl_enable"] and own["zone"] is None


def test_own_dns_attaches_the_domain_and_returns_the_records_to_set():
    fake = FakeLiara()
    client = make(fake)
    view = connect_domain(client, "shop", "mydomain.com")
    assert view["created"] and view["id"] == "d1" and len(view["records"]) == 3 and view["zone"] is None
    assert [c for c in fake.calls if c[0] == "dns"] == []  # Liara's DNS service is not touched
    assert client.journal.domains == [{"id": "d1", "name": "mydomain.com", "app": "shop"}]
    again = connect_domain(client, "shop", "mydomain.com")  # idempotent
    assert not again["created"] and len(client.journal.domains) == 1


def test_liara_dns_creates_the_zone_and_waits_for_the_nameservers():
    fake = FakeLiara()  # no zone yet
    view = connect_domain(make(fake), "shop", "mydomain.com", use_liara_dns=True)
    assert view["zone"]["status"] == "PENDING" and not view["zone"]["active"] and view["zone"]["name_servers"] == ["ns1.liara.ir", "ns2.liara.ir"]
    assert fake.records == []  # the agent writes no DNS records; Liara adds them itself once the zone is active


def test_on_an_active_liara_zone_liara_adds_the_records_so_the_agent_writes_none():
    fake = FakeLiara(zone_status="ACTIVE")
    client = make(fake)
    view = connect_domain(client, "shop", "mydomain.com", use_liara_dns=True)
    assert view["zone"]["active"] and len(view["records"]) == 3
    assert [c for c in fake.calls if c[0] == "dns" and c[1] in ("POST", "PUT", "DELETE")] == []  # nothing is written to anybody's DNS
    assert client.journal.records == []


def test_a_domain_that_belongs_to_another_app_is_reported_clearly():
    fake = FakeLiara()
    fake.fail[("POST", "/v1/domains")] = 409
    with pytest.raises(DomainError) as e:
        connect_domain(make(fake), "shop", "mydomain.com")
    assert e.value.code == "domain_taken" and e.value.status == 409


def test_only_apps_and_domains_this_agent_created_can_be_changed():
    fake = FakeLiara()
    with pytest.raises(NotOwnedError):
        connect_domain(make(fake, owned=()), "someone-elses-app", "mydomain.com")
    client = make(fake)
    with pytest.raises(NotOwnedError):
        client.enable_ssl("not-ours")
    with pytest.raises(NotOwnedError):
        client.delete_domain("not-ours")
    with pytest.raises(NotOwnedError):
        client.delete_record("mydomain.com", "r-not-ours")


def test_ssl_is_requested_by_name_then_by_id_if_refused():
    fake = FakeLiara()
    client = make(fake)
    connect_domain(client, "shop", "mydomain.com")
    client.enable_ssl("d1")
    ssl = [c[3] for c in fake.calls if c[2] == "/v1/domains/provision-ssl-certs"]
    assert ssl == [{"domain": "mydomain.com"}]
    # refused once -> the id is tried
    n = {"i": 0}
    real = fake.api

    def flaky(req):
        if req.url.path == "/v1/domains/provision-ssl-certs":
            n["i"] += 1
            if n["i"] == 1:
                return httpx.Response(400, text="bad")
        return real(req)
    client._http = httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(flaky))
    client.enable_ssl("d1")
    assert n["i"] == 2


def test_refresh_reports_status_and_nudges_a_pending_zone():
    fake = FakeLiara(zone_status="PENDING")
    client = make(fake)
    connect_domain(client, "shop", "mydomain.com", use_liara_dns=True)
    view = refresh_domain(client, "shop", "d1")
    assert view["status"] == "ACTIVE" and view["ready"] and view["zone_check"] == "checked"
    fake.fail["busy"] = True
    assert refresh_domain(client, "shop", "d1")["zone_check"] == "busy"


def test_dry_run_sends_nothing():
    fake = FakeLiara()
    j = Journal()
    j.apps = ["shop"]
    c = LiaraClient("tok", dry_run=True, journal=j, http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake.api)),
                    dns_http=httpx.Client(base_url=DNS_URL, transport=httpx.MockTransport(fake.dns)))
    c.create_domain("shop", "mydomain.com")
    c.create_zone("mydomain.com")
    assert fake.calls == [] and [a.op for a in c.actions] == ["create_domain", "create_zone"] and j.domains == []


def test_liara_s_real_domain_object_gives_statuses_not_values():
    # the list answer of a real domain (nodejs.mydomain.ir): the *Record fields are VERIFICATION STATUSES, as the console's red/green checks show
    listed = {"_id": "6ad62abe84eee4f3945db173", "name": "nodejs.mydomain.ir", "type": "PROJECT", "status": "ACTIVE", "certificatesStatus": "REQUESTING", "behindCdn": False,
              "isWildcard": False, "CNameRecord": "INVALID", "acmeRecord": "VALID"}
    recs = {r["purpose"]: r for r in extract_records(listed, route_target="hotaka.liara.cloud")}
    assert recs["route"] == {"type": "CNAME", "name": "nodejs", "value": "hotaka.liara.cloud", "purpose": "route", "status": "INVALID", "derived": True}
    assert recs["acme"]["name"] == "_acme-challenge.nodejs" and recs["acme"]["status"] == "VALID"
    assert recs["acme"]["value"] == "nodejs-mydomain-ir._acme-challenge.liara.cloud" and recs["acme"]["derived"] is False  # the pattern seen on two real domains
    assert recs["ownership"]["name"] == "liara-challenge.nodejs" and recs["ownership"]["value"] == ""  # only the check answer has the code
    # the check answer adds verificationCode
    checked = {**listed, "verificationCode": "4a2ce85d-a3e7-4b75-a1cb-20daa648ad6a", "punycodeName": "nodejs.mydomain.ir"}
    txt = next(r for r in extract_records(checked) if r["purpose"] == "ownership")
    assert txt["value"] == "4a2ce85d-a3e7-4b75-a1cb-20daa648ad6a" and txt["derived"] is False
    # a bare domain needs an ALIAS, and the challenge names have no label
    apex = {r["purpose"]: r for r in extract_records({"name": "mydomain.com", "CNameRecord": "VALID", "acmeRecord": "VALID", "verificationCode": "u"})}
    assert (apex["route"]["type"], apex["route"]["name"], apex["acme"]["name"], apex["ownership"]["name"]) == ("ALIAS", "@", "_acme-challenge", "liara-challenge")
    # a value that is a host name (not a status word) is taken as the value
    given = {r["purpose"]: r for r in extract_records({"name": "mydomain.com", "CNameRecord": "t.liara.cloud", "acmeRecord": "x.acme.liara.cloud"})}
    assert given["route"]["value"] == "t.liara.cloud" and given["route"]["status"] is None and not given["route"]["derived"] and not given["acme"]["derived"]


def test_a_domain_is_ready_only_when_no_record_is_invalid():
    from liara_agent.domains import domain_view
    base = {"name": "nodejs.mydomain.ir", "status": "ACTIVE", "certificatesStatus": "ACTIVE", "acmeRecord": "VALID"}
    assert domain_view({**base, "CNameRecord": "INVALID"})["ready"] is False  # ACTIVE alone is what the console calls "needs records check"
    v = domain_view({**base, "CNameRecord": "VALID"})
    assert v["ready"] is True and v["ssl_active"] is True


def test_www_is_attached_too_and_redirected_to_the_bare_domain_once():
    fake = FakeLiara()
    client = make(fake)
    plan = plan_domain(client, "shop", "mydomain.com", www=True)
    assert [x["code"] for x in plan["steps"]] == ["domain_attach", "records_manual", "www_redirect", "ssl_enable"]
    connect_domain(client, "shop", "mydomain.com", www=True)
    names = [d["name"] for d in fake.domains]
    assert names == ["mydomain.com", "www.mydomain.com"]
    redirects = [c for c in fake.calls if c[2].endswith("/set-redirect")]
    assert [(c[2], c[3]) for c in redirects] == [("/v1/domains/d2/set-redirect", {"redirectStatus": 301, "redirectTo": "mydomain.com"})]
    assert [d["id"] for d in client.journal.domains] == ["d1", "d2"]
    connect_domain(client, "shop", "mydomain.com", www=True)  # again: nothing is created or redirected twice
    assert len(fake.domains) == 2 and len([c for c in fake.calls if c[2].endswith("/set-redirect")]) == 1


def test_redirects_are_only_set_on_domains_this_agent_attached():
    import pytest
    from liara_agent.liara.client import NotOwnedError
    with pytest.raises(NotOwnedError):
        make(FakeLiara()).set_domain_redirect("not-ours", "mydomain.com")


def test_the_cli_refuses_a_project_without_an_app_this_agent_created(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from liara_agent.cli import app as cli
    monkeypatch.setenv("LIARA_API_TOKEN", "tok")
    r = CliRunner().invoke(cli, ["domain", "add", "mydomain.com", str(tmp_path)])
    assert r.exit_code == 1 and "no app created by this agent" in r.output
    r = CliRunner().invoke(cli, ["domain", "remove", "mydomain.com", str(tmp_path)])
    assert r.exit_code == 1
