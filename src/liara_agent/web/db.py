"""SQLite storage. Every query that returns user data is filtered by user_id: other users' rows are simply not found."""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from liara_agent.web.monitor import SCHEMA as MONITOR_SCHEMA

# What the agent found by itself when an app stopped answering (the diagnosis, as JSON), newest last.
INCIDENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, ts REAL NOT NULL, url TEXT, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS incidents_project ON incidents(project_id, ts);
"""

# One conversation per person and project (project_id 0 = the general one on the dashboard). Messages are stored as the model saw them (JSON);
# an action is a change the agent proposed and the person has not decided yet, or has.
CHAT_SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, project_id INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
  UNIQUE(user_id, project_id));
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE, body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS chat_messages_chat ON chat_messages(chat_id, id);
CREATE TABLE IF NOT EXISTS chat_actions (
  id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE, tool TEXT NOT NULL, args TEXT NOT NULL, tool_call_id TEXT NOT NULL,
  info TEXT NOT NULL, status TEXT NOT NULL, result TEXT, created_at REAL NOT NULL);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw_hash TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
  digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  liara_token_enc TEXT, team_id TEXT NOT NULL DEFAULT '', language TEXT NOT NULL DEFAULT 'en', team_name TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, name TEXT NOT NULL,
  source_kind TEXT NOT NULL, git_url TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS auto_deploys (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, ts REAL NOT NULL, finished_at REAL,
  commit_id TEXT, message TEXT, state TEXT NOT NULL, ok INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  mode TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL, summary TEXT, cleaned_at REAL);
"""


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.executescript(MONITOR_SCHEMA)
        self._conn.executescript(CHAT_SCHEMA)
        self._conn.executescript(INCIDENT_SCHEMA)
        if "team_name" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(settings)")}:
            self._conn.execute("ALTER TABLE settings ADD COLUMN team_name TEXT NOT NULL DEFAULT ''")
        if "github_id" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(users)")}:
            self._conn.execute("ALTER TABLE users ADD COLUMN github_id TEXT")
            self._conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_github_id ON users(github_id) WHERE github_id IS NOT NULL")
        for col in ("alert_webhook_enc", "github_token_enc", "github_login"):
            if col not in {r["name"] for r in self._conn.execute("PRAGMA table_info(settings)")}:
                self._conn.execute(f"ALTER TABLE settings ADD COLUMN {col} TEXT")
        for col in ("git_token_enc", "git_branch"):  # added after the first release
            if col not in {r["name"] for r in self._conn.execute("PRAGMA table_info(projects)")}:
                self._conn.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
        if "tail" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(auto_deploys)")}:   # the last lines of the build, so the page can follow it
            self._conn.execute("ALTER TABLE auto_deploys ADD COLUMN tail TEXT")
        if "source" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(incidents)")}:   # outage (the uptime check saw it down) | daily (the scheduled health check)
            self._conn.execute("ALTER TABLE incidents ADD COLUMN source TEXT NOT NULL DEFAULT 'outage'")
        if "run_id" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(auto_deploys)")}:   # the run page that follows the release
            self._conn.execute("ALTER TABLE auto_deploys ADD COLUMN run_id TEXT")
        if "cleaned_at" not in {r["name"] for r in self._conn.execute("PRAGMA table_info(runs)")}:  # databases created before this column existed
            self._conn.execute("ALTER TABLE runs ADD COLUMN cleaned_at REAL")
        self._never_reuse_project_ids()
        self._lock = threading.Lock()

    def _never_reuse_project_ids(self) -> None:
        """A deleted project's id must never come back: chats, run folders and links are keyed by it. Older databases made `projects` without AUTOINCREMENT
        (SQLite then hands out the highest deleted id again), so the table is rebuilt once with the same columns and rows, and the counter starts above
        every project id anything still mentions."""
        c = self._conn
        sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()["sql"]
        if "AUTOINCREMENT" not in sql.upper():
            new_sql = sql.replace("projects", "projects_new", 1).replace("id INTEGER PRIMARY KEY", "id INTEGER PRIMARY KEY AUTOINCREMENT", 1)
            cols = ", ".join(r["name"] for r in c.execute("PRAGMA table_info(projects)"))
            c.execute("PRAGMA foreign_keys = OFF")   # the other tables keep pointing at "projects", which is the rebuilt table afterwards
            try:
                c.execute("BEGIN")
                c.execute(new_sql)
                c.execute(f"INSERT INTO projects_new({cols}) SELECT {cols} FROM projects")
                c.execute("DROP TABLE projects")
                c.execute("ALTER TABLE projects_new RENAME TO projects")
                c.execute("COMMIT")
            except Exception:
                c.execute("ROLLBACK")
                raise
            finally:
                c.execute("PRAGMA foreign_keys = ON")
        seen = [c.execute(f"SELECT COALESCE(MAX({col}), 0) m FROM {t}").fetchone()["m"]
                for t, col in (("projects", "id"), ("chats", "project_id"), ("runs", "project_id"), ("auto_deploys", "project_id"), ("incidents", "project_id"))]
        top = max(seen)
        row = c.execute("SELECT seq FROM sqlite_sequence WHERE name='projects'").fetchone()
        if row is None:
            c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES ('projects', ?)", (top,))
        elif row["seq"] < top:
            c.execute("UPDATE sqlite_sequence SET seq=? WHERE name='projects'", (top,))

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def run(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, args).lastrowid or 0

    @staticmethod
    def now() -> float:
        return time.time()
