"""Is this project a WordPress site? A WordPress repo is a different kind of deployment: Liara has a managed WordPress product, and the generic PHP route
(a PHP app plus a MariaDB database) also works. The developer should choose with the prices in front of them."""
from __future__ import annotations

import json
from pathlib import Path

MANAGED_CHEAPEST_MONTHLY_INCL_VAT = 4_290_000  # Sahand: 3,900,000 + 10% VAT, read from Liara's console (2026-10-17); not in the API, so it can be stale


def looks_like_wordpress(root: str | Path) -> str | None:
    """The file that says so, or None. wp-content/ or wp-config*.php are decisive; composer.json requiring johnpbloch/wordpress is too."""
    r = Path(root)
    for marker in ("wp-content", "wp-config.php", "wp-config-sample.php", "wp-includes"):
        if (r / marker).exists():
            return marker
    try:
        composer = json.loads((r / "composer.json").read_text(errors="ignore"))
    except (OSError, ValueError):
        return None
    deps = {**(composer.get("require") or {}), **(composer.get("require-dev") or {})}
    return "composer.json" if any(k in deps for k in ("johnpbloch/wordpress", "roots/wordpress", "johnpbloch/wordpress-core")) else None
