"""Response models. They exist so docs/openapi.json describes responses too and the frontend's types are generated, not hand-written."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: int
    username: str


class MeOut(UserOut):
    has_liara_token: bool
    team_id: str
    team_name: str
    language: str


class SessionOut(BaseModel):
    """Never an error: lets the UI ask "am I signed in?" without a 401 in the console."""

    authenticated: bool
    user: UserOut | None = None


class SettingsOut(BaseModel):
    has_liara_token: bool
    has_alert_webhook: bool = False
    github_login: str | None = None  # set when this account is connected to GitHub
    team_id: str
    team_name: str
    language: str


class TeamOut(BaseModel):
    id: str
    name: str


class CheckOut(BaseModel):
    valid: bool
    teams: list[TeamOut] = Field(default_factory=list)
    team_required: bool = False
    networks: list[str] = Field(default_factory=list)  # network names
    apps: list[str] = Field(default_factory=list)  # existing app ids
    code: str | None = None  # when valid is false: liara_token_invalid | liara_forbidden | liara_unreachable
    params: dict[str, Any] = Field(default_factory=dict)


class NetworksOut(BaseModel):
    networks: list[str]


class OkOut(BaseModel):
    ok: bool


class ProjectCreatedOut(BaseModel):
    id: int
    name: str


class CreatedOut(BaseModel):
    apps: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    database_ids: list[str] = Field(default_factory=list)


class AppliedFixOut(BaseModel):
    code: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    cost_delta_hourly: float = 0


class RunResultOut(BaseModel):
    ok: bool | None = None
    stopped: str | None = None
    reason_code: str | None = None
    reason_params: dict[str, Any] = Field(default_factory=dict)
    failed_step: str | None = None
    diagnosis_code: str | None = None
    created: CreatedOut = Field(default_factory=CreatedOut)
    urls: dict[str, str] = Field(default_factory=dict)
    dry_run: bool | None = None
    fixes: list[AppliedFixOut] = Field(default_factory=list)
    heal_attempts: int = 0


class RunRowOut(BaseModel):
    id: str
    project_id: int
    project: str | None = None
    mode: str
    status: str  # running | waiting | succeeded | failed | cancelled | interrupted
    created_at: float
    finished_at: float | None = None
    cleaned_at: float | None = None
    result: RunResultOut | None = None
    summary: dict[str, Any] | None = None


class LastRunOut(BaseModel):
    id: str
    mode: str
    status: str
    created_at: float
    finished_at: float | None = None
    cleaned_at: float | None = None
    result: RunResultOut | None = None


class LiveResourceOut(BaseModel):
    kind: str
    id: str
    plan: str
    tier: str
    tier_label: str
    hourly_price: float
    monthly_price: float | None = None


class LiveOut(BaseModel):
    run_id: str
    urls: dict[str, str]
    resources: list[LiveResourceOut]
    hourly_incl_vat: float
    monthly_incl_vat: float
    free_address_expires_at: float | None = None


class ProjectRowOut(BaseModel):
    id: int
    name: str
    source_kind: str
    git_url: str | None = None
    git_branch: str | None = None
    private: bool = False
    down: bool = False
    wordpress: bool = False  # the project is a WordPress site (wp-content, wp-config...)
    created_at: float
    last_run: LastRunOut | None = None
    live: LiveOut | None = None


class ProjectDetailOut(ProjectRowOut):
    runs: list[RunRowOut] = Field(default_factory=list)


class ProjectsOut(BaseModel):
    projects: list[ProjectRowOut]


class RunsOut(BaseModel):
    runs: list[RunRowOut]


class EventOut(BaseModel):
    id: int
    ts: float
    kind: str
    data: dict[str, Any]


class EventsOut(BaseModel):
    events: list[EventOut]


class RunDetailOut(RunRowOut):
    open_requests: list[EventOut] = Field(default_factory=list)


class RunCreatedOut(BaseModel):
    id: str


class ProjectDeletedOut(BaseModel):
    ok: bool
    deleted: "DeletedOut"  # what was removed on Liara with the project


class DeletedOut(BaseModel):
    apps: list[str]
    databases: list[str]
    domains: list[str] = Field(default_factory=list)
    buckets: list[str] = Field(default_factory=list)
    mail_servers: list[str] = Field(default_factory=list)
    disks: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)  # things that could not be deleted (and may still be billed)


class ExplainOut(BaseModel):
    explanation: str


class CleanupOut(BaseModel):
    deleted: DeletedOut


class DomainRecordOut(BaseModel):
    """A DNS record Liara asks for. `purpose` is the key it was found under in Liara's answer (the API does not document it)."""

    type: str
    name: str
    value: str  # empty when Liara's API does not give it (the console has it)
    purpose: str = ""  # route | ownership | acme
    status: str | None = None  # Liara's verification of this record: VALID | INVALID
    derived: bool = False  # worked out by the agent, not read from Liara: compare with the console


class ZoneOut(BaseModel):
    name: str | None = None
    status: str | None = None
    active: bool
    name_servers: list[str] = Field(default_factory=list)
    current_name_servers: list[str] = Field(default_factory=list)


class DomainOut(BaseModel):
    id: str
    name: str
    status: str | None = None
    certificates_status: str | None = None
    redirect_to: str | None = None
    ready: bool  # the records are in place (best effort: Liara's status strings are not documented)
    ssl_active: bool
    managed: bool = True  # attached by this agent; others are shown but never changed
    records: list[DomainRecordOut] = Field(default_factory=list)
    zone: ZoneOut | None = None
    detail_keys: list[str] = Field(default_factory=list)  # the fields Liara returned: lets the UI say so when it could not find the records
    details: dict[str, Any] = Field(default_factory=dict)  # the domain exactly as Liara returned it (the API documents little of it)
    console_url: str | None = None  # Liara's own page for this domain: it shows which record is verified
    created: bool | None = None
    zone_check: str | None = None  # checked | busy | not_pending


class RegistrarGuideOut(BaseModel):
    domain: str
    kind: str   # irnic | generic
    mode: str   # liara_dns | own_dns
    panel_url: str | None = None
    nameservers: list[str] = Field(default_factory=list)
    records: list[dict[str, Any]] = Field(default_factory=list)
    steps: list["DomainStepOut"]
    wait_hours: dict[str, int]
    sources: list[str] = Field(default_factory=list)


class DomainGuidesOut(BaseModel):
    guides: list[RegistrarGuideOut]


class DomainsOut(BaseModel):
    app: str
    console_url: str
    domains: list[DomainOut]


class DomainStepOut(BaseModel):
    code: str
    params: dict[str, Any] = Field(default_factory=dict)


class DomainPlanOut(BaseModel):
    domain: str
    app: str
    mode: str  # liara_dns | own_dns
    already_attached: bool
    zone: ZoneOut | None = None
    steps: list[DomainStepOut]


class LimitsOut(BaseModel):
    max_upload_mb: int
    max_extracted_mb: int
    max_files: int
    git_hosts: list[str]
    max_concurrent_runs: int
    approval_timeout_seconds: float
    max_heal_attempts: int
    github_oauth: bool = False  # login with GitHub is configured on this server
    registration_code_required: bool = False  # creating an account needs an invite code


class RemovalPlanOut(BaseModel):
    """What removing a domain could also clean up in the developer's Liara DNS zone."""

    domain: str
    zone: ZoneOut | None = None
    records: list[DomainRecordOut]


class DomainRemovedOut(BaseModel):
    ok: bool
    records_removed: int = 0


class DnsRecordCheckOut(BaseModel):
    type: str
    name: str
    fqdn: str
    purpose: str = ""
    authoritative: dict[str, str]  # nameserver host -> ok | missing | mismatch | no_answer
    public: dict[str, str]  # public resolver -> the same
    local: str  # the server's own resolver


class DnsCheckOut(BaseModel):
    """What the zone's own nameservers and the big public resolvers say about a domain's records. `verdict`: all_good | propagating | missing |
    wrong_value | not_at_liara | no_nameservers | unknown."""

    zone: str
    nameservers: list[str]
    records: list[DnsRecordCheckOut]
    verdict: str


class ReleaseOut(BaseModel):
    id: str
    state: str | None = None
    current: bool = False
    port: int | None = None
    type: str | None = None
    image: str | None = None
    created_at: str | None = None
    message: str | None = None
    commit: str | None = None
    branch: str | None = None
    rollback: str | None = None  # source (the agent kept this release's source) | image (Liara's own image of it: less certain) | null (not possible)


class ReleasesOut(BaseModel):
    app: str
    current: str
    total: int | None = None
    releases: list[ReleaseOut]


class RollbackOut(BaseModel):
    release_id: str
    state: str | None = None
    ok: bool
    via: str
    url: str | None = None
    serving: bool | None = None  # the app answered over HTTP after the rollback (null when it could not be checked)
    reason: str | None = None  # why the new release failed, when it did


class CiFileOut(BaseModel):
    path: str  # where it goes in the repository
    content: str
    purpose: str  # workflow | liara_json | patch


class CiStepOut(BaseModel):
    code: str  # ci_add_secret | ci_apply_patch | ci_commit
    params: dict[str, Any] = Field(default_factory=dict)


class CiBundleOut(BaseModel):
    """What to add to a repository so that pushing to a branch deploys it to the app. The Liara token is never in a file: a step says where to create the secret."""

    provider: str  # github | gitlab
    app: str
    files: list[CiFileOut]
    steps: list[CiStepOut]


class UptimeIncidentOut(BaseModel):
    down_at: float
    up_at: float | None = None


class UptimePointOut(BaseModel):
    ts: float
    ok: bool
    ms: int


class UptimeLastOut(BaseModel):
    ts: float
    status: int | None = None
    ms: int
    url: str


class UptimeOut(BaseModel):
    """The last 24 hours of checks. `up_percent` and `current` are null until the first check."""
    interval_seconds: float
    watching: list[str]
    checks: int
    up_percent: float | None = None
    current: bool | None = None
    last: UptimeLastOut | None = None
    avg_ms: int | None = None
    incidents: list[UptimeIncidentOut]
    series: list[UptimePointOut]


class OneClickAppOut(BaseModel):
    app: str
    about: str
    categories: list[str]
    installs: int
    github: str | None = None
    needs_vm: bool  # no Liara Compose spec: it needs a cloud server, which this agent does not create
    databases: list[str]
    apps: int


class OneClickListOut(BaseModel):
    apps: list[OneClickAppOut]


class MetricPointOut(BaseModel):
    ts: float
    value: float


class DailyDeploymentsOut(BaseModel):
    used: int
    cap: int | None = None        # None: unlimited (or unknown)
    remaining: int | None = None
    unlimited: bool
    tier: str | None = None


class AppLimitsOut(BaseModel):
    """What Liara's API limits for this project's app, and what the agent cannot do because the API has no call for it."""
    daily_deployments: DailyDeploymentsOut
    known: list[str]   # codes, worded by the client (`lim.known.<code>`)


class LiveDiagnosisCause(BaseModel):
    code: str
    score: int
    params: dict = {}
    evidence: list[str] = []
    fixes: list[dict] = []   # {code, text, params?, tool?, args?}: `tool` names the chat tool that makes the change (the person approves a card)


class FixAppliedOut(BaseModel):
    ok: bool
    tool: str
    result: dict = {}


class LiveDiagnosisAdvice(BaseModel):
    code: str
    params: dict = {}
    fixes: list[dict] = []


class AiHypothesis(BaseModel):
    """A second opinion from the language model when no rule explained the problem: below the rules, never acted on by itself."""
    title: str
    explanation: str
    evidence: list[str] = []      # log lines that really are in the log
    next_step: str
    confidence: str               # low | medium (never higher)
    grounded: bool                # at least one quoted line was found in the log


class LiveDiagnosisOut(BaseModel):
    app: str
    url: str
    checked_at: float
    state: str  # healthy | degraded | down
    signals: dict
    causes: list[LiveDiagnosisCause]
    advice: list[LiveDiagnosisAdvice] = []   # what to improve before it breaks (a resource close to its limit, a slowish site)
    ai: list[AiHypothesis] | None = None     # the model's reading, when the rules were unsure and a model was available


class AnalysisOut(BaseModel):
    hypotheses: list[AiHypothesis]


class LatestDiagnosisOut(BaseModel):
    """The diagnosis made automatically when the uptime check saw the app go down (null when it never did)."""
    diagnosis: LiveDiagnosisOut | None = None
    source: str | None = None   # outage: found when the uptime check saw the app go down; daily: the scheduled health check


class MetricsOut(BaseModel):
    """CPU and memory of the running app, as Liara reports them (units are not documented, so none is promised)."""
    hours: int
    cpu: list[MetricPointOut]
    memory: list[MetricPointOut]


class PrFileOut(BaseModel):
    path: str


class PrPreviewOut(BaseModel):
    """What a pull request would contain. Nothing is sent to GitHub by the preview."""
    repo: str
    base: str | None = None
    files: list[PrFileOut]
    diff: str


class PrCreatedOut(BaseModel):
    url: str
    number: int
    branch: str


class DeployRowOut(BaseModel):
    id: int
    ts: float
    finished_at: float | None = None
    commit_id: str | None = None
    message: str | None = None
    state: str  # running | succeeded | failed
    error: str | None = None
    tail: list[str] | None = None  # the last lines of the build
    run_id: str | None = None      # the run page that followed this release


class DeploysOut(BaseModel):
    """Releases of newer code made by this server, newest first."""
    deploys: list[DeployRowOut]


class StartedOut(BaseModel):
    status: str  # started
    run_id: str | None = None  # the run page that follows the release


class GithubRepoOut(BaseModel):
    full_name: str
    private: bool
    default_branch: str
    url: str
    pushed_at: str | None = None


class GithubReposOut(BaseModel):
    repos: list[GithubRepoOut]


class WpPlanOut(BaseModel):
    """What would be sent to the managed WordPress site over FTPS. Nothing is sent by the preview."""
    files: int
    total_bytes: int
    parts: list[str]
    uploads: bool
    sample: list[str]


class WpJobOut(BaseModel):
    state: str  # idle | running | done | failed
    sent: int
    total: int
    bytes: int
    error: str | None = None


class ChatWarningOut(BaseModel):
    code: str
    params: dict


class ChatActionOut(BaseModel):
    """A change the assistant proposed: it runs only when the person approves this card."""
    id: str
    tool: str
    status: str  # pending | done | declined | failed
    summary: str  # English fallback; the page words the card from `code` and `params`
    code: str
    params: dict
    files: list[dict] | None = None   # an edit: [{path, status new|changed|deleted, added, removed}]
    diff: str | None = None           # an edit: the unified diff the person reads
    needs_value: bool
    value_label: str | None = None
    confirm_name: bool
    price_delta_hourly: float | None = None
    warnings: list["ChatWarningOut"]
    error: str | None = None
    link: str | None = None


class ChatMessageOut(BaseModel):
    id: int
    at: float | None = None   # when it was written (Unix seconds)
    role: str  # user | assistant
    text: str
    hidden_secret: bool | None = None
    looked: list[str] | None = None
    actions: list[ChatActionOut] | None = None


class ChatOut(BaseModel):
    messages: list[ChatMessageOut]
    pending: str | None = None
    available: bool | None = None
    hidden_secret: bool | None = None
