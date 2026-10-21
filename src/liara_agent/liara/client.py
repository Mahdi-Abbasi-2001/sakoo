"""Liara API client for actions that create, change or delete things.

Safety rules, enforced here rather than left to the caller:
 * dry_run is the default: nothing is sent, the Action is only recorded;
 * we only delete apps/databases that this agent created (tracked in the Journal);
 * secret values never appear in recorded actions.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from liara_agent.liara.live import API_URL

STORAGE_URL = "https://storage-service.liara.ir"
MAIL_URL = "https://mail-service.liara.ir"
DNS_URL = "https://dns-service.iran.liara.ir"  # the DNS service has its own host; this is the one the official CLI uses for Iranian accounts

SECRET_HINT = ("PASSWORD", "SECRET", "TOKEN", "KEY", "PRIVATE", "CREDENTIAL")


class LiaraAPIError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, body: str):
        super().__init__(f"{method} {path} -> {status}: {body[:500]}")
        self.status, self.body = status, body


def _check(r: httpx.Response) -> None:
    if r.is_error:
        raise LiaraAPIError(r.status_code, r.request.method, r.request.url.path, r.text)


class NotOwnedError(PermissionError):
    """Raised when asked to modify or delete a resource this agent did not create."""


@dataclass
class Action:
    op: str
    method: str
    path: str
    summary: str
    body: dict[str, Any] | None = None  # secrets redacted
    sent: bool = False


@dataclass
class Journal:
    """Resources created by this agent. Persisted so a later run can still clean up after itself."""

    path: Path | None = None
    apps: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)  # Liara database ids
    database_names: dict[str, str] = field(default_factory=dict)  # Liara id -> the hostname we gave it (what the UI knows)
    domains: list[dict] = field(default_factory=list)  # {"id", "name", "app"}: custom domains this agent attached
    zones: list[str] = field(default_factory=list)  # DNS zones this agent created (never deleted automatically: the zone is the user's domain)
    records: list[dict] = field(default_factory=list)  # {"zone", "id"}: DNS records this agent added
    buckets: list[str] = field(default_factory=list)  # object-storage buckets this agent created
    keys: list[dict] = field(default_factory=list)  # {"id", "access", "bucket"}: access keys this agent created
    mail_servers: list[dict] = field(default_factory=list)  # {"id", "domain"}
    smtp: list[dict] = field(default_factory=list)  # {"server", "username"}: SMTP users this agent created
    disks: list[dict] = field(default_factory=list)  # {"app", "name"}: disks this agent created
    ftps: list[dict] = field(default_factory=list)  # {"id", "app", "disk", "username"}: FTPS accesses this agent created (short-lived)
    networks: list[dict] = field(default_factory=list)  # {"id", "name"}: private networks this agent created (Liara's API has no way to delete one: an empty network costs nothing and stays)

    @classmethod
    def load(cls, path: str | Path) -> "Journal":
        p = Path(path)
        data = json.loads(p.read_text()) if p.is_file() else {}
        return cls(p, data.get("apps", []), data.get("databases", []), data.get("database_names", {}), data.get("domains", []), data.get("zones", []), data.get("records", []),
                   data.get("buckets", []), data.get("keys", []), data.get("mail_servers", []), data.get("smtp", []), data.get("disks", []), data.get("ftps", []), data.get("networks", []))

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"apps": self.apps, "databases": self.databases, "database_names": self.database_names,
                                            "domains": self.domains, "zones": self.zones, "records": self.records, "buckets": self.buckets, "keys": self.keys,
                                            "mail_servers": self.mail_servers, "smtp": self.smtp, "disks": self.disks, "ftps": self.ftps, "networks": self.networks}, indent=1))


def _unwrap(line: str) -> str:
    """Liara returns log lines as JSON like {"type":"stdout","_entry":"..."} (seen on a real app); keep just the text."""
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return line
    return str(obj.get("_entry", line)) if isinstance(obj, dict) else line


def redact(env: dict[str, str]) -> dict[str, str]:
    """Mask secret-named variables AND passwords embedded in URLs (DATABASE_URL), so recorded actions never show credentials."""
    from liara_agent.events import scrub

    return scrub(env)


# What an access key may do. The spec omits `permissions` but the server requires it, as an array of S3-style names; found by trying values on a throwaway bucket:
# read, list, put and delete are accepted, write, create, upload, update and download are not.
KEY_PERMISSIONS = ["read", "list", "put", "delete"]


class LiaraClient:
    def __init__(self, token: str, *, team_id: str = "", dry_run: bool = True, journal: Journal | None = None,
                 http: httpx.Client | None = None, base_url: str = API_URL, dns_http: httpx.Client | None = None,
                 storage_http: httpx.Client | None = None, mail_http: httpx.Client | None = None):
        if not token:
            raise ValueError("LIARA_API_TOKEN is not set")
        self.dry_run = dry_run
        self.team_id = team_id
        self.journal = journal or Journal()
        self.actions: list[Action] = []
        self._http = http or httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        self._dns_http = dns_http or (None if http else httpx.Client(base_url=DNS_URL, headers={"Authorization": f"Bearer {token}"}, timeout=60))
        self._storage_http = storage_http or (None if http else httpx.Client(base_url=STORAGE_URL, headers={"Authorization": f"Bearer {token}"}, timeout=60))
        self._mail_http = mail_http or (None if http else httpx.Client(base_url=MAIL_URL, headers={"Authorization": f"Bearer {token}"}, timeout=60))

    def dry_copy(self) -> "LiaraClient":
        """A dry-run client sharing this one's connection settings. It can never send a mutating request."""
        c = LiaraClient.__new__(LiaraClient)
        c.dry_run, c.team_id, c.journal, c.actions, c._http, c._dns_http = True, self.team_id, Journal(), [], self._http, self._dns_http
        c._storage_http, c._mail_http = self._storage_http, self._mail_http
        return c

    # -- plumbing ---------------------------------------------------------------------------------------------
    def _params(self) -> dict[str, str] | None:
        return {"teamID": self.team_id} if self.team_id else None

    def _session(self, dns: bool = False, host: str = "") -> httpx.Client:
        """Liara's services have their own hosts: the PaaS API, DNS, object storage and mail."""
        return {"dns": self._dns_http, "storage": self._storage_http, "mail": self._mail_http}.get("dns" if dns else host, self._http)

    def _read(self, path: str, dns: bool = False, host: str = "", **params) -> Any:
        r = self._session(dns, host).get(path, params={**(self._params() or {}), **params})
        r.raise_for_status()
        return r.json()

    def _mutate(self, op: str, method: str, path: str, summary: str, body: dict | None, shown: dict | None = None,
                fake: dict | None = None, dns: bool = False, host: str = "", **kw) -> dict:
        action = Action(op, method, path, summary, shown if shown is not None else body)
        self.actions.append(action)
        if self.dry_run:
            return fake or {}
        r = self._session(dns, host).request(method, path, params=self._params(), json=body if "files" not in kw else None, **kw)
        _check(r)
        action.sent = True
        return r.json() if r.content else {}

    def _owned_app(self, name: str) -> None:
        if name not in self.journal.apps:
            raise NotOwnedError(f"app {name!r} was not created by this agent; refusing to modify it")

    # -- reads (never mutate) -----------------------------------------------------------------------------------
    def networks(self) -> list[dict]:
        return self._read("/v1/networks")["networks"]

    def create_network(self, name: str) -> str:
        """A private network for a project's apps and databases (verified on the real service: the body is {networkName} and the answer {network: {_id}}).
        There is no call to delete one that this agent has found, so it is recorded but never removed."""
        res = self._mutate("create_network", "POST", "/v1/networks", f"create the private network {name}", {"networkName": name}, fake={"network": {"_id": "dryrun-network"}})
        nid = (res.get("network") or {}).get("_id", "")
        if not self.dry_run and nid:
            self.journal.networks.append({"id": nid, "name": name})
            self.journal.save()
        return nid

    def existing_names(self) -> tuple[set[str], set[str]]:
        """(app ids, database hostnames) already on the account. Read-only; used to catch name clashes before creating anything."""
        apps = {p["project_id"] for p in self._read("/v1/projects").get("projects", [])}
        dbs = {d.get("hostname") for d in self._read("/v1/databases").get("databases", [])}
        return apps, {d for d in dbs if d}

    def app_exists(self, name: str) -> bool:
        return any(p["project_id"] == name for p in self._read("/v1/projects")["projects"])

    def release(self, release_id: str) -> dict:
        return self._read(f"/v1/releases/{release_id}")["release"]

    def releases(self, app: str, page: int = 1, count: int = 20) -> dict:
        """The app's releases, newest first: {total, currentRelease, releases[{_id, state, port, imageName, projectType, gitInfo, ...}]}."""
        return self._read(f"/v1/projects/{app}/releases", page=page, count=count)

    def metrics(self, app: str, kind: str, since: int) -> dict:
        """Read-only. kind is cpu or memory; `since` is a Unix time in seconds (the spec does not say the unit: seconds is Prometheus's)."""
        return self._read(f"/v1/projects/{app}/metrics/{kind}", since=since)

    def build_logs(self, release_id: str, since: str | None = None) -> dict:
        return self._read(f"/v2/releases/{release_id}/build-logs", **({"since": since} if since else {}))

    def app_logs(self, name: str, since: int | None = None) -> Any:
        return self._read(f"/v1/projects/{name}/logs", **({"since": since} if since else {}))

    def runtime_logs(self, name: str, release_id: str | None = None) -> list[str]:
        """Latest runtime log lines, oldest first. Shape per the CLI: {data: [{metaData, values: [[ts, line], ...]}]} (verify on a real failure)."""
        params = {"direction": "forward", "last_lines": "true", **({"releaseId": release_id} if release_id else {})}
        data = self._read(f"/v2/projects/{name}/logs", **params)
        rows = [(int(ts), _unwrap(line)) for entry in data.get("data", []) for ts, line in entry.get("values", [])]
        return [line for _, line in sorted(rows)]

    def database(self, db_id: str) -> dict:
        """Includes credentials: never log or send the result to an LLM."""
        return self._read(f"/v1/databases/{db_id}")["database"]

    # -- custom domains (PaaS API) and DNS zones (DNS service) ---------------------------------------------------------
    def app_domains(self, app: str) -> list[dict]:
        return self._read("/v1/domains", project=app).get("domains", [])

    def domain_check(self, domain_id: str) -> dict:
        return self._read(f"/v1/domains/{domain_id}/check").get("domain", {})

    def zones(self) -> list[dict]:
        return self._read("/api/v1/zones", dns=True).get("data", [])

    def zone(self, name: str) -> dict | None:
        try:
            return self._read(f"/api/v1/zones/{name}", dns=True).get("data")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    def zone_records(self, name: str) -> list[dict]:
        return self._read(f"/api/v1/zones/{name}/dns-records", dns=True).get("data", [])

    def _owned_domain(self, domain_id: str) -> dict:
        for d in self.journal.domains:
            if d["id"] == domain_id:
                return d
        raise NotOwnedError(f"domain {domain_id!r} was not attached by this agent; refusing to modify it")

    def create_domain(self, app: str, name: str) -> str:
        """Attach a custom domain to an app this agent created. Liara answers with the DNS records the domain needs."""
        self._owned_app(app) if not self.dry_run else None
        res = self._mutate("create_domain", "POST", "/v1/domains", f"attach {name} to {app}", {"name": name, "project": app, "type": "PROJECT"},
                           fake={"domain": {"_id": "dryrun-domain"}})
        domain_id = (res.get("domain") or {}).get("_id", "")
        if not self.dry_run and domain_id:
            self.journal.domains.append({"id": domain_id, "name": name, "app": app})
            self.journal.save()
        return domain_id

    def enable_ssl(self, domain_id: str) -> dict:
        """Free certificate for an attached domain. The API names its parameter just `domain`; the domain's name is sent first, its id if that is refused."""
        d = self._owned_domain(domain_id) if not self.dry_run else {"name": "example.com"}
        try:
            return self._mutate("enable_ssl", "POST", "/v1/domains/provision-ssl-certs", f"request an SSL certificate for {d['name']}", {"domain": d["name"]}, fake={"dry_run": True})
        except LiaraAPIError as e:
            if e.status not in (400, 404):
                raise
        return self._mutate("enable_ssl", "POST", "/v1/domains/provision-ssl-certs", f"request an SSL certificate for {d['name']} (by id)", {"domain": domain_id}, fake={"dry_run": True})

    def set_domain_redirect(self, domain_id: str, to: str, status: int = 301) -> None:
        """Make a domain this agent attached redirect to another one (www -> the bare domain). 301 is permanent, 302 temporary."""
        self._owned_domain(domain_id) if not self.dry_run else None
        self._mutate("set_domain_redirect", "POST", f"/v1/domains/{domain_id}/set-redirect", f"redirect {domain_id} to {to}", {"redirectStatus": status, "redirectTo": to})

    def delete_domain(self, domain_id: str) -> None:
        self._owned_domain(domain_id) if not self.dry_run else None
        self._mutate("delete_domain", "DELETE", f"/v1/domains/{domain_id}", f"detach domain {domain_id}", None)
        if not self.dry_run:
            self.journal.domains = [d for d in self.journal.domains if d["id"] != domain_id]
            self.journal.save()

    def create_zone(self, name: str) -> dict:
        res = self._mutate("create_zone", "POST", "/api/v1/zones", f"create DNS zone {name}", {"name": name}, fake={"data": {"name": name, "status": "PENDING"}}, dns=True)
        if not self.dry_run:
            self.journal.zones.append(name)
            self.journal.save()
        return res.get("data", {})

    def check_zone(self, name: str) -> None:
        """Ask Liara to re-check the zone's nameservers (406 means a check is already running: try again later)."""
        self._mutate("check_zone", "PUT", f"/api/v1/zones/{name}/check", f"check the nameservers of {name}", None, dns=True)

    def create_record(self, zone: str, name: str, rtype: str, contents: list[dict], ttl: int | None = None) -> str:
        body = {"name": name, "type": rtype, "contents": contents, **({"ttl": ttl} if ttl else {})}
        res = self._mutate("create_record", "POST", f"/api/v1/zones/{zone}/dns-records", f"add {rtype} record {name} to {zone}", body, fake={"data": {"id": "dryrun-record"}}, dns=True)
        rid = (res.get("data") or {}).get("id", "")
        if not self.dry_run and rid:
            self.journal.records.append({"zone": zone, "id": rid})
            self.journal.save()
        return rid

    def delete_record(self, zone: str, record_id: str) -> None:
        if not any(r["id"] == record_id and r["zone"] == zone for r in self.journal.records):
            raise NotOwnedError(f"DNS record {record_id!r} was not added by this agent; refusing to delete it")
        self._mutate("delete_record", "DELETE", f"/api/v1/zones/{zone}/dns-records/{record_id}", f"remove DNS record {record_id} from {zone}", None, dns=True)
        if not self.dry_run:
            self.journal.records = [r for r in self.journal.records if r["id"] != record_id]
            self.journal.save()

    # -- object storage (buckets and their access keys) ---------------------------------------------------------------------
    def buckets(self) -> list[dict]:
        return self._read("/api/v1/buckets", host="storage").get("buckets", [])

    def create_bucket(self, name: str, plan: str, permission: str = "private") -> None:
        self._mutate("create_bucket", "POST", "/api/v1/buckets", f"create {permission} bucket {name} ({plan})", {"name": name, "plan": plan, "permission": permission}, host="storage")
        if not self.dry_run:
            self.journal.buckets.append(name)
            self.journal.save()

    def keys(self) -> list[dict]:
        return self._read("/api/v1/keys", host="storage").get("keys", [])

    def create_key(self, bucket: str, description: str) -> tuple[str, str]:
        """An access key limited to one bucket. The spec says `buckets` holds objects, but the real server answers `"buckets[0]" must be a string`: the bucket is sent as a
        plain string, its name first and, if that is refused, its id. The secret key is shown once and is returned here, never recorded."""
        if bucket not in self.journal.buckets and not self.dry_run:
            raise NotOwnedError(f"bucket {bucket!r} was not created by this agent; refusing to make a key for it")
        shapes = [bucket]
        if not self.dry_run:
            shapes.append(next((b.get("id") for b in self.buckets() if b.get("name") == bucket), ""))
        last: LiaraAPIError | None = None
        for item in shapes:
            try:
                res = self._mutate("create_key", "POST", "/api/v1/keys", f"create an access key for bucket {bucket}", {"buckets": [item], "description": description, "permissions": KEY_PERMISSIONS},
                                   fake={"data": {"accessKey": "dryrun-access", "secretKey": "dryrun-secret"}}, host="storage")
            except LiaraAPIError as e:
                if e.status not in (400, 404):
                    raise
                last = e
                continue
            data = res.get("data") or {}
            access, secret = data.get("accessKey", ""), data.get("secretKey", "")
            if not self.dry_run:
                kid = next((k.get("id") for k in self.keys() if k.get("accessKey") == access), "")
                self.journal.keys.append({"id": kid, "access": access, "bucket": bucket})
                self.journal.save()
            return access, secret
        raise last or LiaraAPIError(400, "POST", "/api/v1/keys", "no key shape accepted")

    def delete_key(self, key_id: str) -> None:
        """Delete an access key this agent made. The endpoint takes the ACCESS KEY (the id answered 404 on a real run, and a bucket that still has a key cannot be deleted)."""
        item = next((k for k in self.journal.keys if key_id in (k.get("id"), k.get("access"))), None)
        if item is None:
            raise NotOwnedError(f"key {key_id!r} was not created by this agent; refusing to delete it")
        ident = [x for x in (item.get("access"), item.get("id")) if x]
        for i, name in enumerate(ident):
            try:
                self._mutate("delete_key", "DELETE", f"/api/v1/keys/{name}", f"delete access key {item.get('access') or item.get('id')}", None, host="storage")
                break
            except LiaraAPIError as e:
                if e.status != 404 or i == len(ident) - 1:
                    raise
        if not self.dry_run:
            self.journal.keys = [k for k in self.journal.keys if k is not item]
            self.journal.save()

    def delete_bucket(self, name: str) -> None:
        """A bucket can only be deleted once the keys that reach it are gone (Liara's rule), so delete its keys first."""
        if name not in self.journal.buckets:
            raise NotOwnedError(f"bucket {name!r} was not created by this agent; refusing to delete it")
        self._mutate("delete_bucket", "DELETE", f"/api/v1/buckets/{name}", f"delete bucket {name}", None, host="storage")
        if not self.dry_run:
            self.journal.buckets.remove(name)
            self.journal.save()

    # -- email servers ------------------------------------------------------------------------------------------------------
    def mail_servers(self) -> list[dict]:
        return (self._read("/api/v1/mails", host="mail").get("data") or {}).get("mailServers", [])

    def mail_server(self, server_id: str) -> dict:
        return ((self._read(f"/api/v1/mails/{server_id}", host="mail").get("data") or {}).get("mailServer")) or {}

    def create_mail_server(self, domain: str, plan: str, mode: str = "DEV") -> str:
        self._mutate("create_mail_server", "POST", "/api/v1/mails", f"create an email server for {domain} ({plan}, {mode})", {"domain": domain, "plan": plan, "mode": mode}, host="mail")
        if self.dry_run:
            return "dryrun-mail"
        server_id = next((m.get("id") for m in self.mail_servers() if m.get("domain") == domain), "")
        if server_id:
            self.journal.mail_servers.append({"id": server_id, "domain": domain})
            self.journal.save()
        return server_id

    def create_mail_address(self, server_id: str, name: str) -> None:
        self._owned_mail(server_id)
        self._mutate("create_mail_address", "POST", f"/api/v1/mails/{server_id}/accounts", f"add the address {name} to the email server", {"name": name}, host="mail")

    def create_smtp_user(self, server_id: str, description: str) -> tuple[str, str]:
        """An SMTP user (the API names the password field 'passowrd', a typo; both spellings are read). The password is shown once and never recorded."""
        self._owned_mail(server_id)
        res = self._mutate("create_smtp_user", "POST", f"/api/v1/mails/{server_id}/smtp-credentials", "create an SMTP user", {"description": description},
                           fake={"data": {"username": "dryrun-user", "password": "dryrun-password"}}, host="mail")
        data = res.get("data") or {}
        user, password = data.get("username", ""), data.get("passowrd") or data.get("password", "")
        if not self.dry_run and user:
            self.journal.smtp.append({"server": server_id, "username": user})
            self.journal.save()
        return user, password

    def delete_smtp_user(self, server_id: str, username: str) -> None:
        if not any(u["server"] == server_id and u["username"] == username for u in self.journal.smtp):
            raise NotOwnedError("that SMTP user was not created by this agent; refusing to delete it")
        self._mutate("delete_smtp_user", "DELETE", f"/api/v1/mails/{server_id}/smtp-credentials/{username}", f"delete SMTP user {username}", None, host="mail")
        if not self.dry_run:
            self.journal.smtp = [u for u in self.journal.smtp if not (u["server"] == server_id and u["username"] == username)]
            self.journal.save()

    def delete_mail_server(self, server_id: str) -> None:
        self._owned_mail(server_id)
        self._mutate("delete_mail_server", "DELETE", f"/api/v1/mails/{server_id}", f"delete email server {server_id}", None, host="mail")
        if not self.dry_run:
            self.journal.mail_servers = [m for m in self.journal.mail_servers if m["id"] != server_id]
            self.journal.save()

    def _owned_mail(self, server_id: str) -> None:
        if not self.dry_run and not any(m["id"] == server_id for m in self.journal.mail_servers):
            raise NotOwnedError(f"email server {server_id!r} was not created by this agent; refusing to modify it")

    # -- creates --------------------------------------------------------------------------------------------------
    def create_app(self, name: str, platform: str, plan: str, bundle: str, network_id: str | None,
                   read_only: bool | None = None) -> None:
        body = {"name": name, "planID": plan, "platform": platform, "bundlePlanID": bundle}
        if network_id:   # Liara refuses `null`: a project without a private network leaves the field out
            body["network"] = network_id
        if read_only is not None:
            body["readOnlyRootFilesystem"] = read_only
        self._mutate("create_app", "POST", "/v1/projects", f"create app {name} ({platform}, {plan}/{bundle})", body)
        if not self.dry_run:
            self.journal.apps.append(name)
            self.journal.save()

    def create_database(self, hostname: str, kind: str, version: str, plan: str, bundle: str, network_id: str | None,
                        public: bool = False, options: dict | None = None) -> str:
        body = {"hostname": hostname, "type": kind, "version": version, "planID": plan, "bundlePlanID": bundle,
                "publicNetwork": public}
        if network_id:
            body["network"] = network_id
        if options:
            body["options"] = options
        res = self._mutate("create_database", "POST", "/v1/databases", f"create {kind} {version} database {hostname} ({plan}/{bundle})",
                           body, fake={"databaseID": f"dryrun-{hostname}"})
        db_id = res.get("databaseID", hostname)
        if not self.dry_run:
            self.journal.databases.append(db_id)
            self.journal.database_names[db_id] = hostname
            self.journal.save()
        return db_id

    def app_envs(self, app: str) -> dict[str, str]:
        """The app's environment variables with their values. Read-only. The values are secrets: they stay inside this process and never go to a language model."""
        project = self._read(f"/v1/projects/{app}").get("project") or {}
        return {e["key"]: e.get("value", "") for e in project.get("envs") or [] if e.get("key")}

    def merge_envs(self, app: str, change: dict[str, str | None]) -> dict[str, str]:
        """Liara's update call REPLACES the whole set of variables, so a change is the existing set with the change applied (None removes a name).
        Returns the full set that was sent (values included: callers must not log it)."""
        merged = {**self.app_envs(app), **{k: v for k, v in change.items() if v is not None}}
        for k, v in change.items():
            if v is None:
                merged.pop(k, None)
        self.set_env(app, merged)
        return merged

    def set_env(self, app: str, variables: dict[str, str]) -> None:
        self._owned_app(app) if not self.dry_run else None
        body = {"project": app, "variables": [{"key": k, "value": v} for k, v in variables.items()]}
        shown = {"project": app, "variables": [{"key": k, "value": v} for k, v in redact(variables).items()]}
        self._mutate("set_env", "POST", "/v1/projects/update-envs", f"set {len(variables)} environment variables on {app}", body, shown)

    # -- deploy ------------------------------------------------------------------------------------------------------
    def upload_source(self, app: str, archive: Path) -> str:
        self._owned_app(app) if not self.dry_run else None
        action = Action("upload_source", "POST", f"/v2/projects/{app}/sources", f"upload {archive.name} ({archive.stat().st_size // 1024} KB) to {app}")
        self.actions.append(action)
        if self.dry_run:
            return "dryrun-source"
        with archive.open("rb") as fh:
            r = self._http.post(f"/v2/projects/{app}/sources", params=self._params(), files={"file": (archive.name, fh, "application/gzip")})
        _check(r)
        action.sent = True
        return r.json()["sourceID"]

    def create_release(self, app: str, source_id: str | None, platform: str, port: int, *, envs: dict[str, str] | None = None,
                       disks: list[dict] | None = None, message: str = "deployed by liara-agent", **extra) -> str:
        """source_id is None when deploying a prebuilt image (pass image="..." in extra)."""
        self._owned_app(app) if not self.dry_run else None
        body: dict[str, Any] = {"type": platform, "port": port, "message": message,
                                **{k: v for k, v in extra.items() if v not in (None, [], {})}}
        if source_id:
            body["sourceID"] = source_id
        if envs:
            body["envs"] = envs
        if disks:
            body["disks"] = disks
        shown = {**body, **({"envs": redact(envs)} if envs else {})}
        res = self._mutate("create_release", "POST", f"/v2/projects/{app}/releases", f"deploy {app} on port {port}", body, shown,
                           fake={"releaseID": "dryrun-release"})
        return res.get("releaseID", "")

    def wait_for_release(self, release_id: str, on_event: Callable[[str, str], None] = lambda kind, text: None,
                         *, poll_seconds: float = 3, timeout_seconds: float = 1800) -> dict:
        """Poll until READY / FAILED / UNHEALTHY, streaming build lines to on_event. Returns the final release."""
        if self.dry_run:
            return {"state": "DRYRUN"}
        deadline, since = time.time() + timeout_seconds, None
        while time.time() < deadline:
            logs = self.build_logs(release_id, since)
            for out in logs.get("buildOutput", []):
                on_event("build_error" if out.get("stream") != "STDOUT" else "build", out.get("line", ""))
                since = out.get("createdAt", since)
            rel = logs.get("release") or self.release(release_id)
            state = str(rel.get("state") or "").upper()
            # a release that carries a failure reason, or whose state names a failure, is over even if its state string is one we have not seen
            failed = state in ("FAILED", "UNHEALTHY") or bool(rel.get("failReason")) or any(w in state for w in ("FAIL", "TIMEOUT", "TIMED", "CANCEL", "ERROR", "STOP", "ABORT"))
            if state == "READY" or failed:
                if failed and state not in ("FAILED", "UNHEALTHY"):
                    rel = {**rel, "failReason": rel.get("failReason") or f"release ended as {state}", "state": "FAILED"}
                return rel
            time.sleep(poll_seconds)
        raise TimeoutError(f"release {release_id} did not finish within {timeout_seconds:.0f}s")

    # -- changes to owned apps (used by self-heal) -------------------------------------------------------------------
    def resize_app(self, name: str, plan: str) -> None:
        """Change the hardware plan. Liara's resize endpoint takes only planID and rejects bundlePlanID ("not allowed", verified on a real app),
        so the feature tier (basic/silver/gold) cannot be changed here: it is chosen when the app is created."""
        self._owned_app(name) if not self.dry_run else None
        self._mutate("resize_app", "POST", f"/v1/projects/{name}/resize", f"change {name} to {plan}", {"planID": plan})

    def update_port(self, name: str, port: int) -> None:
        self._owned_app(name) if not self.dry_run else None
        self._mutate("update_port", "POST", "/v1/projects/update-port", f"set {name} port to {port}", {"project": name, "port": port})

    # -- deletes (owned resources only) -----------------------------------------------------------------------------
    # -- disks and FTPS access to them -------------------------------------------------------------------------------
    def app_id(self, name: str) -> str:
        """The app's internal id. Liara's disk list and delete calls take it (the spec says `{id}` there, `{name}` for create): given the name they answer 500."""
        for p in self._read("/v1/projects").get("projects", []):
            if p.get("project_id") == name:
                return p.get("_id") or name
        return name

    def disks(self, app: str) -> list[dict]:
        """The app's disks: [{name, size (GB), ...}]. Read-only."""
        return self._read(f"/v1/projects/{self.app_id(app)}/disks").get("disks", [])

    def create_disk(self, app: str, name: str, size_gb: int) -> None:
        """A disk has to exist before a release can mount it (the CLI never creates one on deploy)."""
        self._owned_app(app) if not self.dry_run else None
        self._mutate("create_disk", "POST", f"/v1/projects/{app}/disks", f"create disk {name} ({size_gb} GB) for {app}", {"name": name, "size": str(size_gb)})
        if not self.dry_run:
            self.journal.disks.append({"app": app, "name": name})
            self.journal.save()

    def delete_disk(self, app: str, name: str, app_id: str = "") -> None:
        """`app_id` is the app's internal id, looked up before the app is deleted (it cannot be looked up afterwards)."""
        if {"app": app, "name": name} not in self.journal.disks:
            raise NotOwnedError(f"disk {name!r} of {app!r} was not created by this agent; refusing to delete it")
        self._mutate("delete_disk", "DELETE", f"/v1/projects/{app_id or self.app_id(app)}/disks/{name}", f"delete disk {name} of {app}", None)
        if not self.dry_run:
            self.journal.disks.remove({"app": app, "name": name})
            self.journal.save()

    def ftp_accesses(self, app: str, disk: str) -> dict:
        return self._read(f"/v1/projects/{app}/disks/{disk}/ftp")

    def create_ftp(self, app: str, disk: str, username: str) -> dict:
        """A read-write FTPS access to one disk of an app this agent created. Returns {username, password, host, id}; the password is shown by Liara only here."""
        self._owned_app(app) if not self.dry_run else None
        out = self._mutate("create_ftp", "POST", f"/v1/projects/{app}/disks/{disk}/ftp", f"create FTPS access {username} to disk {disk} of {app}",
                           {"username": username, "readOnly": False}, fake={"username": username, "password": "", "host": "ftp.example"})
        if self.dry_run:
            return {**out, "id": ""}
        fid = out.get("_id") or out.get("id") or next((a.get("_id") for a in self.ftp_accesses(app, disk).get("accesses", []) if a.get("username") == out.get("username", username)), "")
        self.journal.ftps.append({"id": fid, "app": app, "disk": disk, "username": out.get("username", username)})
        self.journal.save()
        return {**out, "id": fid}

    def delete_ftp(self, fid: str) -> None:
        item = next((f for f in self.journal.ftps if f["id"] == fid), None)
        if item is None:
            raise NotOwnedError(f"FTPS access {fid!r} was not created by this agent; refusing to delete it")
        try:  # the spec calls the parameter the ftp's "name": the user name is tried first, the id second (the id alone answered 404 on a real run)
            self._mutate("delete_ftp", "DELETE", f"/v1/ftp/{item['username']}", f"delete FTPS access {item['username']}", None)
        except LiaraAPIError as e:
            if e.status != 404 or not fid:
                raise
            self._mutate("delete_ftp", "DELETE", f"/v1/ftp/{fid}", f"delete FTPS access {item['username']}", None)
        if not self.dry_run:
            self.journal.ftps.remove(item)
            self.journal.save()

    def delete_app(self, name: str) -> None:
        self._owned_app(name)
        self._mutate("delete_app", "DELETE", f"/v1/projects/{name}", f"delete app {name}", None)
        if not self.dry_run:
            self.journal.apps.remove(name)
            self.journal.save()

    def delete_database(self, db_id: str) -> None:
        if db_id not in self.journal.databases:
            raise NotOwnedError(f"database {db_id!r} was not created by this agent; refusing to delete it")
        self._mutate("delete_database", "DELETE", f"/v1/databases/{db_id}", f"delete database {db_id}", None)
        if not self.dry_run:
            self.journal.databases.remove(db_id)
            self.journal.save()
