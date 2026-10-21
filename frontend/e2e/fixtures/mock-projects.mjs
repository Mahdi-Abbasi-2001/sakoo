// Hand-written API answers for the dashboard and the project page, in the shape of docs/api.md (a real backend cannot be put into
// "live" or "failed for lack of balance" without spending on Liara).
import fs from 'node:fs'; import path from 'node:path';
export function buildMock() {
const now = Math.floor(Date.now() / 1000), day = 86400;
const buildEvents = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../src/demo/samples/events_build_timeout_then_fixed.json'), 'utf8'));
const created = { apps: ['api'], databases: ['api-db'], database_ids: ['db1'] };
const live = { run_id: 'run4', urls: { api: 'https://api.liara.run/' }, resources: [{ kind: 'app', id: 'api', plan: 'small-g2', tier: 'standard', tier_label: 'silver', hourly_price: 1451.38 }, { kind: 'database', id: 'api-db', plan: 'small-g2', tier: 'basic', tier_label: 'basic', hourly_price: 1145.83 }], hourly_incl_vat: 2597.21, monthly_incl_vat: 1870000, free_address_expires_at: now + 6 * day };
const row = (id, mode, status, ago, result) => ({ id, mode, status, created_at: now - ago, finished_at: status === 'waiting' ? null : now - ago + 40, cleaned_at: null, result });
const R = (o) => ({ ok: null, stopped: null, reason_code: null, reason_params: {}, failed_step: null, diagnosis_code: null, created: { apps: [], databases: [], database_ids: [] }, urls: {}, dry_run: false, fixes: [], heal_attempts: 0, ...o });
const projects = [
  { id: 1, name: 'blog', source_kind: 'git', git_url: 'https://github.com/acme/blog', created_at: now - 3600, last_run: row('run10', 'deploy', 'waiting', 120, null), live: null },
  { id: 2, name: 'api', source_kind: 'zip', git_url: null, created_at: now - 3 * day, last_run: row('run4', 'deploy', 'succeeded', day, R({ ok: true, created, urls: { api: 'https://api.liara.run/' }, fixes: [{ code: 'resize_tier', kind: 'resize', params: { app: 'api', tier_label: 'silver', from_tier_label: 'basic', build_minutes: 15, from_build_minutes: 5 }, cost_delta_hourly: 305.56 }], heal_attempts: 1 })), live },
  { id: 3, name: 'shop', source_kind: 'git', git_url: 'https://github.com/acme/shop', created_at: now - 2 * day, last_run: row('run7', 'deploy', 'failed', 3 * 3600, R({ ok: false, stopped: 'error', reason_code: 'insufficient_balance' })), live: null },
  { id: 4, name: 'notes-app', source_kind: 'zip', git_url: null, created_at: now - 5 * day, last_run: row('run8', 'deploy', 'cancelled', 2 * day, R({ ok: false, stopped: 'cancelled', reason_code: 'not_approved' })), live: null },
];
const detail = { ...projects[1], runs: [row('run5', 'preview', 'succeeded', 3600, R({ ok: true, dry_run: true })), row('run4', 'deploy', 'succeeded', day, R({ ok: true, created, urls: { api: 'https://api.liara.run/' }, fixes: [{ code: 'resize_tier', kind: 'resize', params: { app: 'api', tier_label: 'silver', from_tier_label: 'basic', build_minutes: 15, from_build_minutes: 5 }, cost_delta_hourly: 305.56 }] })), row('run3', 'deploy', 'failed', day + 3600, R({ ok: false, stopped: 'error', reason_code: 'insufficient_balance' })), row('run2', 'deploy', 'cancelled', 3 * day, R({ ok: false, stopped: 'cancelled', reason_code: 'not_approved' })), row('run1', 'deploy', 'interrupted', 3 * day + 600, null)].map((r) => ({ ...r, project_id: 2, project: 'api', summary: null })) };
const table = {
  'GET /api/projects': { projects },
  'GET /api/projects/2': detail,
  'GET /api/runs/run10': { id: 'run10', project_id: 1, project: 'blog', mode: 'deploy', status: 'waiting', created_at: now - 120, finished_at: null, cleaned_at: null, result: null, summary: null, open_requests: [{ id: 10, ts: now - 120, kind: 'confirm_request', data: { id: 'x', prompt: '', kind: 'plan', price_delta_hourly: 4583, code: 'create_resources', params: {}, expires_at: now + 14 * 60 + 32 } }] },
  'GET /api/runs/run4/events': { events: buildEvents },
};

  return table;
}
