import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { reduceRun, maskSecrets, applyRemoval } from './reduceRun';
import { situation } from './RunView';
import { describeRun } from '../projects/describe';
import type { RunEvent } from '../api/events';

const dir = path.resolve(__dirname, '../demo/samples');
const load = (f: string) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')) as RunEvent[];
const waiting = load('events_waiting_for_approval.json');
const dry = load('events_dry_run_compose.json');
const build = load('events_build_timeout_then_fixed.json');
const state = (id: string, pipe: ReturnType<typeof reduceRun>['pipeline']) => pipe.find((n) => n.id === id)!.state;
const upTo = (evs: RunEvent[], kind: string, nth = 1) => { let seen = 0; for (let i = 0; i < evs.length; i++) if (evs[i].kind === kind && ++seen === nth) return evs.slice(0, i + 1); throw new Error('no ' + kind); };

describe('the samples match docs/samples', () => {
  it('copies are identical to the docs', () => {
    const docs = path.resolve(__dirname, '../../../docs/samples');
    if (!fs.existsSync(docs)) return;
    for (const f of fs.readdirSync(docs).filter((x) => x.endsWith('.json'))) expect(fs.readFileSync(path.join(dir, f), 'utf8')).toBe(fs.readFileSync(path.join(docs, f), 'utf8'));
  });
});

describe('waiting for approval (events_waiting_for_approval)', () => {
  const s = reduceRun(waiting);
  it('pipeline: analyze and plan done, confirm waiting, the rest pending', () => {
    expect(['analyze', 'plan', 'confirm', 'provision', 'deploy', 'verify', 'live'].map((id) => state(id, s.pipeline))).toEqual(['ok', 'ok', 'waiting', 'pending', 'pending', 'pending', 'pending']);
    expect(s.pipeline[0].count).toBe(2);                       // docker-compose.yml and web/Dockerfile
    expect([s.pipeline[1].apps, s.pipeline[1].databases]).toEqual([2, 2]);
  });
  it('status, agent and the one open request', () => {
    expect(s.status).toBe('waiting'); expect(s.agent).toBe('waiting');
    expect(s.openRequests).toHaveLength(1);
    const reqEv = waiting.find((e) => e.kind === 'confirm_request')!.data as { id: string; expires_at?: number };
    expect(s.openRequests[0]).toMatchObject({ kind: 'confirm', id: reqEv.id, confirmKind: 'plan' });
    expect(s.openRequests[0].expiresAt).toBe(reqEv.expires_at);          // the server's moment, straight from the event
    expect(s.openRequests[0].prompt).toContain('4,583');
  });
  it('the answered question is not open and is recorded', () => {
    expect(s.openRequests.some((r) => r.kind === 'question')).toBe(false);
    const q = s.timeline.find((t) => t.type === 'question');
    expect(q && q.type === 'question' && q.answer).toBe('worker');
  });
  it('cost totals come from plan_ready', () => {
    expect(s.money.plannedHourly).toBe(4583); expect(s.money.plannedMonthly).toBe(3300000);
    expect(s.cost!.lines).toHaveLength(4);
    expect(s.resources.filter((r) => r.kind !== 'disk').every((r) => r.hourly === 1145.83 && r.state === 'proposed')).toBe(true);
    expect(s.money.currentHourly).toBe(0);                      // nothing exists yet
  });
  it('the disk has no price and is marked unverified', () => {
    const disk = s.resources.find((r) => r.kind === 'disk')!;
    expect(disk).toMatchObject({ id: 'uploads', mountTo: '/app/uploads', ownerApp: 'blog-web', hourly: null, priceUnverified: true });
  });
  it('architecture edges: HTTPS, the database url and the redis host', () => {
    const e = s.edges.map((x) => `${x.from}>${x.to}:${x.label}`);
    expect(e).toEqual(expect.arrayContaining(['internet>blog-web:HTTPS', 'blog-web>blog-db:DATABASE_URL', 'blog-worker>blog-db:DATABASE_URL', 'blog-web>blog-cache:REDIS_HOST']));
    expect(s.edges.every((x) => x.inferred === false)).toBe(true);        // plan.links says so: nothing is guessed from a variable name any more
  });
  it('an older plan without links still gets edges, marked inferred', () => {
    const old = JSON.parse(JSON.stringify(waiting)) as RunEvent[]; for (const e of old) if (e.kind === 'plan_ready') delete (e.data as { plan: { links?: unknown } }).plan.links;
    const edges = reduceRun(old).edges; expect(edges.find((x) => x.label === 'REDIS_HOST')!.inferred).toBe(true); expect(edges.find((x) => x.label === 'DATABASE_URL')!.inferred).toBe(false);
  });
  it('requests that would be sent are counted', () => { expect(s.wouldSend).toHaveLength(10); });
  it('reload: the open request comes back from the events alone', () => {
    const again = reduceRun(JSON.parse(JSON.stringify(waiting)));
    expect(again.openRequests).toEqual(s.openRequests);
  });
});

describe('dry run (events_dry_run_compose)', () => {
  const s = reduceRun(dry);
  it('finishes without ever opening a request, nothing is live', () => {
    expect(s.status).toBe('succeeded'); expect(s.dryRun).toBe(true); expect(s.openRequests).toHaveLength(0);
    expect(['provision', 'deploy', 'verify', 'live'].map((id) => state(id, s.pipeline))).toEqual(['ok', 'ok', 'skipped', 'skipped']);
    expect(s.resources.filter((r) => r.kind !== 'disk').every((r) => r.state === 'live' && r.dry)).toBe(true);
    expect(s.result!.wouldSend.length).toBeGreaterThan(0);
  });
});

describe('build timeout, fixed by moving to silver (events_build_timeout_then_fixed)', () => {
  it('a secret request is open until it is provided, and only its name is kept', () => {
    const at = upTo(build, 'secret_request');
    const open = reduceRun(at).openRequests;
    expect(open).toHaveLength(1); expect(open[0]).toMatchObject({ kind: 'secret', name: 'JWT_SECRET' });
    expect(reduceRun(upTo(build, 'secret_provided')).openRequests).toHaveLength(0);
  });
  it('after the diagnosis, the fix is an open request with its price and the deploy is failed', () => {
    const s = reduceRun(upTo(build, 'confirm_request', 2));
    expect(s.openRequests).toHaveLength(1);
    expect(s.openRequests[0]).toMatchObject({ kind: 'confirm', confirmKind: 'fix' });
    expect(s.openRequests[0].priceDeltaHourly).toBeCloseTo(305.558, 2);
    expect(state('deploy', s.pipeline)).toBe('fail');
    expect(s.diagnoses[0].code).toBe('build_timeout');
    expect(s.resources.find((r) => r.id === 'api')!.state).toBe('failed');
    expect(s.status).toBe('waiting');
  });
  const s = reduceRun(build);
  it('the resource_updated price change is applied and remembered', () => {
    const api = s.resources.find((r) => r.id === 'api')!;
    expect(api).toMatchObject({ tier: 'standard', tierLabel: 'silver', hourly: 1451.38, state: 'live' });
    expect(api.previous).toMatchObject({ tier: 'basic', hourly: 1145.83 });
    expect(s.money.fixDeltaHourly).toBeCloseTo(305.56, 2);
    expect(s.money.currentHourly).toBeCloseTo(1145.83 + 1451.38, 2);
  });
  it('monthly prices come from the events (monthly_price), and heal_attempt carries the maximum', () => {
    const api = s.resources.find((r) => r.id === 'api')!; expect(api.monthly).toBe(1045000);
    expect(s.maxAttempts).toBe(3);
  });
  it('finishes live: every step ok, the test passed, the url is known', () => {
    expect(s.status).toBe('succeeded'); expect(s.agent).toBe('done');
    expect(s.pipeline.map((n) => n.state)).toEqual(['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok']);
    expect(s.tests).toEqual([expect.objectContaining({ name: 'http_200', ok: true, url: 'https://api.liara.run/' })]);
    expect(s.urls).toEqual({ api: 'https://api.liara.run/' });
    expect(s.healAttempts).toBe(1); expect(s.fixesApplied).toHaveLength(1);
  });
});

describe('declining is a calm outcome, not an error', () => {
  const evs: RunEvent[] = [...waiting,
    { id: 11, ts: waiting[waiting.length - 1].ts + 3, kind: 'confirm_response', data: { id: (waiting.at(-1)!.data as { id: string }).id, accepted: false } },
    { id: 12, ts: waiting[waiting.length - 1].ts + 3, kind: 'phase_finished', data: { phase: 'confirm', approved: false } },
    { id: 13, ts: waiting[waiting.length - 1].ts + 3, kind: 'run_finished', data: { ok: false, dry_run: false, created: { apps: [], databases: [], database_ids: [] }, stopped: 'cancelled', reason_code: 'not_approved' } }];
  const s = reduceRun(evs);
  it('status cancelled, agent done (not failed), confirm declined, deploy steps skipped', () => {
    expect(s.status).toBe('cancelled'); expect(s.agent).toBe('done');
    expect(['confirm', 'provision', 'deploy', 'verify', 'live'].map((id) => state(id, s.pipeline))).toEqual(['declined', 'skipped', 'skipped', 'skipped', 'skipped']);
    expect(s.openRequests).toHaveLength(0); expect(s.resources.every((r) => r.state === 'proposed')).toBe(true);
  });
});

describe('failures that are not the end of the world, and ones that are', () => {
  const upToFix = upTo(build, 'confirm_request', 2);
  const last = upToFix.at(-1)!;
  const mk = (kind: string, data: unknown, i: number) => ({ id: last.id + i, ts: last.ts + i, kind, data }) as RunEvent;
  it('declining a fix ends the run as failed, with a diagnosis and no stopped field (the backend contract)', () => {
    const dg = (upToFix.find((e) => e.kind === 'diagnosis')!.data) as { code: string };
    const evs = [...upToFix, mk('confirm_response', { id: (last.data as { id: string }).id, accepted: false }, 1), mk('fix_declined', { fix: 'move api to silver', note: '', kind: 'resize', params: {}, cost_delta_hourly: 305.56, app: 'api', code: 'resize_tier' }, 2),
      mk('run_finished', { ok: false, dry_run: false, failed_step: 'app:api', reason_code: dg.code, created: { apps: ['api'], databases: ['api-db'], database_ids: ['db1'] }, diagnosis: { code: dg.code, params: {}, summary: '', evidence: [], fixes: [] }, heal_attempts: 1 }, 3)];
    const v = reduceRun(evs);
    expect(v.status).toBe('failed'); expect(v.agent).toBe('failed'); expect(v.openRequests).toHaveLength(0);
    expect(v.result).toMatchObject({ ok: false, stopped: null, failedStep: 'app:api', reasonCode: 'build_timeout' });
    expect(v.fixesDeclined).toHaveLength(1); expect(v.resources.find((r) => r.id === 'api')!.state).toBe('failed');
    expect(v.result!.created.apps).toEqual(['api']);                       // what was created before the failure still exists and bills
  });
  it('a step that ends UNHEALTHY or FAILED is a failure; READY and DRYRUN are not; a database step has no state', () => {
    const base = upTo(build, 'step_started', 2);                          // the app step has started
    const ended = (state: string | undefined) => reduceRun([...base, mk('step_finished', { step: 'app:api', ...(state ? { state } : {}), dry_run: false }, 1)]).resources.find((r) => r.id === 'api')!.state;
    expect(ended('UNHEALTHY')).toBe('failed'); expect(ended('FAILED')).toBe('failed'); expect(ended('READY')).toBe('live'); expect(ended('DRYRUN')).toBe('live');
    expect(reduceRun(upTo(build, 'step_finished', 1)).resources.find((r) => r.id === 'api-db')!.state).toBe('live');   // no state: fine
  });
});

describe('secrets never reach the state', () => {
  const SECRET = 'hunter2-very-secret';
  it('a value smuggled into secret_provided is dropped', () => {
    const evs = [...build.slice(0, 13)];
    evs[12] = { ...evs[12], data: { ...(evs[12].data as object), value: SECRET } } as RunEvent;
    expect(JSON.stringify(reduceRun(evs))).not.toContain(SECRET);
  });
  it('tool-call arguments have secret-looking values masked, names kept', () => {
    const masked = maskSecrets('{"env": {"JWT_SECRET": "abc123", "PORT": "3000", "DATABASE_URL": "postgres://$DB_x_USER:$DB_x_PASSWORD@h"}}');
    expect(masked).not.toContain('abc123'); expect(masked).toContain('JWT_SECRET'); expect(masked).toContain('3000'); expect(masked).toContain('$DB_x_PASSWORD');
  });
  it('none of the three sample states contains a field called value, secret value or password text', () => {
    for (const evs of [waiting, dry, build]) { const j = JSON.stringify(reduceRun(evs)); expect(j).not.toMatch(/"value":/); }
  });
});

describe('forward compatibility and replay', () => {
  it('unknown event kinds are ignored', () => {
    const a = reduceRun(waiting); const b = reduceRun([...waiting.slice(0, 5), { id: 99, ts: waiting[5].ts, kind: 'from_the_future', data: { x: 1 } } as unknown as RunEvent, ...waiting.slice(5)]);
    expect(b.pipeline).toEqual(a.pipeline); expect(b.openRequests).toEqual(a.openRequests);
  });
  it('every prefix of every sample reduces without throwing and is deterministic', () => {
    for (const evs of [waiting, dry, build]) for (let n = 0; n <= evs.length; n++) { const p = evs.slice(0, n); expect(reduceRun(p)).toEqual(reduceRun(p)); }
  });
});

describe('after a cleanup (cleaned_at set)', () => {
  const live = reduceRun(build);
  const gone = applyRemoval(live, { at: 1_800_000_000, apps: ['api'], databases: ['api-db'] });
  it('the run was live before, and the cleanup changes only what exists now', () => {
    expect(situation(live)).toBe('live');
    expect(Object.keys(live.urls).length).toBeGreaterThan(0);
    expect(live.money.currentHourly).toBeGreaterThan(0);
  });
  it('is its own situation: no live card, no address, nothing running or billed', () => {
    expect(situation(gone)).toBe('removed');
    expect(gone.urls).toEqual({});
    expect(gone.created).toBeNull();
    expect(gone.money.currentHourly).toBe(0);
    expect(gone.money.currentMonthly).toBe(0);
    expect(gone.resources.filter((r) => r.state === 'live' || r.state === 'creating')).toEqual([]);
    expect(gone.resources.every((r) => r.state === 'removed' || r.state === 'proposed')).toBe(true);
  });
  it('keeps the pipeline and the timeline as history, and says what was deleted and when', () => {
    expect(gone.pipeline).toEqual(live.pipeline);
    expect(gone.timeline).toEqual(live.timeline);
    expect(gone.removed).toEqual({ at: 1_800_000_000, apps: ['api'], databases: ['api-db'] });
    expect(live.removed).toBeNull();      // the overlay does not touch the original
  });
  it('a failed run that was cleaned up is told as removed too', () => {
    const failed = reduceRun(upTo(build, 'diagnosis'));
    expect(situation(applyRemoval(failed, { at: 1, apps: [], databases: [] }))).toBe('removed');
  });
  it('a run list tells a cleaned-up run as removed, never as succeeded or live', () => {
    const base = { status: 'succeeded' as const, mode: 'deploy' as const, result: { created: { apps: ['api'], databases: ['api-db'] } } as never };
    expect(describeRun('en', base).label).toBe('Live');
    const l = describeRun('en', { ...base, cleaned_at: 1_800_000_000 });
    expect(l.label).toBe('Removed'); expect(l.tone).toBe('s-pend'); expect(l.text).toContain('api, api-db');
    expect(describeRun('fa', { ...base, cleaned_at: 1 }).label).toBe('حذف شد');
    expect(describeRun('en', { ...base, mode: 'preview', cleaned_at: 1 }).label).not.toBe('Removed');
  });
});

describe('a project with no database', () => {
  const noDb = waiting.map((e) => (e.kind === 'plan_ready' ? { ...e, data: { ...e.data, plan: { ...(e.data as { plan: object }).plan, databases: [], links: [] } } } : e)) as RunEvent[];
  const v = reduceRun(noDb);
  it('skips the database step instead of leaving it pending', () => {
    expect(state('provision', v.pipeline)).toBe('skipped');
  });
});

describe('a project with no database, during the deploy', () => {
  const evs = build.filter((e) => !(e.kind === 'resource_created' && (e.data as { kind?: string }).kind === 'database') && !String((e.data as { step?: string }).step ?? '').startsWith('database:'))
    .map((e) => (e.kind === 'plan_ready' ? { ...e, data: { ...e.data, plan: { ...(e.data as { plan: object }).plan, databases: [], links: [] } } } : e)) as RunEvent[];
  const mid = upTo(evs, 'resource_created', 1);                // the app is being created
  const v = reduceRun(mid);
  it('never shows the database step as the one in progress', () => {
    expect(state('provision', v.pipeline)).toBe('skipped');
    expect(state('deploy', v.pipeline)).not.toBe('pending');
  });
});

describe('buckets and email servers', () => {
  const ev = (id: number, kind: string, data: Record<string, unknown>) => ({ id, ts: 1000 + id, kind, data }) as unknown as RunEvent;
  const plan = {
    apps: [{ name: 'shop', path: '.', platform: 'node', port: 3000, build_command: null, start_command: null, image: null, args: [], env: [{ name: 'AWS_BUCKET', reference: '$S3_shop-files_BUCKET', has_value: true }], disks: [], role: 'web', evidence: [] }],
    databases: [], storage: [{ id: 'shop-files', permission: 'private', evidence: [] }], mail: { domain: 'my.com', mode: 'DEV', evidence: [] },
    links: [{ from: 'shop', to: 'shop-files', to_kind: 'bucket', env: ['AWS_BUCKET'] }, { from: 'shop', to: 'my-com', to_kind: 'mail', env: ['MAIL_HOST'] }],
    env: [], questions: [], notes: [],
  };
  const cost = { lines: [{ kind: 'app', name: 'shop', plan: 'small-g2', tier: 'basic', tier_label: 'basic', hourly_incl_vat: 1146, monthly_incl_vat: 825000 },
    { kind: 'bucket', name: 'shop-files', plan: '20g', tier: '', tier_label: '', hourly_incl_vat: 726, monthly_incl_vat: 522500 },
    { kind: 'mail', name: 'my.com', plan: 'm2', tier: '', tier_label: '', hourly_incl_vat: 2253, monthly_incl_vat: 1622500 }], total_hourly_incl_vat: 4125, total_monthly_incl_vat: 2970000 };
  const events = [
    ev(1, 'run_started', { project: 'shop', dry_run: false }),
    ev(2, 'plan_ready', { how: 'investigated by the agent', how_code: 'investigated_by_agent', how_params: {}, plan, cost, disks: [], readiness: { findings: [], counts: {} }, network_id: null, would_send: [], usage: null }),
    ev(3, 'step_started', { step: 'bucket:shop-files' }),
    ev(4, 'resource_created', { kind: 'bucket', id: 'shop-files', plan: '20g', tier: '', tier_label: '', hourly_price: 726, monthly_price: 522500, dry_run: false }),
    ev(5, 'step_finished', { step: 'bucket:shop-files', dry_run: false }),
    ev(6, 'step_started', { step: 'mail:my.com' }),
    ev(7, 'resource_created', { kind: 'mail', id: 'my.com', plan: 'm2', tier: '', tier_label: '', hourly_price: 2253, monthly_price: 1622500, dry_run: false }),
    ev(8, 'step_finished', { step: 'mail:my.com', dry_run: false }),
    ev(9, 'run_finished', { ok: true, dry_run: false, created: { apps: ['shop'], databases: [], database_ids: [], buckets: ['shop-files'], mail_servers: ['my.com'] }, urls: {},
      follow_ups: [{ code: 'mail_dns', params: { domain: 'my.com' }, records: [{ type: 'MX', name: '@', value: '10 c1.liara.email', status: 'VALID', purpose: 'mx' }] }] }),
  ];
  const s = reduceRun(events);
  it('plans and creates them as resources next to the apps, with their cost', () => {
    const byId = (id: string) => s.resources.find((r) => r.id === id)!;
    expect([byId('shop-files').kind, byId('shop-files').monthly, byId('shop-files').state]).toEqual(['bucket', 522500, 'live']);
    expect([byId('my.com').kind, byId('my.com').monthly, byId('my.com').state]).toEqual(['mail', 1622500, 'live']);
  });
  it('provisioning covers them (they are created before the app)', () => {
    expect(s.pipeline.find((n) => n.id === 'provision')!.subs.map((x) => x.id).sort()).toEqual(['my.com', 'shop-files']);
  });
  it('draws an edge to each: the mail server by its domain, not by its placeholder id', () => {
    expect(s.edges.filter((e) => e.from === 'shop').map((e) => e.to).sort()).toEqual(['my.com', 'shop-files']);
  });
  it('keeps what the person still has to do', () => {
    expect(s.result!.followUps).toEqual([{ code: 'mail_dns', params: { domain: 'my.com' }, records: [{ type: 'MX', name: '@', value: '10 c1.liara.email', status: 'VALID', purpose: 'mx' }] }]);
    expect(s.result!.created.buckets).toEqual(['shop-files']);
  });
  it('a clean-up marks them removed too', () => {
    const r = applyRemoval(s, { at: 2000, apps: ['shop'], databases: [], buckets: ['shop-files'], mail_servers: ['my.com'] });
    expect(['shop-files', 'my.com'].map((id) => r.resources.find((x) => x.id === id)!.state)).toEqual(['removed', 'removed']);
  });
});

describe('a release of new code on a running app (a run of its own)', () => {
  const plan = build.find((e) => e.kind === 'plan_ready')!;
  const app = (plan.data as never as { plan: { apps: { name: string }[] } }).plan.apps[0].name;
  const nothing = { apps: [], databases: [], database_ids: [], buckets: [], mail_servers: [] };
  const ev = (id: number, kind: string, data: Record<string, unknown>) => ({ id, ts: 1000 + id, kind, data }) as unknown as RunEvent;
  const head = [ev(1, 'run_started', { project: 'p', dry_run: false, release: true }), ev(2, 'phase_started', { phase: 'analyze' }), ev(3, 'phase_finished', { phase: 'analyze', manifests: {}, hints: [] }),
    ev(4, 'phase_started', { phase: 'plan' }), { ...plan, id: 5, ts: 1005 } as RunEvent, ev(6, 'phase_finished', { phase: 'plan' }), ev(7, 'phase_started', { phase: 'confirm' }),
    ev(8, 'phase_finished', { phase: 'confirm', approved: true }), ev(9, 'phase_started', { phase: 'deploy' }), ev(10, 'step_started', { step: `app:${app}` }), ev(11, 'build', { step: `app:${app}`, line: 'Step 1/3' })];
  it('follows the build, then ends live with nothing to delete', () => {
    const building = reduceRun(head);
    expect(building.release).toBe(true);
    expect(building.status).toBe('running');
    const done = reduceRun([...head, ev(12, 'test_result', { step: `app:${app}`, name: 'http_200', ok: true, detail: 'HTTP 200', url: 'https://x.liara.run/' }), ev(13, 'step_finished', { step: `app:${app}`, state: 'READY', dry_run: false }),
      ev(14, 'phase_finished', { phase: 'deploy', ok: true }), ev(15, 'run_finished', { ok: true, dry_run: false, release: true, created: nothing, urls: { [app]: 'https://x.liara.run/' } })]);
    expect(done.status).toBe('succeeded');
    expect(done.result?.created.apps).toEqual([]);
    expect(situation(done)).toBe('live');
  });
  it('a failed release says so and keeps the earlier release', () => {
    const failed = reduceRun([...head, ev(12, 'step_finished', { step: `app:${app}`, state: 'FAILED', dry_run: false }), ev(13, 'phase_finished', { phase: 'deploy', ok: false }),
      ev(14, 'run_finished', { ok: false, dry_run: false, release: true, stopped: 'error', reason_code: 'release_failed', reason_params: { state: 'FAILED', detail: 'FAILED' }, created: nothing, urls: {} })]);
    expect(failed.status).toBe('failed');
    expect(failed.result?.reasonCode).toBe('release_failed');
  });
});
