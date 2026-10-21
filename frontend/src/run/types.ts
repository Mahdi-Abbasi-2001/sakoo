import type { Cost, DiagnosisView, Evidence, FixResult, Note, Params, PlanView, ReadinessFinding, RunEvent, Created, RequestView, FollowUp } from '../api/events';

export type NodeState = 'pending' | 'running' | 'waiting' | 'ok' | 'fail' | 'declined' | 'skipped';
export type AgentState = 'reading' | 'thinking' | 'waiting' | 'working' | 'done' | 'failed';
export type RunStatusView = 'running' | 'waiting' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';
export type ResState = 'proposed' | 'creating' | 'live' | 'failed' | 'removed';
/** What a cleanup took away. It is not an event: it comes from `cleaned_at` of the run and, right after the call, from the cleanup's own answer. */
export interface Removal { at: number | null; apps: string[]; databases: string[]; buckets?: string[]; mail_servers?: string[] }

export interface SubNode { id: string; state: NodeState }
export interface PipeNode {
  id: 'analyze' | 'plan' | 'confirm' | 'provision' | 'deploy' | 'verify' | 'live';
  state: NodeState;
  /** structured facts the UI words from the catalog: counts, names */
  count?: number; apps?: number; databases?: number; url?: string | null;
  subs: SubNode[];
}

export interface Resource {
  id: string; kind: 'app' | 'database' | 'disk' | 'bucket' | 'mail';
  /** database engine (postgres, redis) or app platform (docker, node) */
  type: string | null; role: 'web' | 'worker' | null; port: number | null; version: string | null; path: string | null; mountTo: string | null; ownerApp: string | null;
  plan: string | null; tier: string | null; tierLabel: string | null;
  hourly: number | null; monthly: number | null;          // toman incl. VAT; null when the price is not known (a disk)
  priceUnverified: boolean;
  state: ResState; dry: boolean;
  previous: { plan: string; tier: string; hourly: number | null } | null;   // set by resource_updated
  evidence: Evidence[]; env: { name: string; reference: string | null; has_value: boolean }[];
  url: string | null;
}
export interface Edge { from: string; to: string; label: string; inferred: boolean }

export interface LogLine { n: number; id: number; ts: number; rel: number; kind: string; text: string; tone: '' | 'ok' | 'warn' | 'fail' | 'wait'; step: string | null; phase: string | null }
export interface TestRow { step: string; name: string; ok: boolean | null; detail: string; url: string | null; app: string }

export type TimelineItem =
  | { key: string; type: 'read'; state: 'ok'; ts: number; manifests: Record<string, string[]>; hints: string[]; envVars: string[] }
  | { key: string; type: 'question'; state: 'ok' | 'wait'; ts: number; id: string; code: string | null; params: Params; text: string; options: string[]; answer: string | null }
  | { key: string; type: 'plan'; state: 'ok'; ts: number; how: string; howCode: string | null; howParams: Params; apps: number; databases: number; plan: PlanView }
  | { key: string; type: 'notes'; state: 'warn' | 'ok'; ts: number; notes: Note[]; warnings: number; readiness: ReadinessFinding[] }
  | { key: string; type: 'confirm'; state: 'wait' | 'ok' | 'fail'; ts: number; id: string; kind: 'plan' | 'fix' | 'patch'; code: string | null; params: Params; prompt: string; priceDelta: number | null; accepted: boolean | null }
  | { key: string; type: 'secret'; state: 'wait' | 'ok'; ts: number; id: string; name: string; provided: boolean }
  | { key: string; type: 'resource'; state: 'run' | 'ok' | 'fail'; ts: number; id: string; kind: 'app' | 'database' | 'bucket' | 'mail' }
  | { key: string; type: 'diagnosis'; state: 'fail'; ts: number; diagnosis: DiagnosisView & { app: string }; attempt: number | null }
  | { key: string; type: 'fix'; state: 'ok' | 'fail'; ts: number; fix: FixResult; declined: boolean }
  | { key: string; type: 'patch'; state: 'ok'; ts: number; file: string; diff: string }
  | { key: string; type: 'test'; state: 'ok' | 'fail'; ts: number; test: TestRow }
  | { key: string; type: 'warning'; state: 'warn'; ts: number; step: string; code: string | null; params: Params; message: string }
  | { key: string; type: 'agent'; state: 'ok'; ts: number; text: string }
  | { key: string; type: 'expired'; state: 'fail'; ts: number; id: string; request: string }
  | { key: string; type: 'finished'; state: 'ok' | 'fail' | 'wait'; ts: number; ok: boolean; cancelled: boolean; reasonCode: string | null; reasonParams: Params };

export interface OpenReq {
  kind: 'question' | 'confirm' | 'secret';
  /** data.id, the id to answer with */
  id: string;
  eventId: number; ts: number;
  /** when the server declines it (unix seconds, the server's clock), straight from the event */
  expiresAt: number | null;
  // question
  text?: string; options?: string[]; code?: string | null; params?: Params;
  // confirm
  confirmKind?: 'plan' | 'fix' | 'patch'; prompt?: string; priceDeltaHourly?: number | null;
  // secret: the NAME only. A value is never part of an event and never part of this state.
  name?: string;
}

export interface RunViewState {
  events: number; lastEventId: number; t0: number | null; tLast: number | null;
  project: string | null; dryRun: boolean | null; release: boolean; started: boolean;   // release: new code on a running app, not a deployment (nothing to delete)
  status: RunStatusView; agent: AgentState; phase: string | null;
  pipeline: PipeNode[];
  plan: PlanView | null; how: string | null; planNotes: Note[]; readiness: ReadinessFinding[]; wouldSend: RequestView[]; howCode: string | null; howParams: Params;
  network: string | null; networkNew: boolean; cost: Cost | null; usage: { calls: number; prompt_tokens: number; completion_tokens: number } | null;
  disks: { app: string; name: string; mount_to: string; price: number | null; billing: string }[];
  resources: Resource[]; edges: Edge[];
  /** toman per hour incl. VAT: what the plan would cost, what exists now, and the difference a fix has made */
  money: { plannedHourly: number | null; plannedMonthly: number | null; currentHourly: number; currentMonthly: number; fixDeltaHourly: number };
  timeline: TimelineItem[]; logs: LogLine[]; tests: TestRow[];
  openRequests: OpenReq[];
  diagnoses: (DiagnosisView & { app: string })[]; fixesApplied: FixResult[]; fixesDeclined: FixResult[]; healAttempts: number; maxAttempts: number | null;
  urls: Record<string, string>; created: Created | null;
  removed: Removal | null;
  result: null | { ok: boolean; dryRun: boolean; stopped: string | null; failedStep: string | null; reasonCode: string | null; reasonParams: Params; diagnosis: DiagnosisView | null; blockers: { code: string | null; params: Params; message: string }[]; fixes: { code: string; kind: string; params: Params; cost_delta_hourly: number }[]; created: Created; urls: Record<string, string>; wouldSend: RequestView[]; healAttempts: number; followUps: FollowUp[] };
}
export type { RunEvent };
