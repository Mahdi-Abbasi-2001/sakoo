"""Outage alerts: when a running app stops answering (or answers again) the person's own webhook is called with a short JSON message.
The URL is the person's (a Slack, Discord, Telegram-bridge or any HTTPS endpoint), so it is the one place the server calls an address a user typed:
only https, no credentials in the address, no redirects, and every address the host resolves to must be public."""
from __future__ import annotations

import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse

import httpx


class WebhookError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


Resolve = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    except OSError:
        return []


def validate_webhook(url: str, resolve: Resolve = _resolve) -> str:
    u = urlparse(url.strip())
    if u.scheme != "https" or not u.hostname:
        raise WebhookError("webhook_scheme", "the address must start with https://")
    if u.username or u.password:
        raise WebhookError("webhook_credentials", "do not put a user name or password in the address")
    ips = resolve(u.hostname)
    if not ips:
        raise WebhookError("webhook_unresolved", "that host does not resolve")
    for ip in ips:
        a = ipaddress.ip_address(ip)
        if not a.is_global:
            raise WebhookError("webhook_private", "that address points inside a private network")
    return url.strip()


def send(url: str, payload: dict, *, resolve: Resolve = _resolve, post=None) -> bool:
    """POST the payload; True when the endpoint accepted it. Never raises: an alert must not break monitoring."""
    try:
        validate_webhook(url, resolve)
        post = post or (lambda u, j: httpx.post(u, json=j, timeout=6, follow_redirects=False))
        return post(url, payload).status_code < 300
    except (WebhookError, httpx.HTTPError):
        return False


def transition(states: list[bool]) -> str | None:
    """`states` is the newest-first list of the last checks (True = up). 'down' after two failures in a row that follow a good check (or the first ever
    checks), 'up' when a check succeeds right after at least two failures. A single failed check is not an alert."""
    if len(states) >= 2 and not states[0] and not states[1] and (len(states) == 2 or states[2]):
        return "down"
    if len(states) >= 3 and states[0] and not states[1] and not states[2]:
        return "up"
    return None
