import json

import httpx
import pytest
from test_heal import READY, FakeLiara

from liara_agent.deploy import executor
from liara_agent.deploy.cleanup import remove_all
from liara_agent.deploy.decisions import decide_mail, decide_storage, decide_wordpress, wordpress_estimates
from liara_agent.deploy.executor import execute
from liara_agent.deploy.provision import dns_records, provision_bucket, provision_mail
from liara_agent.deploy.services import (bucket_name, mail_id, probe_endpoint, substitute_service_vars, wire_mail_env, wire_storage_env)
from liara_agent.detect.wordpress import looks_like_wordpress
from liara_agent.liara.client import Journal, LiaraAPIError, LiaraClient, NotOwnedError
from liara_agent.liara.live import snapshot_catalog
from liara_agent.liara.pricing import choose_mail_plan, choose_storage_plan, estimate_plan, mail_cost, storage_cost
from liara_agent.plan.model import AppSpec, DeployPlan, MailSpec, StorageSpec

CAT = snapshot_catalog()
SERVER_ID = "a" * 24
DNS_SETUP = {"spf": {"spf_record": "v=spf1 mx include:spf.c1.liara.email ~all", "spf_status": "VALID"},
             "dkim": {"dkim_record": "k=rsa; p=ABC", "dkim_record_name": "mail._domainkey", "dkim_status": "INVALID"},
             "mx_record": {"mx_record_value": "c1.liara.email", "mx_status": "VALID"},
             "return_path": {"return_path_domain": "rp.mydomain.com", "return_path_value": "rp.c1.liara.email", "return_path_status": "INVALID"}}


class Services:
    """Liara's object-storage and mail hosts."""

    def __init__(self, taken=(), key_needs_id=False):
        self.buckets, self.keys, self.mails, self.smtp, self.accounts, self.calls = [], [], [], [], [], []
        self.taken, self.key_needs_id = set(taken), key_needs_id

    def storage(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append(("storage", m, path, body))
        if (m, path) == ("GET", "/api/v1/buckets"):
            return httpx.Response(200, json={"buckets": [{"id": f"id-{b}", "name": b} for b in self.buckets]})
        if (m, path) == ("POST", "/api/v1/buckets"):
            if body["name"] in self.taken:
                return httpx.Response(409, text="taken")
            self.buckets.append(body["name"])
            return httpx.Response(201, json={"status": "success"})
        if (m, path) == ("POST", "/api/v1/keys"):
            item = body["buckets"][0]
            if sorted(body.get("permissions") or []) != ["delete", "list", "put", "read"]:  # the real server requires them
                return httpx.Response(400, text='"permissions" is required')
            if not isinstance(item, str):  # the real server: "buckets[0]" must be a string
                return httpx.Response(400, text='"buckets[0]" must be a string')
            if self.key_needs_id and not item.startswith("id-"):
                return httpx.Response(400, text="bad bucket")
            self.keys.append({"id": "k1", "accessKey": "AKIA-TEST"})
            return httpx.Response(201, json={"status": "success", "data": {"accessKey": "AKIA-TEST", "secretKey": "SECRET-TEST"}})
        if (m, path) == ("GET", "/api/v1/keys"):
            return httpx.Response(200, json={"keys": self.keys})
        if m == "DELETE" and path.startswith("/api/v1/keys/"):
            ident = path.rsplit("/", 1)[1]
            if not any(k["accessKey"] == ident for k in self.keys):   # the real endpoint takes the ACCESS KEY: the id answered 404
                return httpx.Response(404)
            self.keys = [k for k in self.keys if k["accessKey"] != ident]
            return httpx.Response(204)
        if m == "DELETE" and path.startswith("/api/v1/buckets/"):
            if self.keys:
                return httpx.Response(400, text="bucket still has keys")  # Liara's rule: keys first
            self.buckets.remove(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        return httpx.Response(404)

    def mail(self, req: httpx.Request) -> httpx.Response:
        path, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else None
        self.calls.append(("mail", m, path, body))
        if (m, path) == ("POST", "/api/v1/mails"):
            self.mails.append(body["domain"])
            return httpx.Response(201, json={"status": "success"})
        if (m, path) == ("GET", "/api/v1/mails"):
            return httpx.Response(200, json={"data": {"mailServers": [{"id": SERVER_ID, "domain": d} for d in self.mails]}})
        if (m, path) == ("GET", f"/api/v1/mails/{SERVER_ID}"):
            return httpx.Response(200, json={"data": {"mailServer": {"id": SERVER_ID, "domain": self.mails[0], "smtp_server": "smtp.c1.liara.email", "smtp_port": 465, "dns_setup": DNS_SETUP}}})
        if (m, path) == ("POST", f"/api/v1/mails/{SERVER_ID}/accounts"):
            self.accounts.append(body["name"])
            return httpx.Response(201, json={"status": "success"})
        if (m, path) == ("POST", f"/api/v1/mails/{SERVER_ID}/smtp-credentials"):
            self.smtp.append("u1")
            return httpx.Response(201, json={"status": "success", "data": {"username": "u1", "passowrd": "pw-TEST"}})  # sic: the API's own spelling
        if m == "DELETE" and "/smtp-credentials/" in path:
            self.smtp.remove(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if m == "DELETE" and path == f"/api/v1/mails/{SERVER_ID}":
            if self.smtp:
                return httpx.Response(400, text="delete the SMTP users first")
            self.mails.clear()
            return httpx.Response(204)
        return httpx.Response(404)


def client_for(svc, deploy=None, dry=False):
    deploy = deploy or FakeLiara([READY])
    return LiaraClient("tok", dry_run=dry, journal=Journal(), http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(deploy)),
                       storage_http=httpx.Client(base_url="https://storage-service.liara.ir", transport=httpx.MockTransport(svc.storage)),
                       mail_http=httpx.Client(base_url="https://mail-service.liara.ir", transport=httpx.MockTransport(svc.mail)))


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))
    monkeypatch.setattr("liara_agent.deploy.provision.probe_endpoint", lambda b, **kw: ("https://storage.iran.liara.site", True))


# ---- plans and prices ----
def test_cheapest_plans_and_their_monthly_prices():
    assert choose_storage_plan(CAT) == "20g" and storage_cost(CAT, "20g").monthly == 475000  # the API-documented id wins a price tie with 20g-g2
    assert choose_mail_plan(CAT) == "m2" and mail_cost(CAT, "m2").monthly == 1475000
    plan = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1)], storage=[StorageSpec(id="a-files")], mail=MailSpec(domain="mydomain.com"))
    lines = {l.kind: l for l in estimate_plan(CAT, plan)}
    assert {"app", "bucket", "mail"} == set(lines) and round(lines["bucket"].cost.monthly_with_vat) == 522500 and round(lines["mail"].cost.monthly_with_vat) == 1622500


# ---- placeholders and wiring ----
def test_placeholders_are_filled_and_unknown_ones_left_alone():
    env = {"B": "$S3_shop-files_BUCKET", "H": "$MAIL_my-com_HOST", "X": "$S3_other_BUCKET", "P": "plain"}
    out = substitute_service_vars(env, {"S3:shop-files": {"BUCKET": "shop-files"}, "MAIL:my-com": {"HOST": "smtp.x"}})
    assert out == {"B": "shop-files", "H": "smtp.x", "X": "$S3_other_BUCKET", "P": "plain"}


def test_storage_uses_the_names_the_code_reads_and_fills_the_rest_with_liaras_own():
    plan = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=3000)])
    spec = StorageSpec(id="shop-files")
    names = wire_storage_env(plan, spec, {"AWS_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "PORT"})
    env = plan.apps[0].env
    assert env["AWS_BUCKET"] == "$S3_shop-files_BUCKET" and env["AWS_ACCESS_KEY_ID"] == "$S3_shop-files_ACCESS_KEY" and env["AWS_SECRET_ACCESS_KEY"] == "$S3_shop-files_SECRET_KEY"
    assert env["LIARA_ENDPOINT"] == "$S3_shop-files_ENDPOINT"  # the code names no endpoint variable: a client cannot work without one
    assert set(names) == set(env)


def test_storage_without_any_hint_uses_liaras_documented_names_or_laravels_aws_style():
    node = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1)])
    wire_storage_env(node, StorageSpec(id="a-files"), set())
    assert set(node.apps[0].env) == {"LIARA_ACCESS_KEY", "LIARA_SECRET_KEY", "LIARA_BUCKET_NAME", "LIARA_ENDPOINT"}
    php = DeployPlan(apps=[AppSpec(name="a", platform="laravel", port=1)])
    wire_storage_env(php, StorageSpec(id="a-files"), set())
    assert php.apps[0].env["AWS_USE_PATH_STYLE_ENDPOINT"] == "true" and php.apps[0].env["AWS_DEFAULT_REGION"] == "$S3_a-files_REGION"


def test_a_value_the_plan_set_on_purpose_is_not_overwritten():
    plan = DeployPlan(apps=[AppSpec(name="a", platform="node", port=1, env={"AWS_BUCKET": "my-own-bucket"})])
    wire_storage_env(plan, StorageSpec(id="a-files"), {"AWS_BUCKET"})
    assert plan.apps[0].env["AWS_BUCKET"] == "my-own-bucket"


def test_mail_uses_the_names_the_code_reads():
    plan = DeployPlan(apps=[AppSpec(name="a", platform="django", port=1)])
    wire_mail_env(plan, MailSpec(domain="mydomain.com"), {"EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD", "DEFAULT_FROM_EMAIL", "EMAIL_PORT"})
    assert plan.apps[0].env == {"EMAIL_HOST": "$MAIL_mydomain-com_HOST", "EMAIL_PORT": "$MAIL_mydomain-com_PORT", "EMAIL_HOST_USER": "$MAIL_mydomain-com_USER",
                                "EMAIL_HOST_PASSWORD": "$MAIL_mydomain-com_PASSWORD", "DEFAULT_FROM_EMAIL": "$MAIL_mydomain-com_FROM"}


def test_bucket_names_are_valid_and_readable():
    assert bucket_name("Blog_Web") == "blog-web-files" and bucket_name("x") == "x-files" and len(bucket_name("a" * 80)) <= 40 and mail_id("my.domain.com") == "my-domain-com"


def test_the_endpoint_is_probed_not_assumed():
    seen = []
    def get(url):
        seen.append(url)
        return httpx.Response(403 if "c2" in url else 404)  # only the second candidate knows this bucket
    assert probe_endpoint("shop-files", get=get, sleep=lambda s: None, attempts=2) == ("https://storage.c2.liara.site", True)
    assert probe_endpoint("shop-files", get=lambda u: httpx.Response(404), sleep=lambda s: None, attempts=2) == ("https://storage.iran.liara.site", False)  # unconfirmed


# ---- creating and deleting ----
def test_a_bucket_and_its_key_are_created_and_journaled():
    svc = Services()
    c = client_for(svc)
    values, follow = provision_bucket(c, CAT, StorageSpec(id="shop-files"), lambda k, d: None)
    assert values["ACCESS_KEY"] == "AKIA-TEST" and values["SECRET_KEY"] == "SECRET-TEST" and values["BUCKET"] == "shop-files" and follow == []
    assert c.journal.buckets == ["shop-files"] and c.journal.keys == [{"id": "k1", "access": "AKIA-TEST", "bucket": "shop-files"}]
    create = next(x for x in svc.calls if x[:3] == ("storage", "POST", "/api/v1/buckets"))
    assert create[3] == {"name": "shop-files", "plan": "20g", "permission": "private"}  # private unless chosen otherwise


def test_a_taken_bucket_name_gets_a_suffix_and_the_key_request_falls_back_to_the_id():
    svc = Services(taken={"shop-files"}, key_needs_id=True)
    c = client_for(svc)
    values, _ = provision_bucket(c, CAT, StorageSpec(id="shop-files"), lambda k, d: None)
    assert values["BUCKET"].startswith("shop-files-") and values["BUCKET"] in svc.buckets
    assert values["ACCESS_KEY"] == "AKIA-TEST"  # the first key shape (by name) was refused, the second (by id) accepted


def test_an_unconfirmed_endpoint_becomes_a_follow_up(monkeypatch):
    monkeypatch.setattr("liara_agent.deploy.provision.probe_endpoint", lambda b, **kw: ("https://storage.iran.liara.site", False))
    _, follow = provision_bucket(client_for(Services()), CAT, StorageSpec(id="shop-files"), lambda k, d: None)
    assert follow[0]["code"] == "bucket_endpoint_unconfirmed" and follow[0]["params"]["bucket"] == "shop-files"


def test_a_mail_server_gets_an_address_and_an_smtp_user_and_reports_its_dns_records():
    svc = Services()
    c = client_for(svc)
    values, follow = provision_mail(c, CAT, MailSpec(domain="mydomain.com"), lambda k, d: None)
    assert values == {"HOST": "smtp.c1.liara.email", "PORT": "465", "USER": "u1", "PASSWORD": "pw-TEST", "FROM": "noreply@mydomain.com"}
    assert svc.accounts == ["noreply"] and c.journal.mail_servers == [{"id": SERVER_ID, "domain": "mydomain.com"}] and c.journal.smtp == [{"server": SERVER_ID, "username": "u1"}]
    create = next(x for x in svc.calls if x[:3] == ("mail", "POST", "/api/v1/mails"))
    assert create[3] == {"domain": "mydomain.com", "plan": "m2", "mode": "DEV"}
    assert [f["code"] for f in follow] == ["mail_dns", "mail_dev_mode"]  # emails stay in the sent folder until the server is switched to live
    recs = {r["purpose"]: r for r in follow[0]["records"]}
    assert recs["mx"]["value"] == "10 c1.liara.email" and recs["dkim"]["status"] == "INVALID" and recs["return_path"]["type"] == "CNAME" and set(recs) == {"spf", "dkim", "mx", "return_path"}


def test_dns_records_tolerate_a_server_that_does_not_describe_them_yet():
    assert dns_records({}) == [] and dns_records({"dns_setup": {"spf": {}}}) == []


def test_only_resources_this_agent_created_can_be_changed():
    c = client_for(Services())
    for call in (lambda: c.create_key("not-ours", "x"), lambda: c.delete_bucket("not-ours"), lambda: c.delete_key("k9"), lambda: c.delete_mail_server(SERVER_ID),
                 lambda: c.create_smtp_user(SERVER_ID, "x"), lambda: c.delete_smtp_user(SERVER_ID, "u1")):
        with pytest.raises(NotOwnedError):
            call()


def test_dry_run_creates_nothing():
    svc = Services()
    c = client_for(svc, dry=True)
    provision_bucket(c, CAT, StorageSpec(id="shop-files"), lambda k, d: None)
    provision_mail(c, CAT, MailSpec(domain="mydomain.com"), lambda k, d: None)
    assert svc.calls == [] and svc.buckets == [] and c.journal.buckets == [] and {a.op for a in c.actions} >= {"create_bucket", "create_key", "create_mail_server", "create_smtp_user"}


def test_cleanup_deletes_keys_before_buckets_and_smtp_users_before_mail_servers(tmp_path):
    svc = Services()
    c = client_for(svc)
    provision_bucket(c, CAT, StorageSpec(id="shop-files"), lambda k, d: None)
    provision_mail(c, CAT, MailSpec(domain="mydomain.com"), lambda k, d: None)
    deleted: dict = {}
    remove_all(c, deleted)
    assert deleted["buckets"] == ["shop-files"] and deleted["mail_servers"] == ["mydomain.com"]
    assert svc.buckets == [] and svc.keys == [] and svc.mails == [] and svc.smtp == []
    assert c.journal.buckets == [] and c.journal.keys == [] and c.journal.mail_servers == [] and c.journal.smtp == []
    order = [x[2] for x in svc.calls if x[1] == "DELETE"]
    assert order.index("/api/v1/keys/AKIA-TEST") < order.index("/api/v1/buckets/shop-files")


# ---- the executor ----
def test_the_executor_creates_services_first_and_fills_the_apps_placeholders(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
    svc, deploy = Services(), FakeLiara([READY])
    c = client_for(svc, deploy)
    app = AppSpec(name="shop", platform="docker", port=3000, env={"AWS_BUCKET": "$S3_shop-files_BUCKET", "AWS_SECRET_ACCESS_KEY": "$S3_shop-files_SECRET_KEY", "MAIL_PASSWORD": "$MAIL_mydomain-com_PASSWORD"})
    plan = DeployPlan(apps=[app], storage=[StorageSpec(id="shop-files")], mail=MailSpec(domain="mydomain.com"))
    events = []
    out = execute(plan, CAT, c, tmp_path, network_id="n1", emit=lambda k, d: events.append((k, d)))
    assert out.ok and [f["code"] for f in out.follow_ups] == ["mail_dns", "mail_dev_mode"]
    sent = next(x[2] for x in deploy.calls if x[1].endswith("update-envs"))
    got = {v["key"]: v["value"] for v in sent["variables"]}
    assert got == {"AWS_BUCKET": "shop-files", "AWS_SECRET_ACCESS_KEY": "SECRET-TEST", "MAIL_PASSWORD": "pw-TEST"}
    kinds = [d["kind"] for k, d in events if k == "resource_created"]
    assert kinds == ["bucket", "mail", "app"]  # the services exist before the app that needs them
    # secrets never appear in an event
    assert "SECRET-TEST" not in json.dumps(events) and "pw-TEST" not in json.dumps(events)
    # nor in the recorded actions (what a preview or a log would show)
    assert "SECRET-TEST" not in json.dumps([a.body for a in c.actions]) and "pw-TEST" not in json.dumps([a.body for a in c.actions])


# ---- decisions ----
def asker(answers):
    seen = []
    def ask(text, options, **kw):
        seen.append((kw.get("code"), options, kw.get("params")))
        return answers.pop(0)
    ask.seen = seen
    return ask


HINT_S3, HINT_MAIL = {("service", "object-storage")}, {("service", "mail")}


def test_storage_is_offered_with_its_price_and_added_only_on_yes():
    plan = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
    ask = asker(["create_bucket"])
    decide_storage(plan, HINT_S3, {"AWS_BUCKET"}, CAT, ask)
    assert ask.seen == [("storage_choice", ["create_bucket", "keep_current"], {"monthly": 522500})]
    assert [s.id for s in plan.storage] == ["shop-files"] and plan.apps[0].env["AWS_BUCKET"] == "$S3_shop-files_BUCKET"
    kept = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
    decide_storage(kept, HINT_S3, set(), CAT, asker(["keep_current"]))
    assert kept.storage == [] and kept.apps[0].env == {} and kept.notes[-1].code == "storage_kept"
    none = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
    ask2 = asker([])
    decide_storage(none, set(), set(), CAT, ask2)  # no S3 client in the code: nothing is asked
    assert ask2.seen == []


def test_mail_has_three_options_and_a_domain_is_only_asked_for_when_liara_is_chosen():
    plan = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
    ask = asker(["liara_mail", "https://www.MyDomain.com/"])
    decide_mail(plan, HINT_MAIL, {"MAIL_HOST"}, CAT, ask)
    assert ask.seen[0] == ("mail_choice", ["liara_mail", "own_smtp", "skip"], {"monthly": 1622500}) and ask.seen[1][0] == "mail_domain"
    assert plan.mail.domain == "mydomain.com" and plan.mail.mode == "DEV" and plan.apps[0].env["MAIL_HOST"] == "$MAIL_mydomain-com_HOST"
    for answer, code in (("own_smtp", "mail_own_smtp"), ("skip", "mail_skipped")):
        p = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
        a = asker([answer])
        decide_mail(p, HINT_MAIL, set(), CAT, a)
        assert p.mail is None and p.notes[-1].code == code and len(a.seen) == 1
    bad = DeployPlan(apps=[AppSpec(name="shop", platform="node", port=1)])
    decide_mail(bad, HINT_MAIL, set(), CAT, asker(["liara_mail", "not a domain"]))
    assert bad.mail is None and bad.notes[-1].code == "mail_domain_invalid"


def test_wordpress_is_detected_and_both_routes_are_priced(tmp_path):
    assert looks_like_wordpress(tmp_path) is None
    (tmp_path / "wp-content").mkdir()
    assert looks_like_wordpress(tmp_path) == "wp-content"
    only_composer = tmp_path / "c"
    only_composer.mkdir()
    (only_composer / "composer.json").write_text(json.dumps({"require": {"johnpbloch/wordpress": "^6"}}))
    assert looks_like_wordpress(only_composer) == "composer.json"
    est = wordpress_estimates(CAT)
    assert est["managed"] == 4290000 and 1_000_000 < est["generic"] < 2_500_000  # the managed product costs a multiple of the generic route
    assert est["app"] > 0  # Liara's WordPress image through the API is priced too
    ask = asker(["generic_php"])
    assert decide_wordpress(CAT, "wp-content", ask) == "generic_php" and ask.seen[0][0] == "wordpress_choice" and ask.seen[0][2]["marker"] == "wp-content"
    assert ask.seen[0][1][0] == "wordpress_app"  # the route with no console steps is offered first
    assert decide_wordpress(CAT, "wp-content", asker(["managed_console"])) == "managed_console"
    assert decide_wordpress(CAT, "wp-content", asker(["wordpress_app"])) == "wordpress_app"


# ---- through the whole pipeline ----
def _project(tmp_path, extra_deps, wordpress=False):
    import shutil
    from pathlib import Path
    proj = tmp_path / "proj"
    shutil.copytree(Path(__file__).parent / "fixtures" / "express_pg", proj)
    pkg = json.loads((proj / "package.json").read_text())
    pkg.setdefault("dependencies", {}).update(extra_deps)
    (proj / "package.json").write_text(json.dumps(pkg))
    if wordpress:
        (proj / "wp-content").mkdir()
    return proj


def _call(name, **args):
    return {"content": None, "tool_calls": [{"id": f"c-{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


PLAN = {"apps": [{"name": "api", "platform": "node", "port": 3000, "evidence": [{"file": "package.json", "line": 7, "note": "start script"}]}], "env": []}


def _run(tmp_path, proj, svc, answers, execute=True):
    from liara_agent.config import Settings
    from liara_agent.deploy.pipeline import run_deployment
    from liara_agent.events import Run
    from liara_agent.interact import ScriptedChannel
    from liara_agent.llm.client import MockLLM
    run = Run()
    deploy = FakeLiara([READY])
    c = client_for(svc, deploy, dry=not execute)
    res = run_deployment(proj, "api", CAT, Settings(), c, ScriptedChannel(run, answers=answers), run, network_id="n1", execute_=execute, llm=MockLLM([_call("submit_plan", **PLAN)]))
    return res, run, c, deploy


def test_pipeline_a_storage_library_leads_to_a_question_a_bucket_and_a_wired_app(tmp_path):
    proj = _project(tmp_path, {"@aws-sdk/client-s3": "^3.0.0"})
    svc = Services()
    res, run, c, deploy = _run(tmp_path, proj, svc, ["create_bucket"])
    assert res.ok, [e.data for e in run.events if e.kind == "run_finished"]
    q = next(e for e in run.events if e.kind == "question" and e.data["code"] == "storage_choice")
    assert q.data["options"] == ["create_bucket", "keep_current"] and q.data["params"] == {"monthly": 522500}
    plan = next(e for e in run.events if e.kind == "plan_ready").data
    assert [s["id"] for s in plan["plan"]["storage"]] == ["api-files"] and any(l["kind"] == "bucket" for l in plan["cost"]["lines"])
    assert any(l["to_kind"] == "bucket" for l in plan["plan"]["links"])  # the architecture knows the app talks to the bucket
    done = run.events[-1].data
    assert done["created"]["buckets"] == ["api-files"] and svc.buckets == ["api-files"]
    sent = next(x[2] for x in deploy.calls if x[1].endswith("update-envs"))
    assert {v["key"]: v["value"] for v in sent["variables"]}["LIARA_BUCKET_NAME"] == "api-files"
    assert "SECRET-TEST" not in json.dumps([e.as_dict() for e in run.events])
    # and cleaning up afterwards removes it again
    deleted: dict = {}
    remove_all(c, deleted)
    assert deleted["buckets"] == ["api-files"] and svc.buckets == []


def test_pipeline_declining_the_bucket_changes_nothing(tmp_path):
    proj = _project(tmp_path, {"@aws-sdk/client-s3": "^3.0.0"})
    svc = Services()
    res, run, c, _ = _run(tmp_path, proj, svc, ["keep_current"])
    assert any(e.kind == "question" and e.data["code"] == "storage_choice" for e in run.events)  # it was offered
    assert res.ok and svc.calls == [] and run.events[-1].data["created"]["buckets"] == []


def test_pipeline_mail_with_liara_creates_the_server_and_reports_the_dns_records(tmp_path):
    proj = _project(tmp_path, {"nodemailer": "^6.0.0"})
    svc = Services()
    res, run, c, deploy = _run(tmp_path, proj, svc, ["liara_mail", "mydomain.com"])
    assert res.ok, [e.data for e in run.events if e.kind == "run_finished"]
    done = run.events[-1].data
    assert done["created"]["mail_servers"] == ["mydomain.com"] and [f["code"] for f in done["follow_ups"]] == ["mail_dns", "mail_dev_mode"]
    assert any(r["purpose"] == "dkim" for r in done["follow_ups"][0]["records"])
    sent = next(x[2] for x in deploy.calls if x[1].endswith("update-envs"))
    env = {v["key"]: v["value"] for v in sent["variables"]}
    assert env["MAIL_HOST"] == "smtp.c1.liara.email" and env["MAIL_FROM"] == "noreply@mydomain.com"
    assert "pw-TEST" not in json.dumps([e.as_dict() for e in run.events])


def test_pipeline_a_dry_run_previews_the_services_without_creating_them(tmp_path):
    proj = _project(tmp_path, {"nodemailer": "^6.0.0", "@aws-sdk/client-s3": "^3.0.0"})
    svc = Services()
    res, run, c, _ = _run(tmp_path, proj, svc, ["create_bucket", "liara_mail", "mydomain.com"], execute=False)
    assert res.ok and svc.calls == []
    ops = [w["op"] for w in run.events[-1].data["would_send"]]
    assert "create_bucket" in ops and "create_mail_server" in ops and "create_smtp_user" in ops


def test_pipeline_a_wordpress_site_asks_first_and_stops_cleanly_for_the_managed_product(tmp_path):
    proj = _project(tmp_path, {}, wordpress=True)
    svc = Services()
    res, run, c, deploy = _run(tmp_path, proj, svc, ["managed_console"])
    assert not res.ok and res.stopped == "cancelled"
    done = run.events[-1].data
    assert done["reason_code"] == "wordpress_managed" and done["follow_ups"][0]["code"] == "wordpress_console" and done["created"]["apps"] == []
    assert deploy.calls == [] and svc.calls == []
    q = next(e for e in run.events if e.kind == "question")
    assert q.data["code"] == "wordpress_choice" and q.data["params"]["managed"] == 4290000 and "plan_ready" not in [e.kind for e in run.events]  # nothing was planned or paid for


def test_pipeline_a_wordpress_site_can_still_take_the_generic_route(tmp_path):
    proj = _project(tmp_path, {}, wordpress=True)
    res, run, _, _ = _run(tmp_path, proj, Services(), ["generic_php"])
    assert res.ok and "plan_ready" in [e.kind for e in run.events]


def _node_app(tmp_path, scripts, deps):
    import json as _json
    (tmp_path / "package.json").write_text(_json.dumps({"scripts": scripts, "dependencies": {f"d{i}": "1" for i in range(deps)}}))
    return DeployPlan(apps=[AppSpec(name="shop", platform="node", port=3000)])


def test_a_heavy_build_asks_before_the_cheapest_tier_and_remembers_the_answer(tmp_path):
    from liara_agent.deploy.decisions import decide_build_tier
    from liara_agent.liara.pricing import estimate_plan
    plan = _node_app(tmp_path, {"build": "webpack --config a.js && webpack --config b.js"}, 35)
    ask = asker(["recommended"])
    decide_build_tier(plan, tmp_path, CAT, ask)
    code, options, params = ask.seen[0]
    assert code == "build_tier_choice" and options == ["recommended", "smallest"]
    assert params["smallest_minutes"] == 5 and params["recommended_minutes"] >= 15 and params["recommended_monthly"] > params["smallest_monthly"]
    assert plan.apps[0].min_build_minutes > 5
    line = estimate_plan(CAT, plan)[0]
    assert CAT.project_bundles[line.plan][line.tier].build_timeout >= 15   # the price card and the app that gets created use the tier that fits


def test_the_person_can_still_pick_the_cheapest_tier(tmp_path):
    from liara_agent.deploy.decisions import decide_build_tier
    from liara_agent.liara.pricing import estimate_plan
    plan = _node_app(tmp_path, {"build": "webpack --config a.js && webpack --config b.js"}, 35)
    decide_build_tier(plan, tmp_path, CAT, asker(["smallest"]))
    assert plan.apps[0].min_build_minutes == 0
    line = estimate_plan(CAT, plan)[0]
    assert CAT.project_bundles[line.plan][line.tier].build_timeout == 5


def test_a_light_project_is_not_asked(tmp_path):
    from liara_agent.deploy.decisions import decide_build_tier
    ask = asker([])
    decide_build_tier(_node_app(tmp_path, {"start": "node server.js"}, 8), tmp_path, CAT, ask)
    decide_build_tier(DeployPlan(apps=[AppSpec(name="py", platform="python", port=8000)]), tmp_path, CAT, ask)
    assert ask.seen == []
