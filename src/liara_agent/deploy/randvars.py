"""$RANDOM_STRING in a ready-made app's spec: a fresh secret for each use, made when the app is deployed (never in a plan or an event)."""
from __future__ import annotations

import re
import secrets

RANDOM = re.compile(r"\$RANDOM_STRING")


def substitute_random(env: dict[str, str]) -> dict[str, str]:
    return {k: RANDOM.sub(lambda _: secrets.token_urlsafe(24), v) for k, v in env.items()}
