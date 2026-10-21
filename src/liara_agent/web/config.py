from __future__ import annotations

import secrets
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="WEB_", extra="ignore")

    data_dir: Path = Path(".web-data")
    secret_key: str = ""  # encrypts stored Liara tokens; generated into data_dir on first start if empty
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"  # browser origins allowed to make changes
    cookie_secure: bool = False  # set true behind HTTPS
    static_dir: Path = Path("frontend/dist")  # the built frontend, served at / when it exists (single origin: cookies and CSRF just work)
    session_days: int = 14
    max_upload_mb: int = 50
    max_extracted_mb: int = 200
    max_files: int = 5000
    git_hosts: str = "github.com,gitlab.com,bitbucket.org"
    max_concurrent_runs_per_user: int = 2
    monitor_interval_seconds: float = 300  # uptime checks of running apps; 0 switches them off
    health_report_hours: float = 24  # a full health check of every running app (diagnosis and advice), kept and sent to the alert webhook when something needs attention; 0 switches it off
    registration_code: str = ""  # when set, creating an account needs this invite code (set it on any server reachable from the internet: accounts can use the server's model key)
    public_url: str = ""  # the address people use (https://...), when a proxy hides it; used for the GitHub callback. Empty: taken from the proxy's forwarded headers
    github_client_id: str = ""  # an OAuth app registered at github.com/settings/developers (callback: <this server>/api/github/callback); empty = login with GitHub is off
    github_client_secret: str = ""
    max_heal_attempts: int = 3  # automatic fix attempts per app
    request_timeout_seconds: float = 900  # how long a run waits for the user to answer an approval

    def origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}

    def hosts(self) -> set[str]:
        return {h.strip().lower() for h in self.git_hosts.split(",") if h.strip()}

    def ensure_secret(self) -> str:
        """The key lives in the data dir with owner-only permissions when not provided by the environment."""
        if self.secret_key:
            return self.secret_key
        self.data_dir.mkdir(parents=True, exist_ok=True)
        f = self.data_dir / ".secret_key"
        if not f.exists():
            f.write_text(secrets.token_urlsafe(48))
            f.chmod(0o600)
        return f.read_text().strip()
