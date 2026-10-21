"""$DB_<id>_* variables: how an app's environment refers to a database's generated credentials."""
from __future__ import annotations

import re
from urllib.parse import quote

DB_VAR = re.compile(r"\$\{?DB_([A-Za-z0-9-]+)_(HOST|PORT|USER|PASSWORD|NAME)\}?")
# The API has no database-name field for Postgres; Liara's own specs hardcode "postgres". MariaDB/MySQL may return dbName.
DEFAULT_NAME = {"postgres": "postgres"}


def db_values(db: dict) -> dict[str, str]:
    return {"HOST": db.get("hostname", ""), "PORT": str(db.get("internalPort") or db.get("port") or ""),
            "USER": db.get("username", ""), "PASSWORD": db.get("root_password", ""),
            "NAME": db.get("dbName") or DEFAULT_NAME.get(db.get("type", ""), "")}


def substitute_db_vars(env: dict[str, str], dbs: dict[str, dict]) -> dict[str, str]:
    """Replace $DB_<id>_HOST etc. with real values. Unknown ids are left untouched (and reported by the caller).
    Inside a URL's user:password part the values are percent-encoded: a generated password may contain @ or /."""
    def one(value: str) -> str:
        userinfo = [(m.start(), m.end()) for m in re.finditer(r"://[^/@\s]*@", value)]

        def sub(m: re.Match) -> str:
            db = dbs.get(m.group(1))
            if not db:
                return m.group(0)
            v = db_values(db)[m.group(2)]
            return quote(v, safe="") if m.group(2) in ("USER", "PASSWORD") and any(a <= m.start() < b for a, b in userinfo) else v
        return DB_VAR.sub(sub, value)
    return {k: one(v) for k, v in env.items()}
