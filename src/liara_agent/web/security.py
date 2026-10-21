"""Passwords, sessions, encrypted token storage, login rate limiting."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(hashed: str, password: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    """Only the digest of a session token is stored, so a database leak does not hand out sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


class TokenVault:
    """Encrypts users' Liara API tokens at rest. The key never leaves the server and is not stored in the database."""

    def __init__(self, secret: str):
        self._f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))

    def encrypt(self, value: str) -> str:
        return self._f.encrypt(value.encode()).decode()

    def decrypt(self, blob: str) -> str | None:
        try:
            return self._f.decrypt(blob.encode()).decode()
        except InvalidToken:
            return None


class RateLimiter:
    """At most `limit` attempts per `window` seconds per key (in memory; fine for a single process)."""

    def __init__(self, limit: int = 5, window: float = 300):
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
