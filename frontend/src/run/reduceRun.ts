import type { RunEvent, PlanView, Phase } from '../api/events';
import type { AgentState, HandoffView, Removal, Edge, LogLine, NodeState, OpenReq, PipeNode, Resource, RunStatusView, RunViewState, TestRow, TimelineItem } from './types';

/** The samples price a month as 720 hours (825,000 toman / 1,145.83 per hour = 720.0), so a price changed by a fix is turned into a month the same way. */
export const HOURS_PER_MONTH = 720;
const SECRETISH = /secret|token|password|passwd|key|credential/i;

/** Mask the value of anything that looks like a secret inside a JSON string (the model's tool-call arguments), keeping the names. */
export function maskSecrets(s: string): string {
  return s.replace(/("([^"\\]*)"\s*:\s*)("(?:[^"\\]|\\.)*"|[^,}\]\s{\[]+)/g, (m, pre: string, name: string) => (SECRETISH.test(name) && !/\$[A-Z_]/i.test(m) ? `${pre}"•••"` : m));
}

const PIPE_IDS = ['analyze', 'plan', 'confirm', 'provision', 'deploy', 'verify', 'live'] as const;
type Pid = (typeof PIPE_IDS)[number];

function emptyPipeline(): PipeNode[] { return PIPE_IDS.map((id) => ({ id, state: 'pending' as NodeState, subs: [] })); }

export function initialRunState(): RunViewState {
  return {
    events: 0, lastEventId: 0, t0: null, tLast: null, project: null, dryRun: null, release: false, started: false, status: 'running', agent: 'reading', phase: null,
    pipeline: emptyPipeline(), plan: null, how: null, howCode: null, howParams: {}, planNotes: [], readiness: [], wouldSend: [], network: null, networkNew: false, cost: null, usage: null, disks: [],
    resources: [], edges: [], money: { plannedHourly: null, plannedMonthly: null, currentHourly: 0, currentMonthly: 0, fixDeltaHourly: 0 }, removed: null,
    timeline: [], logs: [], tests: [], openRequests: [], handoff: null, diagnoses: [], fixesApplied: [], fixesDeclined: [], healAttempts: 0, maxAttempts: null, urls: {}, created: null, result: null,
  };
}

function describe(e: RunEvent): { text: string; tone: LogLine['tone'] } {
  const d = e.data as Record<string, unknown>;
  const kv = (o: Record<string, unknown>) => Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== '').map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`).join(' ');
  switch (e.kind) {
    case 'run_started': return { text: kv({ project: d.project, dry_run: d.dry_run }), tone: '' };
    case 'phase_started': return { text: String(d.phase), tone: '' };
    case 'phase_finished': return { text: [String(d.phase), d.manifests ? `manifests=[${Object.entries(d.manifests as Record<string, string[]>).flatMap(([dir, fs]) => fs.map((f) => (dir === '.' ? f : `${dir}/${f}`))).join(', ')}]` : '', d.hints ? `hints=[${(d.hints as string[]).join(', ')}]` : '', d.approved !== undefined ? `approved=${d.approved}` : ''].filter(Boolean).join(' '), tone: 'ok' };
    case 'plan_ready': return { text: `${String(d.how)}: ${(d.plan as PlanView).apps.length} apps, ${(d.plan as PlanView).databases.length} databases`, tone: 'ok' };
    case 'tool_call': return { text: `${String(d.name)} ${maskSecrets(String(d.arguments ?? '')).slice(0, 160)}`, tone: '' };
    case 'build': return { text: `${String(d.step)} ${String(d.line)}`, tone: '' };
    case 'build_error': return { text: `${String(d.step)} ${String(d.line)}`, tone: 'fail' };
    case 'question': return { text: String(d.text), tone: 'wait' };
    case 'answered': return { text: `answer=${d.value ?? ''}`, tone: 'ok' };
    case 'confirm_request': return { text: String(d.prompt), tone: 'wait' };
    case 'confirm_response': return { text: d.accepted ? 'accepted' : 'declined', tone: d.accepted ? 'ok' : 'warn' };
    case 'secret_request': return { text: `name=${String(d.name)}`, tone: 'wait' };
    case 'secret_provided': return { text: `name=${String(d.name)} provided=${String(d.provided)}`, tone: 'ok' };   // never the value
    case 'resource_created': return { text: kv({ kind: d.kind, id: d.id, plan: d.plan, tier: d.tier_label, hourly: d.hourly_price }), tone: 'ok' };
    case 'resource_updated': return { text: kv({ kind: d.kind, id: d.id, tier: d.tier_label, hourly: d.hourly_price }), tone: 'warn' };
    case 'test_result': return { text: `${String(d.name)} ${String(d.detail)}`, tone: d.ok ? 'ok' : 'fail' };
    case 'diagnosis': return { text: `${String(d.code)} ${String(d.summary)}`, tone: 'fail' };
    case 'fix_applied': return { text: String(d.fix), tone: 'ok' };
    case 'fix_declined': return { text: String(d.fix), tone: 'warn' };
    case 'run_finished': return { text: kv({ ok: d.ok, dry_run: d.dry_run, reason_code: d.reason_code }), tone: d.ok ? 'ok' : (d.stopped === 'cancelled' ? 'warn' : 'fail') };
    default: return { text: kv(d as Record<string, unknown>).slice(0, 200), tone: '' };
  }
}

/** Turn the list of events into everything the run page shows. Pure: the same events always give the same state, for a live stream and for a replay. */
export function reduceRun(events: readonly RunEvent[]): RunViewState {
  const S = initialRunState();
  const pipe = new Map<Pid, PipeNode>(S.pipeline.map((n) => [n.id, n]));
  const node = (id: Pid) => pipe.get(id)!;
  const res = new Map<string, Resource>();
  const stepOf = new Map<string, string>();          // resource id -> step name
  const open: OpenReq[] = [];
  let questionAnswers = new Map<string, string | null>();
  let finished = false;
  let lastDiagnosisApp: string | null = null;
  let attempt: number | null = null;
  let autoNext = false;     // fix_auto_applied comes right before the fix_applied it belongs to
  const tl = S.timeline;
  const push = (it: TimelineItem) => { tl.push(it); };
  const setNode = (id: Pid, state: NodeState) => { node(id).state = state; };
  const resKey = (kind: string, id: string) => `${kind}:${id}`;
  const sortedByStart = [...events];

  function addResource(r: Partial<Resource> & { id: string; kind: Resource['kind'] }) {
    const k = resKey(r.kind, r.id);
    const cur = res.get(k) ?? { id: r.id, kind: r.kind, type: null, role: null, port: null, version: null, path: null, mountTo: null, ownerApp: null, plan: null, tier: null, tierLabel: null, hourly: null, monthly: null, priceUnverified: false, state: 'proposed' as const, dry: false, previous: null, evidence: [], env: [], url: null };
    Object.assign(cur, r);
    res.set(k, cur);
    return cur;
  }
  function stepRes(step: string): Resource | undefined { const [kind, id] = step.split(':'); return kind === 'app' || kind === 'database' || kind === 'bucket' || kind === 'mail' ? res.get(resKey(kind, id)) : undefined; }

  for (const e of sortedByStart) {
    S.events++; S.lastEventId = Math.max(S.lastEventId, e.id);
    if (S.t0 === null) S.t0 = e.ts; S.tLast = e.ts;
    const d = e.data as never as Record<string, any>;
    const { text, tone } = describe(e);
    S.logs.push({ n: e.id, id: e.id, ts: e.ts, rel: e.ts - S.t0, kind: e.kind, text, tone, step: typeof d.step === 'string' ? d.step : null, phase: S.phase });

    switch (e.kind) {
      case 'run_started': S.started = true; S.project = d.project; S.dryRun = !!d.dry_run; S.release = !!d.release; break;
      case 'phase_started': {
        S.phase = d.phase as Phase;
        const id = d.phase as Pid; if (pipe.has(id)) setNode(id, 'running');
        if (d.phase === 'deploy') { setNode('provision', 'pending'); }
        break;
      }
      case 'phase_finished': {
        const ph = d.phase as Phase;
        if (ph === 'analyze') {
          setNode('analyze', 'ok'); node('analyze').count = Object.values((d.manifests ?? {}) as Record<string, string[]>).reduce((n, a) => n + a.length, 0);
          push({ key: 'e' + e.id, type: 'read', state: 'ok', ts: e.ts, manifests: d.manifests ?? {}, hints: d.hints ?? [], envVars: d.env_var_names ?? [] });
        } else if (ph === 'plan') { setNode('plan', 'ok'); }
        else if (ph === 'confirm') {
          if (d.approved === false) { setNode('confirm', 'declined'); for (const id of ['provision', 'deploy', 'verify', 'live'] as Pid[]) setNode(id, 'skipped'); }
          else setNode('confirm', 'ok');
        } else if (ph === 'deploy') {
          for (const id of ['provision', 'deploy'] as Pid[]) if (node(id).state === 'running') setNode(id, d.ok === false ? 'fail' : 'ok');
        }
        break;
      }
      case 'question': {
        open.push({ kind: 'question', id: d.id, eventId: e.id, ts: e.ts, expiresAt: d.expires_at ?? null, text: d.text, options: d.options ?? [], code: d.code ?? null, params: d.params ?? {} });
        setNode('plan', 'waiting');
        push({ key: 'e' + e.id, type: 'question', state: 'wait', ts: e.ts, id: d.id, code: d.code ?? null, params: d.params ?? {}, text: d.text, options: d.options ?? [], answer: null });
        break;
      }
      case 'answered': {
        const i = open.findIndex((r) => r.kind === 'question' && r.id === d.id); if (i >= 0) open.splice(i, 1);
        questionAnswers.set(d.id, d.value ?? null);
        for (const it of tl) if (it.type === 'question' && it.id === d.id && it.answer === null) { it.answer = d.value ?? ''; it.state = 'ok'; }
        if (!open.some((r) => r.kind === 'question') && node('plan').state === 'waiting') setNode('plan', 'running');
        break;
      }
      case 'plan_ready': {
        const plan = d.plan as PlanView;
        S.plan = plan; S.how = d.how; S.howCode = d.how_code ?? null; S.howParams = d.how_params ?? {}; S.planNotes = plan.notes ?? []; S.readiness = d.readiness?.findings ?? []; S.wouldSend = d.would_send ?? []; S.network = d.network?.name ?? d.network_id ?? null; S.networkNew = !!d.network?.new;
        S.cost = d.cost ?? null; S.usage = d.usage ?? null; S.disks = d.disks ?? [];
        node('plan').apps = plan.apps.length; node('plan').databases = plan.databases.length;
        S.money.plannedHourly = S.cost ? S.cost.total_hourly_incl_vat : null; S.money.plannedMonthly = S.cost ? S.cost.total_monthly_incl_vat : null;
        const line = (name: string) => S.cost?.lines.find((l) => l.name === name);
        for (const a of plan.apps) {
          const l = line(a.name);
          addResource({ id: a.name, kind: 'app', type: a.platform, role: a.role, port: a.port, path: a.path, plan: l?.plan ?? null, tier: l?.tier ?? null, tierLabel: l?.tier_label ?? null, hourly: l?.hourly_incl_vat ?? null, monthly: l?.monthly_incl_vat ?? null, evidence: a.evidence, env: a.env });
        }
        for (const db of plan.databases) {
          const l = line(db.id);
          addResource({ id: db.id, kind: 'database', type: db.kind, version: db.version, plan: l?.plan ?? null, tier: l?.tier ?? null, tierLabel: l?.tier_label ?? null, hourly: l?.hourly_incl_vat ?? null, monthly: l?.monthly_incl_vat ?? null, evidence: db.evidence });
        }
        for (const st of plan.storage ?? []) {
          const l = line(st.id);
          addResource({ id: st.id, kind: 'bucket', type: st.permission, plan: l?.plan ?? null, tier: null, tierLabel: null, hourly: l?.hourly_incl_vat ?? null, monthly: l?.monthly_incl_vat ?? null, evidence: st.evidence });
        }
        if (plan.mail) {
          const l = line(plan.mail.domain);
          addResource({ id: plan.mail.domain, kind: 'mail', type: plan.mail.mode, plan: l?.plan ?? null, tier: null, tierLabel: null, hourly: l?.hourly_incl_vat ?? null, monthly: l?.monthly_incl_vat ?? null, evidence: plan.mail.evidence });
        }
        for (const dk of S.disks) addResource({ id: dk.name, kind: 'disk', mountTo: dk.mount_to, ownerApp: dk.app, hourly: dk.price, monthly: null, priceUnverified: dk.billing === 'unverified' || dk.price === null });
        const warnings = plan.notes.filter((n) => n.level !== 'info').length + S.readiness.filter((f) => f.level === 'warning').length;
        push({ key: 'e' + e.id, type: 'plan', state: 'ok', ts: e.ts, how: d.how, howCode: d.how_code ?? null, howParams: d.how_params ?? {}, apps: plan.apps.length, databases: plan.databases.length, plan });
        if (plan.notes.length || S.readiness.length) push({ key: 'n' + e.id, type: 'notes', state: warnings ? 'warn' : 'ok', ts: e.ts, notes: plan.notes, warnings, readiness: S.readiness });
        // edges: the plan says which resource each app uses and through which variables. Older runs without `links` fall back to reading the
        // $DB_<id>_ references, and to a variable name containing REDIS (marked inferred).
        const edges: Edge[] = [];
        for (const a of plan.apps) if (a.role === 'web' && a.port) edges.push({ from: 'internet', to: a.name, label: 'HTTPS', inferred: false });
        if (Array.isArray(plan.links)) {
          for (const l of plan.links) edges.push({ from: l.from, to: l.to_kind === 'mail' && plan.mail ? plan.mail.domain : l.to, label: l.env.join(', '), inferred: false });
        } else {
          for (const a of plan.apps) for (const ev of a.env) {
            const m = ev.reference?.match(/\$DB_([^_$]+(?:-[^_$]+)*)_/);
            if (m && plan.databases.some((x) => x.id === m[1])) edges.push({ from: a.name, to: m[1], label: ev.name, inferred: false });
            else if (/redis/i.test(ev.name)) for (const db of plan.databases.filter((x) => /redis/i.test(x.kind))) edges.push({ from: a.name, to: db.id, label: ev.name, inferred: true });
          }
        }
        S.edges = edges.filter((x, i, arr) => arr.findIndex((y) => y.from === x.from && y.to === x.to && y.label === x.label) === i);
        break;
      }
      case 'plan_updated': case 'plan_rejected': case 'tool_call': break;
      case 'confirm_request': {
        setNode('confirm', 'waiting');
        open.push({ kind: 'confirm', id: d.id, eventId: e.id, ts: e.ts, expiresAt: d.expires_at ?? null, confirmKind: d.kind, prompt: d.prompt, priceDeltaHourly: d.price_delta_hourly ?? null, code: d.code ?? null, params: d.params ?? {} });
        push({ key: 'e' + e.id, type: 'confirm', state: 'wait', ts: e.ts, id: d.id, kind: d.kind, code: d.code ?? null, params: d.params ?? {}, prompt: d.prompt, priceDelta: d.price_delta_hourly ?? null, accepted: null });
        break;
      }
      case 'confirm_response': {
        const i = open.findIndex((r) => r.kind === 'confirm' && r.id === d.id); if (i >= 0) open.splice(i, 1);
        for (let k = tl.length - 1; k >= 0; k--) { const it = tl[k]; if (it.type === 'confirm' && it.id === d.id && it.accepted === null) { it.accepted = !!d.accepted; it.state = d.accepted ? 'ok' : 'fail'; break; } }
        if (!open.some((r) => r.kind === 'confirm') && node('confirm').state === 'waiting') setNode('confirm', d.accepted ? 'ok' : 'declined');
        break;
      }
      case 'secret_request': {
        open.push({ kind: 'secret', id: d.id, eventId: e.id, ts: e.ts, expiresAt: d.expires_at ?? null, name: d.name });
        push({ key: 'e' + e.id, type: 'secret', state: 'wait', ts: e.ts, id: d.id, name: d.name, provided: false });
        break;
      }
      case 'secret_provided': {
        const i = open.findIndex((r) => r.kind === 'secret' && r.id === d.id); if (i >= 0) open.splice(i, 1);
        for (let k = tl.length - 1; k >= 0; k--) { const it = tl[k]; if (it.type === 'secret' && it.id === d.id && !it.provided) { it.provided = !!d.provided; it.state = 'ok'; break; } }
        break;   // only name and provided are read; whatever else an event might carry is dropped here
      }
      case 'request_expired': {
        if (d.request === 'handoff') { if (S.handoff && S.handoff.state === 'waiting') { S.handoff.state = 'expired'; for (const it of tl) if (it.type === 'handoff' && it.handoff === S.handoff) it.state = 'fail'; } break; }
        const i = open.findIndex((r) => r.id === d.id && (!d.request || r.kind.startsWith(String(d.request).slice(0, 4)) || String(d.request).includes(r.kind)));
        if (i >= 0) open.splice(i, 1);
        push({ key: 'e' + e.id, type: 'expired', state: 'fail', ts: e.ts, id: d.id, request: String(d.request ?? '') });
        break;
      }
      case 'step_started': {
        const step = String(d.step); const r = stepRes(step);
        const [kind] = step.split(':');
        const pid: Pid = kind === 'database' || kind === 'bucket' || kind === 'mail' ? 'provision' : 'deploy';
        if (kind === 'app') { if (node('provision').state === 'running') setNode('provision', 'ok'); }
        setNode(pid, 'running');
        if (r) { r.state = 'creating'; stepOf.set(r.id, step); node(pid).subs = node(pid).subs.filter((s) => s.id !== r.id).concat({ id: r.id, state: 'running' }); }
        push({ key: 'e' + e.id, type: 'resource', state: 'run', ts: e.ts, id: step.split(':')[1] ?? step, kind: kind === 'database' || kind === 'bucket' || kind === 'mail' ? kind : 'app' });
        break;
      }
      case 'resource_created': {
        const r = addResource({ id: d.id, kind: d.kind, type: d.type ?? d.platform ?? null, plan: d.plan, tier: d.tier, tierLabel: d.tier_label, hourly: d.hourly_price, monthly: d.monthly_price ?? Math.round(d.hourly_price * HOURS_PER_MONTH), dry: !!d.dry_run });
        if (r.state === 'proposed') r.state = 'creating';
        break;
      }
      case 'resource_updated': {
        const r = addResource({ id: d.id, kind: d.kind, plan: d.plan, tier: d.tier, tierLabel: d.tier_label });
        r.previous = { plan: d.previous?.plan, tier: d.previous?.tier, hourly: r.hourly };
        r.hourly = d.hourly_price; r.monthly = d.monthly_price ?? Math.round(d.hourly_price * HOURS_PER_MONTH);
        break;
      }
      case 'step_finished': {
        const step = String(d.step); const r = stepRes(step); const [kind] = step.split(':');
        const pid: Pid = kind === 'database' || kind === 'bucket' || kind === 'mail' ? 'provision' : 'deploy';
        const bad = !!d.state && !['READY', 'DRYRUN'].includes(String(d.state).toUpperCase());      // FAILED and UNHEALTHY are failures; database steps carry no state
        if (r) { r.state = bad ? 'failed' : 'live'; if (d.dry_run) r.dry = true; const s = node(pid).subs.find((x) => x.id === r.id); if (s) s.state = bad ? 'fail' : 'ok'; else node(pid).subs.push({ id: r.id, state: bad ? 'fail' : 'ok' }); }
        for (let k = tl.length - 1; k >= 0; k--) { const it = tl[k]; if (it.type === 'resource' && it.id === (step.split(':')[1] ?? step) && it.state === 'run') { it.state = bad ? 'fail' : 'ok'; break; } }
        if (bad) setNode(pid, 'fail');
        break;
      }
      case 'build': break;
      case 'build_error': { setNode('deploy', 'fail'); break; }
      case 'warning': push({ key: 'e' + e.id, type: 'warning', state: 'warn', ts: e.ts, step: String(d.step), code: d.code ?? null, params: d.params ?? {}, message: d.message }); break;
      case 'test_result': {
        const t: TestRow = { step: d.step, name: d.name, ok: !!d.ok, detail: d.detail, url: d.url ?? null, app: String(d.step).split(':')[1] ?? d.step };
        S.tests.push(t); setNode('verify', t.ok ? 'ok' : 'fail');
        const r = stepRes(d.step); if (r && d.url) r.url = d.url;
        push({ key: 'e' + e.id, type: 'test', state: t.ok ? 'ok' : 'fail', ts: e.ts, test: t });
        break;
      }
      case 'heal_attempt': S.healAttempts = Math.max(S.healAttempts, d.attempt); if (typeof d.max_attempts === 'number') S.maxAttempts = d.max_attempts; attempt = d.attempt; break;
      case 'diagnosis': {
        const dg = d as never as RunViewStateDiagnosis; S.diagnoses.push(dg); lastDiagnosisApp = dg.app ?? null;
        const r = dg.app ? res.get(resKey('app', dg.app)) : undefined; if (r) r.state = 'failed';
        setNode('deploy', 'fail');
        push({ key: 'e' + e.id, type: 'diagnosis', state: 'fail', ts: e.ts, diagnosis: dg, attempt });
        break;
      }
      case 'fix_applied': case 'fix_declined': {
        const declined = e.kind === 'fix_declined'; const f = d as never as RunViewStateFix;
        (declined ? S.fixesDeclined : S.fixesApplied).push(f);
        if (!declined) { S.money.fixDeltaHourly += f.cost_delta_hourly ?? 0; if (node('deploy').state === 'fail') setNode('deploy', 'running'); }
        push({ key: 'e' + e.id, type: 'fix', state: declined ? 'fail' : 'ok', ts: e.ts, fix: f, declined, auto: autoNext && !declined });
        autoNext = false;
        break;
      }
      case 'handoff_waiting': {
        const h: HandoffView = { id: d.id, app: d.app, runId: d.run_id, mcpUrl: d.mcp_url, diagnosis: d.diagnosis, expiresAt: d.expires_at ?? null, state: 'waiting', how: null, files: [], calls: [] };
        S.handoff = h; setNode('deploy', 'waiting');
        push({ key: 'e' + e.id, type: 'handoff', state: 'wait', ts: e.ts, handoff: h });
        break;
      }
      case 'external_agent_call': if (S.handoff) S.handoff.calls.push({ tool: String(d.tool), ok: !!d.ok, ts: e.ts, detail: d.detail }); break;
      case 'handoff_received': {
        const h = S.handoff;
        if (h) { h.state = 'received'; h.how = d.how; h.files = d.files ?? []; for (const it of tl) if (it.type === 'handoff' && it.handoff === h) it.state = 'ok'; if (node('deploy').state === 'waiting') setNode('deploy', 'running'); }
        break;
      }
      case 'fix_auto_applied': autoNext = true; break;
      case 'patch_ready': push({ key: 'e' + e.id, type: 'patch', state: 'ok', ts: e.ts, file: d.file, diff: d.diff }); break;
      case 'agent_message': push({ key: 'e' + e.id, type: 'agent', state: 'ok', ts: e.ts, text: d.text }); break;
      case 'run_finished': {
        finished = true;
        const created = d.created ?? { apps: [], databases: [], database_ids: [] };
        S.created = created; S.urls = d.urls ?? {};
        S.result = { ok: !!d.ok, dryRun: !!d.dry_run, stopped: d.stopped ?? null, failedStep: d.failed_step ?? null, reasonCode: d.reason_code ?? null, reasonParams: d.reason_params ?? {}, diagnosis: d.diagnosis ?? null, blockers: d.blockers ?? [], fixes: d.fixes ?? [], created, urls: d.urls ?? {}, wouldSend: d.would_send ?? [], healAttempts: d.heal_attempts ?? 0, followUps: d.follow_ups ?? [] };
        if (d.ok && !d.dry_run) {
          if (node('provision').state === 'running') setNode('provision', 'ok'); if (node('deploy').state !== 'fail') setNode('deploy', 'ok');
          if (node('verify').state === 'pending') setNode('verify', 'ok');
          setNode('live', Object.keys(d.urls ?? {}).length ? 'ok' : 'pending'); node('live').url = Object.values(d.urls ?? {})[0] as string ?? null;
          for (const [app, url] of Object.entries((d.urls ?? {}) as Record<string, string>)) { const r = res.get(resKey('app', app)); if (r) r.url = url; }
        } else if (d.ok && d.dry_run) {
          for (const id of ['provision', 'deploy'] as Pid[]) if (node(id).state === 'running') setNode(id, 'ok');
          setNode('verify', 'skipped'); setNode('live', 'skipped');
        } else {
          for (const id of PIPE_IDS) if (node(id).state === 'running' || node(id).state === 'waiting') setNode(id, d.stopped === 'cancelled' ? 'declined' : 'fail');
          if (d.stopped === 'cancelled') { for (const id of ['provision', 'deploy', 'verify', 'live'] as Pid[]) if (node(id).state === 'pending') setNode(id, 'skipped'); if (node('confirm').state !== 'declined') setNode('confirm', 'declined'); }
          else { for (const id of ['verify', 'live'] as Pid[]) if (node(id).state === 'pending') setNode(id, 'skipped'); }
        }
        const cancelled = d.stopped === 'cancelled';
        push({ key: 'e' + e.id, type: 'finished', state: d.ok ? 'ok' : (cancelled ? 'wait' : 'fail'), ts: e.ts, ok: !!d.ok, cancelled, reasonCode: d.reason_code ?? null, reasonParams: d.reason_params ?? {} });
        break;
      }
      default: break;    // unknown kinds are ignored, so a newer backend never breaks an older page
    }
  }
  void lastDiagnosisApp; void questionAnswers;

  // a project with no database has nothing to provision: that step is skipped, not pending, so it never reads as "now" or counts as unfinished
  if (S.plan && S.plan.databases.length === 0 && node('provision').state === 'pending') setNode('provision', 'skipped');

  S.openRequests = open;
  S.pipeline = PIPE_IDS.map((id) => pipe.get(id)!);
  S.resources = [...res.values()];
  // money: what exists now (created or being created) and what a fix changed
  let hr = 0, mo = 0;
  for (const r of S.resources) if ((r.state === 'creating' || r.state === 'live' || r.state === 'failed') && r.hourly !== null && r.kind !== 'disk') { hr += r.hourly; mo += r.monthly ?? Math.round(r.hourly * HOURS_PER_MONTH); }
  S.money.currentHourly = hr; S.money.currentMonthly = mo;

  // status and the agent's presence, both derived and never stored
  let status: RunStatusView;
  if (finished) status = S.result!.ok ? 'succeeded' : (S.result!.stopped === 'cancelled' ? 'cancelled' : 'failed');
  else if (open.length || S.handoff?.state === 'waiting') status = 'waiting'; else status = 'running';
  S.status = status;
  let agent: AgentState;
  if (finished) agent = S.result!.ok ? 'done' : (S.result!.stopped === 'cancelled' ? 'done' : 'failed');
  else if (open.length || S.handoff?.state === 'waiting') agent = 'waiting';
  else if (S.phase === 'deploy' || S.phase === 'confirm') agent = S.diagnoses.length && S.diagnoses.length > S.fixesApplied.length + S.fixesDeclined.length ? 'thinking' : 'working';
  else if (S.phase === 'plan') agent = 'thinking'; else agent = 'reading';
  S.agent = agent;
  return S;
}
type RunViewStateDiagnosis = import('../api/events').DiagnosisView & { app: string };
type RunViewStateFix = import('../api/events').FixResult;

/** The run as history after a cleanup: what it created is gone, nothing runs and nothing bills, the pipeline and the timeline stay as they were. */
export function applyRemoval(view: RunViewState, removal: Removal): RunViewState {
  return {
    ...view,
    removed: removal,
    resources: view.resources.map((r) => (r.state === 'proposed' ? r : { ...r, state: 'removed' as const })),
    money: { ...view.money, currentHourly: 0, currentMonthly: 0 },
    urls: {},
    created: null,
  };
}
