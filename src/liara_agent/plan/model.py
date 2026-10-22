from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from liara_agent.liara import catalog


class Evidence(BaseModel):
    file: str
    line: int | None = None
    note: str = ""


class Finding(BaseModel):
    """A claim about the project. Claims without evidence are dropped or turned into questions."""

    value: str
    confidence: float = 0.5
    evidence: list[Evidence] = Field(default_factory=list)


class DiskSpec(BaseModel):
    name: str
    mount_to: str
    size_gb: int = 1  # created before the release that mounts it; its price is not in Liara's API


class AppSpec(BaseModel):
    name: str
    path: str = "."
    platform: str
    port: int | None = None
    build_command: str | None = None
    start_command: str | None = None
    image: str | None = None  # deploy a prebuilt Docker image instead of building from `path`
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)  # may reference $DB_<id>_HOST etc.
    disks: list[DiskSpec] = Field(default_factory=list)
    min_ram_gb: float = 0.5  # the smallest memory the app should get (a ready-made app's spec says how big it wants to be)
    min_build_minutes: float = 0  # the build time the tier must allow; 0 = the cheapest tier is fine (set when the person accepts a bigger tier for a heavy build)
    role: str = "web"  # web | worker (no listening port)
    platform_config: dict = Field(default_factory=dict)  # sent with the release, e.g. {"version": "16"}; set from the project's own version pins
    evidence: list[Evidence] = Field(default_factory=list)


class DatabaseSpec(BaseModel):
    kind: str
    id: str = "main"  # becomes the private-network hostname; the agent prefixes it to make it unique
    version: str | None = None
    env_var: str | None = None
    min_ram_gb: float = 0.5
    evidence: list[Evidence] = Field(default_factory=list)


class StorageSpec(BaseModel):
    """An object-storage bucket for the app's files. Its id is the bucket name (globally unique); access is private unless the person chose otherwise."""

    id: str
    permission: str = "private"  # private | public
    evidence: list[Evidence] = Field(default_factory=list)


class MailSpec(BaseModel):
    """Liara's email server for a domain the developer owns. Needs DNS records at the developer's DNS provider before it can send."""

    domain: str
    mode: str = "DEV"  # DEV | LIVE: Liara's own sending modes
    evidence: list[Evidence] = Field(default_factory=list)


class Note(BaseModel):
    """`code` + `params` are the stable, translatable form of the message (the UI owns the wording); `message` is the English fallback."""

    level: str  # info | warning | blocker
    message: str
    code: str | None = None
    params: dict = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)


class Question(BaseModel):
    text: str  # English fallback
    code: str | None = None
    params: dict = Field(default_factory=dict)
    options: list[str] = Field(default_factory=list)  # stable option ids the UI can localize; empty = free text


class EnvVar(BaseModel):
    name: str
    secret: bool = False
    source: str = "user"  # user | generated | database
    evidence: list[Evidence] = Field(default_factory=list)


class WordpressDelivery(BaseModel):
    """After the app is up, send the project's own wp-content to this disk over an FTPS access made through the API (and remove the access)."""
    app: str
    disk: str = "data"


class DeployPlan(BaseModel):
    apps: list[AppSpec] = Field(default_factory=list)
    databases: list[DatabaseSpec] = Field(default_factory=list)
    storage: list[StorageSpec] = Field(default_factory=list)
    mail: MailSpec | None = None
    wordpress: WordpressDelivery | None = None
    env: list[EnvVar] = Field(default_factory=list)
    questions: list[str | Question] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_catalog(self):
        for a in self.apps:
            if a.platform not in catalog.platforms():
                raise ValueError(f"unknown Liara platform {a.platform!r}; choose from {sorted(catalog.platforms())}")
        for d in self.databases:
            if d.kind not in catalog.databases():
                raise ValueError(f"unknown database {d.kind!r}; choose from {sorted(catalog.databases())}")
        return self
