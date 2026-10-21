"""Object-storage buckets and email servers: how an app is connected to them.

Like databases, these resources are created before the app, and the app's environment refers to what they produce through placeholders that are
filled in at deploy time, so a secret (a storage key, an SMTP password) never passes through a plan, an event or the model:

    $S3_<id>_ENDPOINT | BUCKET | ACCESS_KEY | SECRET_KEY | REGION        a bucket
    $MAIL_<id>_HOST | PORT | USER | PASSWORD | FROM                      an email server (<id> is its domain with dashes)

The variable NAMES the app reads are taken from its own code when they are found (AWS_BUCKET, MAIL_HOST ...); otherwise Liara's documented names are used.
"""
from __future__ import annotations

import re
import time
from typing import Callable

import httpx

from liara_agent.plan.model import DeployPlan, MailSpec, StorageSpec

SVAR = re.compile(r"\$\{?(S3|MAIL)_([A-Za-z0-9-]+)_(ENDPOINT|BUCKET|ACCESS_KEY|SECRET_KEY|REGION|HOST|PORT|USER|PASSWORD|FROM)\}?")

# Variable names by role, in order of preference. Detected names (from the code) win; the last resort is Liara's own documented convention.
S3_NAMES = {
    "access_key": ("AWS_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID", "S3_ACCESS_KEY", "STORAGE_ACCESS_KEY", "MINIO_ACCESS_KEY", "LIARA_ACCESS_KEY", "ACCESS_KEY_ID", "AWS_ACCESS_KEY"),
    "secret_key": ("AWS_SECRET_ACCESS_KEY", "S3_SECRET_ACCESS_KEY", "S3_SECRET_KEY", "STORAGE_SECRET_KEY", "MINIO_SECRET_KEY", "LIARA_SECRET_KEY", "SECRET_ACCESS_KEY"),
    "bucket": ("AWS_BUCKET", "AWS_S3_BUCKET", "AWS_STORAGE_BUCKET_NAME", "S3_BUCKET", "S3_BUCKET_NAME", "STORAGE_BUCKET", "LIARA_BUCKET_NAME", "BUCKET_NAME", "BUCKET"),
    "endpoint": ("AWS_ENDPOINT", "AWS_ENDPOINT_URL", "AWS_S3_ENDPOINT_URL", "S3_ENDPOINT", "S3_ENDPOINT_URL", "STORAGE_ENDPOINT", "LIARA_ENDPOINT", "LIARA_ENDPOINT_URL", "ENDPOINT_URL"),
    "region": ("AWS_REGION", "AWS_DEFAULT_REGION", "S3_REGION"),
}
S3_DOCUMENTED = {"access_key": "LIARA_ACCESS_KEY", "secret_key": "LIARA_SECRET_KEY", "bucket": "LIARA_BUCKET_NAME", "endpoint": "LIARA_ENDPOINT"}  # docs.liara.ir, Node.js and Python guides
S3_AWS_STYLE = {"access_key": "AWS_ACCESS_KEY_ID", "secret_key": "AWS_SECRET_ACCESS_KEY", "bucket": "AWS_BUCKET", "endpoint": "AWS_ENDPOINT", "region": "AWS_DEFAULT_REGION"}  # the Laravel guide

MAIL_NAMES = {
    "host": ("MAIL_HOST", "SMTP_HOST", "EMAIL_HOST", "MAIL_SERVER", "SMTP_SERVER", "EMAIL_SMTP_HOST"),
    "port": ("MAIL_PORT", "SMTP_PORT", "EMAIL_PORT"),
    "user": ("MAIL_USER", "MAIL_USERNAME", "SMTP_USER", "SMTP_USERNAME", "EMAIL_HOST_USER", "EMAIL_USER"),
    "password": ("MAIL_PASSWORD", "SMTP_PASSWORD", "SMTP_PASS", "EMAIL_HOST_PASSWORD", "EMAIL_PASSWORD"),
    "from": ("MAIL_FROM", "MAIL_FROM_ADDRESS", "EMAIL_FROM", "SMTP_FROM", "DEFAULT_FROM_EMAIL"),
}
MAIL_DOCUMENTED = {"host": "MAIL_HOST", "port": "MAIL_PORT", "user": "MAIL_USER", "password": "MAIL_PASSWORD", "from": "MAIL_FROM"}  # the Node.js guide
MAIL_ADDRESS = "noreply"  # the sending address created on the server: <noreply>@<domain>

# Liara's bucket endpoint is not in its API. The docs show storage.c2.liara.site; the console showed <bucket>.storage.iran.liara.site. Probed, not assumed.
S3_ENDPOINT_CANDIDATES = ("https://storage.iran.liara.site", "https://storage.c2.liara.site")


def substitute_service_vars(env: dict[str, str], services: dict[str, dict[str, str]]) -> dict[str, str]:
    """Replace $S3_<id>_* and $MAIL_<id>_* with what the created resources returned. `services` is keyed "S3:<id>" / "MAIL:<id>".
    Unknown ids are left untouched (the caller reports them)."""
    def sub(m: re.Match) -> str:
        values = services.get(f"{m.group(1)}:{m.group(2)}")
        return values.get(m.group(3), m.group(0)) if values else m.group(0)
    return {k: SVAR.sub(sub, v) for k, v in env.items()}


def mail_id(domain: str) -> str:
    return domain.replace(".", "-")


def bucket_name(app: str) -> str:
    """A readable, valid bucket id (lowercase letters, digits and dashes; 3-63 characters) from the app's name."""
    base = re.sub(r"[^a-z0-9-]+", "-", f"{app}-files".lower()).strip("-")
    return (base + "-bucket")[:40] if len(base) < 3 else base[:40].strip("-")


# -- wiring ---------------------------------------------------------------------------------------------------------------
def _pick(names: tuple[str, ...], detected: set[str]) -> str | None:
    return next((n for n in names if n in detected), None)


def wire_storage_env(plan: DeployPlan, spec: StorageSpec, detected: set[str]) -> dict[str, str]:
    """Add the variables a bucket needs to the web app's environment. Returns {name: role} for what was set."""
    app = next((a for a in plan.apps if a.role == "web"), plan.apps[0] if plan.apps else None)
    if app is None:
        return {}
    chosen = {role: _pick(names, detected) for role, names in S3_NAMES.items()}
    if not any(chosen.values()):  # nothing in the code to go by: Laravel/PHP use AWS-style names, everything else the names in Liara's guides
        chosen = dict(S3_AWS_STYLE if app.platform in ("laravel", "php") else S3_DOCUMENTED)
    else:
        for role in ("access_key", "secret_key", "bucket", "endpoint"):  # a client needs all four: a name the code does not read is harmless, a missing one breaks it
            if chosen.get(role) is None:
                chosen[role] = S3_DOCUMENTED[role]
    placeholders = {"access_key": "ACCESS_KEY", "secret_key": "SECRET_KEY", "bucket": "BUCKET", "endpoint": "ENDPOINT", "region": "REGION"}
    out: dict[str, str] = {}
    for role, name in chosen.items():
        if not name or (name in app.env and "$S3_" not in app.env[name]):
            continue  # a value the plan set on purpose is left alone
        app.env[name] = f"$S3_{spec.id}_{placeholders[role]}"
        out[name] = role
    if app.platform in ("laravel", "php") and "AWS_USE_PATH_STYLE_ENDPOINT" not in app.env and any(n.startswith("AWS_") for n in out):
        app.env["AWS_USE_PATH_STYLE_ENDPOINT"] = "true"  # from Liara's Laravel guide
    return out


def wire_mail_env(plan: DeployPlan, spec: MailSpec, detected: set[str]) -> dict[str, str]:
    app = next((a for a in plan.apps if a.role == "web"), plan.apps[0] if plan.apps else None)
    if app is None:
        return {}
    out: dict[str, str] = {}
    mid = mail_id(spec.domain)
    for role, names in MAIL_NAMES.items():
        name = _pick(names, detected) or MAIL_DOCUMENTED[role]
        if name in app.env and "$MAIL_" not in app.env[name]:
            continue
        app.env[name] = f"$MAIL_{mid}_{role.upper()}"
        out[name] = role
    return out


# -- the endpoint of a bucket ---------------------------------------------------------------------------------------------
def probe_endpoint(bucket: str, *, get: Callable[[str], httpx.Response] | None = None, sleep: Callable[[float], None] = time.sleep,
                   attempts: int = 6, delay: float = 5.0) -> tuple[str, bool]:
    """The S3 endpoint that answers for this bucket, and whether it was confirmed. A bucket's address answers 403 (private) or 200 once it exists,
    404/no answer before; the first candidate that does is the endpoint. If none does in time the first candidate is returned UNCONFIRMED."""
    get = get or (lambda u: httpx.get(u, timeout=8, follow_redirects=False))
    for i in range(attempts):
        for base in S3_ENDPOINT_CANDIDATES:
            host = base.split("://", 1)[1]
            try:
                r = get(f"https://{bucket}.{host}/")
            except httpx.HTTPError:
                continue
            if r.status_code in (200, 301, 302, 403):
                return base, True
        if i < attempts - 1:
            sleep(delay)
    return S3_ENDPOINT_CANDIDATES[0], False
