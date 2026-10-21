import { ROUTES } from './schema.gen';
import type * as G from './schema.gen';
import type { Me, Session, SettingsOut, LiaraCheckResult, Limits, ProjectRow, ProjectDetail, RunRow, RunDetail, RunEvent } from './types';

/** The server's clock minus the browser's, from the Date header of the last response (1 s resolution). Countdowns to a server time use it. */
let serverOffsetMs = 0;
export const serverNow = () => Date.now() + serverOffsetMs;

export class ApiError extends Error {
  status: number; code: string; params: Record<string, unknown>;
  constructor(status: number, code: string, message: string, params: Record<string, unknown> = {}) { super(message); this.status = status; this.code = code; this.params = params; }
}

type Route = (typeof ROUTES)[keyof typeof ROUTES];
const R = (id: keyof typeof ROUTES): Route => ROUTES[id];

function fill(path: string, args?: Record<string, string | number>) {
  return path.replace(/\{(\w+)\}/g, (_, k) => encodeURIComponent(String(args?.[k])));
}

async function call<T>(route: { method: string; path: string }, opts: { args?: Record<string, string | number>; json?: unknown; form?: FormData; query?: Record<string, string | number> } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (route.method !== 'GET') headers['X-Requested-With'] = 'XMLHttpRequest';   // the backend's CSRF rule
  if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
  if (opts.form) body = opts.form;
  let url = fill(route.path, opts.args);
  if (opts.query) url += '?' + new URLSearchParams(Object.entries(opts.query).map(([k, v]) => [k, String(v)])).toString();
  let res: Response;
  try { res = await fetch(url, { method: route.method, headers, body, credentials: 'include' }); }
  catch { throw new ApiError(0, 'network', 'The server could not be reached.'); }
  const dh = res.headers.get('Date'); if (dh) { const t = Date.parse(dh); if (!Number.isNaN(t)) serverOffsetMs = t - Date.now(); }
  if (res.ok) return (await res.json().catch(() => ({}))) as T;
  throw await failure(res);
}

async function failure(res: Response): Promise<ApiError> {
  let detail: unknown = null;
  try { detail = (await res.json()).detail; } catch { /* not json */ }
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail) {
    const d = detail as { code: string; message?: string; params?: Record<string, unknown> };
    return new ApiError(res.status, d.code, d.message || d.code, d.params || {});
  }
  if (Array.isArray(detail)) return new ApiError(res.status, 'validation', 'The request was not valid.', { errors: detail });
  return new ApiError(res.status, 'http_' + res.status, res.statusText || 'Request failed');
}

/** What the assistant's live answer is made of: a new answer starts (`step`), its text arrives in pieces (`delta`), it looks something up (`tool`), it proposes a change (`card`). */
export type ChatEvent = { type: 'step' } | { type: 'delta'; text: string } | { type: 'tool'; name: string } | { type: 'card' };

/** POST and read the answer as it is written (server-sent events). Resolves with the saved conversation; failures throw ApiError like every other call. */
async function chatStream(path: string, json: unknown, onEvent: (e: ChatEvent) => void): Promise<G.ChatOut> {
  let res: Response;
  try { res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }, body: JSON.stringify(json), credentials: 'include' }); }
  catch { throw new ApiError(0, 'network', 'The server could not be reached.'); }
  if (!res.ok || !res.body) throw await failure(res);
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = '';
  for (;;) {
    let chunk: ReadableStreamReadResult<Uint8Array>;
    try { chunk = await reader.read(); } catch { throw new ApiError(0, 'network', 'The connection was lost.'); }
    if (chunk.done) break;
    buf += dec.decode(chunk.value, { stream: true });
    for (let i = buf.indexOf('\n\n'); i >= 0; i = buf.indexOf('\n\n')) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = block.split('\n').find((l) => l.startsWith('data: ')); if (!line) continue;
      const ev = JSON.parse(line.slice(6)) as ChatEvent | ({ type: 'done' } & G.ChatOut) | { type: 'error'; code: string; message: string };
      if (ev.type === 'done') return ev;
      if (ev.type === 'error') throw new ApiError(502, ev.code, ev.message);
      onEvent(ev);
    }
  }
  throw new ApiError(0, 'network', 'The connection was lost.');
}

export const api = {
  health: () => call<G.OkOut>(R('health_api_health_get')),
  session: () => call<Session>(R('session_api_session_get')),
  register: (b: G.Credentials) => call<G.UserOut>(R('register_api_auth_register_post'), { json: b }),
  login: (b: G.Credentials) => call<G.UserOut>(R('login_api_auth_login_post'), { json: b }),
  logout: () => call<G.OkOut>(R('logout_api_auth_logout_post')),
  me: () => call<Me>(R('me_api_me_get')),
  liaraCheck: (b: G.LiaraCheck) => call<LiaraCheckResult>(R('liara_check_api_liara_check_post'), { json: b }),
  settings: () => call<SettingsOut>(R('get_settings__api_settings_get')),
  saveSettings: (b: G.SettingsIn) => call<SettingsOut>(R('put_settings_api_settings_put'), { json: b }),
  setAlertWebhook: (url: string) => call<SettingsOut>(R('put_alert_webhook_api_settings_alert_webhook_put'), { json: { url } }),
  deleteAlertWebhook: () => call<SettingsOut>(R('delete_alert_webhook_api_settings_alert_webhook_delete')),
  testAlertWebhook: () => call<G.OkOut>(R('test_alert_webhook_api_settings_alert_webhook_test_post')),
  fixPreview: (id: number, tool: string, args: Record<string, unknown>) => call<G.ChatActionOut>(R('fix_preview_api_projects__pid__fixes_preview_post'), { args: { pid: id }, json: { tool, args } }),
  fixApply: (id: number, tool: string, args: Record<string, unknown>, value?: string) => call<G.FixAppliedOut>(R('fix_apply_api_projects__pid__fixes_apply_post'), { args: { pid: id }, json: { tool, args, ...(value ? { value } : {}) } }),
  domainGuide: (id: number, domain: string) => call<G.DomainGuidesOut>(R('domains_guide_api_projects__pid__domains_guide_get'), { args: { pid: id }, query: { domain } }),
  diagnosisLatest: (id: number) => call<G.LatestDiagnosisOut>(R('live_diagnosis_latest_api_projects__pid__diagnosis_latest_get'), { args: { pid: id } }),
  appLimits: (id: number) => call<G.AppLimitsOut>(R('project_limits_api_projects__pid__limits_get'), { args: { pid: id } }),
  analyzeDiagnosis: (id: number) => call<G.AnalysisOut>(R('live_diagnosis_analyze_api_projects__pid__diagnosis_analyze_post'), { args: { pid: id } }),
  diagnosis: (id: number) => call<G.LiveDiagnosisOut>(R('live_diagnosis_get_api_projects__pid__diagnosis_get'), { args: { pid: id } }),
  metrics: (id: number, hours: number) => call<G.MetricsOut>(R('project_metrics_api_projects__pid__metrics_get'), { args: { pid: id }, query: { hours } }),
  prPreview: (id: number) => call<G.PrPreviewOut>(R('pr_preview_api_projects__pid__pr_get'), { args: { pid: id } }),
  prCreate: (id: number, token: string) => call<G.PrCreatedOut>(R('pr_create_api_projects__pid__pr_post'), { args: { pid: id }, json: { confirm: true, token } }),
  deploys: (id: number) => call<G.DeploysOut>(R('deploys_get_api_projects__pid__deploys_get'), { args: { pid: id } }),
  redeploy: (id: number) => call<G.StartedOut>(R('redeploy_now_api_projects__pid__redeploy_post'), { args: { pid: id } }),
  githubLogin: (mode: 'signin' | 'connect', invite = '', next = '') => call<{ url: string }>(R('github_login_api_github_login_get'), { query: { mode, invite, next } }),
  githubRepos: () => call<G.GithubReposOut>(R('github_repos_api_github_repos_get')),
  githubDisconnect: () => call<SettingsOut>(R('github_disconnect_api_github_connection_delete')),
  wpPlan: (id: number, uploads: boolean) => call<G.WpPlanOut>(R('wp_plan_api_projects__pid__wordpress_get'), { args: { pid: id }, query: { uploads: uploads ? 'true' : 'false' } }),
  wpUpload: (id: number, b: G.WpUploadIn) => call<G.WpJobOut>(R('wp_upload_api_projects__pid__wordpress_upload_post'), { args: { pid: id }, json: b }),
  wpStatus: (id: number) => call<G.WpJobOut>(R('wp_upload_status_api_projects__pid__wordpress_upload_get'), { args: { pid: id } }),
  deleteToken: () => call<G.OkOut>(R('delete_token_api_settings_liara_token_delete')),
  networks: () => call<G.NetworksOut>(R('networks_api_liara_networks_get')),
  limits: () => call<Limits>(R('limits_api_limits_get')),
  projects: () => call<{ projects: ProjectRow[] }>(R('list_projects_api_projects_get')),
  project: (id: number) => call<ProjectDetail>(R('get_project_api_projects__pid__get'), { args: { pid: id } }),
  createProject: (form: FormData) => call<G.ProjectCreatedOut>(R('create_project_api_projects_post'), { form }),
  deleteProject: (id: number, confirm: string) => call<G.ProjectDeletedOut>(R('delete_project_api_projects__pid__delete'), { args: { pid: id }, query: { confirm } }),
  oneclickApps: () => call<G.OneClickListOut>(R('oneclick_list_api_oneclick_get')),
  createOneClick: (app: string, name: string) => call<G.ProjectCreatedOut>(R('create_oneclick_project_api_projects_oneclick_post'), { json: { app, name } }),
  startRun: (id: number, b: G.RunIn) => call<G.RunCreatedOut>(R('start_run_api_projects__pid__runs_post'), { args: { pid: id }, json: b }),
  runs: () => call<{ runs: RunRow[] }>(R('list_runs_api_runs_get')),
  run: (id: string) => call<RunDetail>(R('get_run_api_runs__rid__get'), { args: { rid: id } }),
  runEvents: (id: string, after = 0) => call<{ events: RunEvent[] }>(R('run_events_api_runs__rid__events_get'), { args: { rid: id }, query: { after } }),
  cancelRun: (id: string) => call<G.OkOut>(R('cancel_run_api_runs__rid__cancel_post'), { args: { rid: id } }),
  respond: (id: string, b: G.RespondIn) => call<G.OkOut>(R('respond_api_runs__rid__respond_post'), { args: { rid: id }, json: b }),
  uptime: (id: number) => call<G.UptimeOut>(R('uptime_api_projects__pid__uptime_get'), { args: { pid: id } }),
  uptimeCheck: (id: number) => call<G.UptimeOut>(R('uptime_now_api_projects__pid__uptime_check_post'), { args: { pid: id } }),
  previewBranch: (id: number, branch: string) => call<G.ProjectCreatedOut>(R('preview_branch_api_projects__pid__branches_post'), { args: { pid: id }, json: { branch } }),
  releases: (id: number) => call<G.ReleasesOut>(R('list_project_releases_api_projects__pid__releases_get'), { args: { pid: id } }),
  rollback: (id: number, rid: string) => call<G.RollbackOut>(R('rollback_release_api_projects__pid__releases__rid__rollback_post'), { args: { pid: id, rid }, json: { confirm: true } }),
  domains: (id: number) => call<G.DomainsOut>(R('list_domains_api_projects__pid__domains_get'), { args: { pid: id } }),
  planDomain: (id: number, b: G.DomainPlanIn) => call<G.DomainPlanOut>(R('plan_domain_api_projects__pid__domains_plan_post'), { args: { pid: id }, json: b }),
  connectDomain: (id: number, b: G.DomainCreateIn) => call<G.DomainOut>(R('connect_domain_api_projects__pid__domains_post'), { args: { pid: id }, json: b }),
  checkDomain: (id: number, did: string) => call<G.DomainOut>(R('check_domain_api_projects__pid__domains__did__check_post'), { args: { pid: id, did } }),
  domainSsl: (id: number, did: string) => call<G.DomainOut>(R('enable_ssl_api_projects__pid__domains__did__ssl_post'), { args: { pid: id, did } }),
  dnsCheck: (id: number, did: string) => call<G.DnsCheckOut>(R('dns_check_api_projects__pid__domains__did__dns_check_get'), { args: { pid: id, did } }),
  domainRemoval: (id: number, did: string) => call<G.RemovalPlanOut>(R('removal_plan_api_projects__pid__domains__did__removal_get'), { args: { pid: id, did } }),
  detachDomain: (id: number, did: string, removeRecords = false) => call<G.DomainRemovedOut>(R('detach_domain_api_projects__pid__domains__did__delete'), { args: { pid: id, did }, query: { remove_records: String(removeRecords) } }),
  chat: (projectId: number) => call<G.ChatOut>(R('chat_get_api_chat_get'), { query: { project_id: projectId } }),
  chatSend: (projectId: number, text: string) => call<G.ChatOut>(R('chat_send_api_chat_messages_post'), { json: { project_id: projectId, text } }),
  chatDecide: (projectId: number, actionId: string, b: { decision: 'approve' | 'decline'; value?: string; confirm_name?: string }) => call<G.ChatOut>(R('chat_decide_api_chat_actions__action_id__post'), { args: { action_id: actionId }, json: { project_id: projectId, ...b } }),
  chatSendLive: (projectId: number, text: string, onEvent: (e: ChatEvent) => void) => chatStream('/api/chat/messages/stream', { project_id: projectId, text }, onEvent),
  chatDecideLive: (projectId: number, actionId: string, b: { decision: 'approve' | 'decline'; value?: string; confirm_name?: string }, onEvent: (e: ChatEvent) => void) => chatStream(`/api/chat/actions/${encodeURIComponent(actionId)}/stream`, { project_id: projectId, ...b }, onEvent),
  chatClear: (projectId: number) => call<G.OkOut>(R('chat_clear_api_chat_delete'), { query: { project_id: projectId } }),
  explain: (id: string) => call<G.ExplainOut>(R('explain_run_api_runs__rid__explain_post'), { args: { rid: id } }),
  cleanup: (id: string, b: G.CleanupIn) => call<G.CleanupOut>(R('cleanup_api_runs__rid__cleanup_post'), { args: { rid: id }, json: b }),
};
