"""Creating a bucket or an email server for a plan. Each returns the values the app's placeholders stand for, and the follow-ups the
person still has to do (a DNS record, a mode switch): things this agent cannot do for them."""
from __future__ import annotations

import secrets
from typing import Callable

from liara_agent.deploy.services import MAIL_ADDRESS, mail_id, probe_endpoint
from liara_agent.liara.client import LiaraAPIError, LiaraClient
from liara_agent.liara.live import LiveCatalog
from liara_agent.liara.pricing import choose_mail_plan, choose_storage_plan, mail_cost, storage_cost
from liara_agent.plan.model import MailSpec, StorageSpec

Emit = Callable[[str, dict], None]
FALLBACK_SMTP = ("smtp.c1.liara.email", 465)  # from Liara's Node.js email guide; the server object normally says


def provision_bucket(client: LiaraClient, cat: LiveCatalog, spec: StorageSpec, emit: Emit) -> tuple[dict[str, str], list[dict]]:
    plan_id = choose_storage_plan(cat)
    step = f"bucket:{spec.id}"
    emit("step_started", {"step": step})
    name = spec.id
    for attempt in range(4):  # a bucket id is unique across all of Liara: on a clash a suffixed one is tried
        try:
            client.create_bucket(name, plan_id, spec.permission)
            break
        except LiaraAPIError as e:
            if e.status != 409 or attempt == 3:
                raise
            name = f"{spec.id[:34].strip('-')}-{secrets.token_hex(2)}"
    cost = storage_cost(cat, plan_id)
    emit("resource_created", {"kind": "bucket", "id": name, "plan": plan_id, "permission": spec.permission, "hourly_price": round(cost.hourly_with_vat, 2),
                              "monthly_price": round(cost.monthly_with_vat), "dry_run": client.dry_run})
    access, secret = client.create_key(name, f"{name} (created by liara-agent)")
    if client.dry_run:
        endpoint, confirmed = "https://storage.iran.liara.site", True
    else:
        endpoint, confirmed = probe_endpoint(name)
    emit("step_finished", {"step": step, "dry_run": client.dry_run})
    follow = [] if confirmed else [{"code": "bucket_endpoint_unconfirmed", "params": {"bucket": name, "endpoint": endpoint}, "records": []}]
    return {"ENDPOINT": endpoint, "BUCKET": name, "ACCESS_KEY": access, "SECRET_KEY": secret, "REGION": "us-east-1"}, follow


def dns_records(server: dict) -> list[dict]:
    """The DNS records an email server needs, from the `dns_setup` part of its description (SPF, DKIM, MX and the return-path CNAME)."""
    d = server.get("dns_setup") or {}
    domain = server.get("domain", "")
    out: list[dict] = []
    if (spf := d.get("spf") or {}).get("spf_record"):
        out.append({"type": "TXT", "name": "@", "value": spf["spf_record"], "status": spf.get("spf_status"), "purpose": "spf"})
    if (dkim := d.get("dkim") or {}).get("dkim_record"):
        out.append({"type": "TXT", "name": dkim.get("dkim_record_name") or f"mail._domainkey.{domain}", "value": dkim["dkim_record"], "status": dkim.get("dkim_status"), "purpose": "dkim"})
    if (mx := d.get("mx_record") or {}).get("mx_record_value"):
        out.append({"type": "MX", "name": "@", "value": f"10 {mx['mx_record_value']}", "status": mx.get("mx_status"), "purpose": "mx"})
    if (rp := d.get("return_path") or {}).get("return_path_value"):
        out.append({"type": "CNAME", "name": rp.get("return_path_domain") or f"rp.{domain}", "value": rp["return_path_value"], "status": rp.get("return_path_status"), "purpose": "return_path"})
    return out


def provision_mail(client: LiaraClient, cat: LiveCatalog, spec: MailSpec, emit: Emit) -> tuple[dict[str, str], list[dict]]:
    plan_id = choose_mail_plan(cat)
    step = f"mail:{spec.domain}"
    emit("step_started", {"step": step})
    server_id = client.create_mail_server(spec.domain, plan_id, spec.mode)
    cost = mail_cost(cat, plan_id)
    emit("resource_created", {"kind": "mail", "id": spec.domain, "plan": plan_id, "mode": spec.mode, "hourly_price": round(cost.hourly_with_vat, 2),
                              "monthly_price": round(cost.monthly_with_vat), "dry_run": client.dry_run})
    if not server_id:
        raise LiaraAPIError(404, "GET", "/api/v1/mails", f"the email server for {spec.domain} was created but not found in the list")
    server = {} if client.dry_run else client.mail_server(server_id)
    client.create_mail_address(server_id, MAIL_ADDRESS)
    user, password = client.create_smtp_user(server_id, "created by liara-agent")
    host, port = server.get("smtp_server") or FALLBACK_SMTP[0], server.get("smtp_port") or FALLBACK_SMTP[1]
    emit("step_finished", {"step": step, "dry_run": client.dry_run})
    follow = [{"code": "mail_dns", "params": {"domain": spec.domain, "mail": mail_id(spec.domain)}, "records": dns_records(server)}]
    if spec.mode == "DEV":
        follow.append({"code": "mail_dev_mode", "params": {"domain": spec.domain}, "records": []})
    return {"HOST": str(host), "PORT": str(port), "USER": user, "PASSWORD": password, "FROM": f"{MAIL_ADDRESS}@{spec.domain}"}, follow
