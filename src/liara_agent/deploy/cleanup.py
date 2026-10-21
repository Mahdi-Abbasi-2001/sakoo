"""Removing everything this agent created for a project: the journal is the list, and nothing outside it is ever touched.

Order matters: DNS records and domains first, then the apps, then a bucket's keys before the bucket (Liara refuses to delete a bucket that still has keys),
an email server's SMTP users before the server, and the databases last."""
from __future__ import annotations

from typing import Callable

from liara_agent.liara.client import Journal, LiaraAPIError, LiaraClient


def delete_or_forget(journal: Journal, kind: str, item, delete: Callable[[], None]) -> None:
    """Delete something this agent added; if Liara no longer has it (404) just drop it from the journal."""
    try:
        delete()
    except LiaraAPIError as e:
        if e.status != 404:
            raise
        setattr(journal, kind, [x for x in getattr(journal, kind) if x != item])
        journal.save()


def nothing_created(journal: Journal) -> bool:
    return not (journal.apps or journal.databases or journal.domains or journal.records or journal.buckets or journal.keys or journal.mail_servers or journal.smtp or journal.disks or journal.ftps)


def remove_all(client: LiaraClient, deleted: dict[str, list[str]]) -> None:
    """Delete everything in the client's journal. `deleted` is filled in as it goes, so a failure part way still reports what was removed."""
    j = client.journal
    for k in ("apps", "databases", "domains", "buckets", "mail_servers", "disks"):
        deleted.setdefault(k, [])
    for f in list(j.ftps):  # a temporary access should already be gone; make sure
        delete_or_forget(j, "ftps", f, lambda: client.delete_ftp(f["id"]))
    for rec in list(j.records):  # DNS records this agent added to a zone (the zone itself is the user's and stays)
        delete_or_forget(j, "records", rec, lambda: client.delete_record(rec["zone"], rec["id"]))
    for d in list(j.domains):
        delete_or_forget(j, "domains", d, lambda: client.delete_domain(d["id"]))
        deleted["domains"].append(d["name"])
    pending: list[dict] = []
    ids: dict[str, str] = {}
    for a in list(j.apps):
        try:
            ids[a] = client.app_id(a)  # remembered: the disk calls need it and it cannot be looked up once the app is deleted
        except Exception:
            ids[a] = ""
        for d in [d for d in j.disks if d["app"] == a]:  # while the app exists: Liara's disk calls need it
            try:
                delete_or_forget(j, "disks", d, lambda: client.delete_disk(d["app"], d["name"], ids[a]))
                deleted["disks"].append(f"{d['app']}/{d['name']}")
            except LiaraAPIError:
                pending.append(d)  # maybe still mounted: try again once the app is gone
        delete_or_forget(j, "apps", a, lambda: client.delete_app(a))
        deleted["apps"].append(a)
    for d in pending + [d for d in list(j.disks) if d not in pending and d["app"] not in j.apps]:
        if d not in j.disks:
            continue
        try:
            delete_or_forget(j, "disks", d, lambda: client.delete_disk(d["app"], d["name"], ids.get(d["app"], "")))
            deleted["disks"].append(f"{d['app']}/{d['name']}")
        except LiaraAPIError as e:  # not hidden: a disk left behind may still be billed
            deleted.setdefault("failed", []).append(f"disk {d['app']}/{d['name']}: Liara answered HTTP {e.status} (check the app's disks in the console)")
    for key in list(j.keys):
        delete_or_forget(j, "keys", key, lambda: client.delete_key(key.get("access") or key["id"]))
    for b in list(j.buckets):
        try:
            delete_or_forget(j, "buckets", b, lambda: client.delete_bucket(b))
            deleted["buckets"].append(b)
        except LiaraAPIError as e:  # one stuck bucket must not stop the rest from being removed
            deleted.setdefault("failed", []).append(f"bucket {b}: Liara answered HTTP {e.status}")
    for u in list(j.smtp):
        delete_or_forget(j, "smtp", u, lambda: client.delete_smtp_user(u["server"], u["username"]))
    for m in list(j.mail_servers):
        delete_or_forget(j, "mail_servers", m, lambda: client.delete_mail_server(m["id"]))
        deleted["mail_servers"].append(m["domain"])
    for d in list(j.databases):
        name = j.database_names.get(d, d)
        delete_or_forget(j, "databases", d, lambda: client.delete_database(d))
        deleted["databases"].append(name)
