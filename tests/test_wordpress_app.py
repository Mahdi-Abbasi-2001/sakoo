import json
from pathlib import Path

import httpx

from liara_agent import wpdeliver
from liara_agent.config import Settings
from liara_agent.deploy import executor
from liara_agent.deploy.cleanup import remove_all
from liara_agent.deploy.pipeline import run_deployment
from liara_agent.events import Run
from liara_agent.interact import ScriptedChannel
from liara_agent.liara.client import Journal, LiaraClient
from liara_agent.liara.live import snapshot_catalog
from test_heal import READY, FakeLiara

CAT = snapshot_catalog()


class Fake(FakeLiara):
    """FakeLiara plus databases, disks and FTPS accesses."""

    def __init__(self):
        super().__init__([READY])
        self.disks, self.ftps, self.order, self.apps = [], [], [], []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        body = json.loads(req.content) if req.content and req.headers.get("content-type", "").startswith("application/json") else None
        self.order.append((m, p))
        if (m, p) == ("POST", "/v1/databases"):
            return httpx.Response(200, json={"databaseID": "db1"})
        if m == "GET" and p.startswith("/v1/databases/"):
            return httpx.Response(200, json={"database": {"hostname": "wp-main", "internalPort": 3306, "username": "root", "root_password": "pw", "status": "RUNNING", "type": "mariadb"}})
        if (m, p) == ("GET", "/v1/databases"):
            return httpx.Response(200, json={"databases": []})
        if p.endswith("/disks") and m == "POST":
            self.disks.append(body["name"])
            return httpx.Response(201)
        if (m, p) == ("GET", "/v1/projects"):
            return httpx.Response(200, json={"projects": [{"project_id": n, "_id": f"id-{n}"} for n in self.apps]})
        if p.endswith("/disks") and m == "GET":
            if "/id-" not in p:  # Liara answers 500 to a name where it wants the app's id (seen on a real run)
                return httpx.Response(500)
            return httpx.Response(200, json={"disks": [{"name": n} for n in self.disks]})
        if "/disks/" in p and p.endswith("/ftp") and m == "POST":
            self.ftps.append({"_id": "f1", "username": body["username"], "readOnly": body["readOnly"]})
            return httpx.Response(200, json={"username": body["username"], "password": "ftp-secret-pw", "host": "eiger.liara.cloud"})
        if "/disks/" in p and p.endswith("/ftp") and m == "GET":
            return httpx.Response(200, json={"accesses": self.ftps, "host": "eiger.liara.cloud"})
        if p.startswith("/v1/ftp/") and m == "DELETE":
            if p.rsplit("/", 1)[1] != "launchpad":  # by user name; the id answers 404 (seen on a real run)
                return httpx.Response(404)
            self.ftps = []
            return httpx.Response(200)
        if m == "DELETE" and "/disks/" in p:
            if "/id-" not in p:
                return httpx.Response(500)
            self.disks.remove(p.rsplit("/", 1)[1])
            return httpx.Response(200)
        if (m, p) == ("POST", "/v1/projects"):
            self.apps.append(body["name"])
            return super().__call__(req)
        if m == "DELETE" and p.startswith("/v1/projects/") and p.count("/") == 3:
            self.apps = [a for a in self.apps if a != p.rsplit("/", 1)[1]]
            return httpx.Response(200)
        return super().__call__(req)


def wp_project(tmp_path):
    for rel in ("wp-content/themes/mine/style.css", "wp-content/plugins/p/p.php", "wp-config.php"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    return tmp_path


def go(tmp_path, monkeypatch, answers=("wordpress_app", "openlitespeed", True), upload=None, execute=True):
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))
    sent = []
    monkeypatch.setattr(wpdeliver, "upload", upload or (lambda plan, **kw: sent.append((plan, kw)) or {"sent": len(plan["files"]), "bytes": 2}))
    fake = Fake()
    client = LiaraClient("tok", dry_run=not execute, journal=Journal(), http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake)))
    run = Run()
    res = run_deployment(wp_project(tmp_path), "blog", CAT, Settings(), client, ScriptedChannel(run, answers=list(answers)), run, network_id="n1", execute_=execute)
    return res, run, fake, client, sent


def test_the_wordpress_image_is_installed_through_the_api_and_wp_content_is_sent_without_the_console(tmp_path, monkeypatch):
    res, run, fake, client, sent = go(tmp_path, monkeypatch)
    done = run.events[-1].data
    assert res.ok, done
    q = next(e for e in run.events if e.kind == "question" and e.data["code"] == "wordpress_choice")
    assert q.data["options"][0] == "wordpress_app" and q.data["params"]["app"] > 0
    plan = next(e for e in run.events if e.kind == "plan_ready").data
    assert plan["how_code"] == "from_oneclick" and plan["plan"]["apps"][0]["image"].startswith("registry.c2.liara.ir/one-click-apps/wordpress-openlitespeed")
    order = [(m, p) for m, p in fake.order if m != "GET"]
    disk_at = next(i for i, x in enumerate(order) if x[1].endswith("/disks"))
    release_at = next(i for i, x in enumerate(order) if x[1].endswith("/releases"))
    assert disk_at < release_at                      # the disk exists before a release mounts it
    assert set(fake.disks) == {"data", "tmp"} and fake.ftps == []     # the temporary FTPS access is gone again
    (plan_sent, kw), = sent
    assert {f["path"] for f in plan_sent["files"]} == {"wp-content/themes/mine/style.css", "wp-content/plugins/p/p.php"}   # never wp-config.php
    assert kw["host"] == "eiger.liara.cloud" and kw["port"] == 2112 and kw["user"] == "launchpad"
    codes = [f["code"] for f in done["follow_ups"]]
    assert codes == ["wordpress_permissions", "wordpress_db_import"]
    assert "ftp-secret-pw" not in json.dumps([e.as_dict() for e in run.events])    # the access password never reaches an event
    assert fake.disks and sorted(d["name"] for d in client.journal.disks) == ["data", "tmp"]


def test_a_failed_upload_does_not_fail_the_deploy_and_still_removes_the_access(tmp_path, monkeypatch):
    def boom(plan, **kw):
        raise wpdeliver.WpError("wp_login_failed", "refused")
    res, run, fake, client, _ = go(tmp_path, monkeypatch, upload=boom)
    done = run.events[-1].data
    assert res.ok and fake.ftps == []
    assert done["follow_ups"][0] == {"code": "wordpress_upload_failed", "params": {"app": "blog", "why": "wp_login_failed"}, "records": []}


def test_cleanup_removes_the_disks_and_the_app(tmp_path, monkeypatch):
    res, run, fake, client, _ = go(tmp_path, monkeypatch)
    deleted: dict = {}
    remove_all(client, deleted)
    assert deleted["apps"] == ["blog"] and sorted(deleted["disks"]) == ["blog/data", "blog/tmp"] and client.journal.disks == [] and fake.disks == []


def test_a_dry_run_shows_the_disk_and_ftp_requests_and_sends_nothing(tmp_path, monkeypatch):
    res, run, fake, client, sent = go(tmp_path, monkeypatch, answers=("wordpress_app", "apache"), execute=False)
    done = run.events[-1].data
    ops = [r["op"] for r in done["would_send"]]
    assert done["dry_run"] and "create_disk" in ops and "create_ftp" in ops and sent == [] and not any(m != "GET" for m, _ in fake.order)


def test_the_web_server_is_a_question_and_apache_gets_its_own_app_spec(tmp_path, monkeypatch):
    res, run, fake, client, sent = go(tmp_path, monkeypatch, answers=("wordpress_app", "apache", True))
    ask = next(e for e in run.events if e.kind == "question" and e.data["code"] == "wordpress_server").data
    assert ask["options"] == ["openlitespeed", "apache"] and ask["params"]["apache"] < ask["params"]["openlitespeed"] and ask["params"]["htaccess"] is False
    plan = next(e for e in run.events if e.kind == "plan_ready").data
    assert plan["how_params"] == {"app": "wordpress-new"} and plan["plan"]["apps"][0]["port"] == 80
    assert res.ok and sent[0][1]["host"] == "eiger.liara.cloud"
    (tmp_path / ".htaccess").write_text("RewriteEngine On")
    res2, run2, *_ = go(tmp_path, monkeypatch, answers=("wordpress_app", "openlitespeed", True))
    assert next(e for e in run2.events if e.kind == "question" and e.data["code"] == "wordpress_server").data["params"]["htaccess"] is True


def test_a_failing_disk_list_does_not_stop_the_deploy(tmp_path, monkeypatch):
    class Flaky(Fake):
        def __call__(self, req):
            if req.method == "GET" and req.url.path.endswith("/disks"):
                return httpx.Response(500)
            return super().__call__(req)
    monkeypatch.setattr(executor.time, "sleep", lambda s: None)
    monkeypatch.setattr(executor, "verify_http", lambda url, **kw: (True, "HTTP 200"))
    monkeypatch.setattr(wpdeliver, "upload", lambda plan, **kw: {"sent": 2, "bytes": 2})
    fake = Flaky()
    client = LiaraClient("tok", dry_run=False, journal=Journal(), http=httpx.Client(base_url="https://api.liara.ir", transport=httpx.MockTransport(fake)))
    run = Run()
    res = run_deployment(wp_project(tmp_path), "blog", CAT, Settings(), client, ScriptedChannel(run, answers=["wordpress_app", "openlitespeed", True]), run, network_id="n1", execute_=True)
    assert res.ok and set(fake.disks) == {"data", "tmp"}


def test_disks_are_shrunk_to_fit_the_plans_storage():
    from liara_agent.deploy.executor import fit_disks
    from liara_agent.plan.model import DiskSpec
    d = [DiskSpec(name="tmp", mount_to="/tmp", size_gb=1), DiskSpec(name="data", mount_to="/x", size_gb=5)]
    assert [x.size_gb for x in fit_disks(d, 5)] == [1, 4]          # the small plan: 5 GB in all
    assert [x.size_gb for x in fit_disks(d, 10)] == [1, 5] and [x.size_gb for x in fit_disks(d, None)] == [1, 5]
    assert [x.size_gb for x in fit_disks(d, 1)] == [1, 1]          # never below 1 GB each: Liara's own error is then the answer
    assert d[1].size_gb == 5                                      # the plan is not modified
