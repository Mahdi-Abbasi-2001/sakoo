from liara_agent.dnscheck import PUBLIC_RESOLVERS, LookupFailed, check_domain, nameservers_of, verdict

NS = {"ns1.liara.zone": "10.0.0.1", "ns2.liara.zone": "10.0.0.2"}
TARGET_IP = "185.208.181.160"
RECORDS = [{"type": "CNAME", "name": "nodejs", "value": "hotaka.liara.cloud", "purpose": "route"},
           {"type": "CNAME", "name": "_acme-challenge.nodejs", "value": "nodejs-mydomain-ir._acme-challenge.liara.cloud", "purpose": "acme"},
           {"type": "TXT", "name": "liara-challenge.nodejs", "value": "4a2ce85d-a3e7-4b75-a1cb-20daa648ad6a", "purpose": "ownership"}]


class Dns:
    """A tiny fake of the DNS: what each server answers. `stale` servers still say the route does not exist (a cached NXDOMAIN)."""

    def __init__(self, stale=(), authoritative_missing=(), broken=()):
        self.stale, self.auth_missing, self.broken = set(stale), set(authoritative_missing), set(broken)

    def __call__(self, server, name, rtype):
        if server in self.broken:
            raise LookupFailed("timeout")
        if rtype == "NS" and name == "mydomain.ir":
            return list(NS)
        if rtype == "A" and name in NS:
            return [NS[name]]
        if rtype == "A" and name == "hotaka.liara.cloud":
            return [TARGET_IP]
        is_auth = server in NS.values()
        if name == "nodejs.mydomain.ir":
            if is_auth and "route" in self.auth_missing or (not is_auth and server in self.stale):
                return []
            return [TARGET_IP] if rtype == "A" else []  # flattened ALIAS: answered as an address
        if name == "_acme-challenge.nodejs.mydomain.ir" and rtype == "CNAME":
            return [] if is_auth and "acme" in self.auth_missing else ["nodejs-mydomain-ir._acme-challenge.liara.cloud"]
        if name == "liara-challenge.nodejs.mydomain.ir" and rtype == "TXT":
            return ["4a2ce85d-a3e7-4b75-a1cb-20daa648ad6a"]
        return []


def check(dns):
    return check_domain("nodejs.mydomain.ir", "mydomain.ir", RECORDS, look=dns)


def test_everything_right_everywhere():
    out = check(Dns())
    assert out["verdict"] == "all_good" and out["nameservers"] == list(NS)
    route = next(r for r in out["records"] if r["purpose"] == "route")
    assert set(route["authoritative"].values()) == {"ok"} and set(route["public"]) == set(PUBLIC_RESOLVERS)


def test_right_at_the_source_but_a_public_resolver_still_caches_the_old_answer():
    # exactly what happened on the first real domain: Liara's nameservers answered, a resolver still said NXDOMAIN
    dns = Dns(stale={"1.1.1.1"})
    out = check(dns)
    route = next(r for r in out["records"] if r["purpose"] == "route")
    assert out["verdict"] == "propagating" and route["public"]["Cloudflare"] == "missing" and route["public"]["Google"] == "ok" and set(route["authoritative"].values()) == {"ok"}


def test_a_record_missing_at_the_nameservers_is_not_a_waiting_problem():
    out = check(Dns(authoritative_missing={"route"}))
    assert out["verdict"] == "missing"


def test_a_domain_whose_nameservers_are_not_liaras_is_told_so():
    class Elsewhere(Dns):
        def __call__(self, server, name, rtype):
            if rtype == "NS":
                return ["ns1.other-dns.com"]
            if rtype == "A" and name == "ns1.other-dns.com":
                return ["10.9.9.9"]
            return [] if server == "10.9.9.9" else super().__call__(server, name, rtype)
    assert check(Elsewhere())["verdict"] == "not_at_liara"


def test_a_value_that_points_elsewhere_is_wrong_not_missing():
    class Wrong(Dns):
        def __call__(self, server, name, rtype):
            if name == "_acme-challenge.nodejs.mydomain.ir" and rtype == "CNAME":
                return ["somewhere.else.example"]
            return super().__call__(server, name, rtype)
    assert check(Wrong())["verdict"] == "wrong_value"


def test_no_nameservers_and_no_answers():
    class Nothing(Dns):
        def __call__(self, server, name, rtype):
            if rtype == "NS":
                return []
            return super().__call__(server, name, rtype)
    assert check(Nothing())["verdict"] == "no_nameservers"
    class Dead(Dns):
        def __call__(self, server, name, rtype):
            if rtype == "NS" and server is None and name == "mydomain.ir":
                return list(NS)
            if server is not None:
                raise LookupFailed("timeout")
            return super().__call__(server, name, rtype)
    assert check(Dead())["verdict"] == "unknown"


def test_nameservers_of_tolerates_a_failing_lookup():
    def boom(server, name, rtype):
        raise LookupFailed("down")
    assert nameservers_of("mydomain.ir", boom) == ([], {})
    assert verdict([], {}, []) == "no_nameservers"
