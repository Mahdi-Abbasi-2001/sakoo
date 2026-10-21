import { useAssistantOnline } from '../../lib/assistant';
import { useFollow } from '../../lib/useFollow';
import { rovingKeys } from '../../lib/roving';
import { useMemo, useRef, useState } from 'react';
import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { money, money2, num, rel } from '../format';
import { api } from '../../api/client';
import { errorText } from '../../i18n/errors';
import { sendText } from '../../i18n/messages';
import { useSay } from '../wording';
import type { RunViewState } from '../types';

type Tab = 'logs' | 'tests' | 'plan' | 'files' | 'costs';

export function Details({ runId, view, filter, tab, onTab }: { runId: string | null; view: RunViewState; filter: string | null; tab: Tab; onTab: (t: Tab) => void }) {
  const { t, lang } = useI18n();
  const files = view.timeline.filter((i) => i.type === 'patch').length;
  const tabs: [Tab, number][] = [['logs', view.logs.length], ['tests', view.tests.length], ['plan', (view.plan?.apps.length ?? 0) + (view.plan?.databases.length ?? 0)], ['files', files], ['costs', view.cost?.lines.length ?? 0]];
  return (
    <section className="card details-card" aria-label={t('tab.list')} id="details">
      <div className="tabs" role="tablist" onKeyDown={rovingKeys('tab')}>
        {tabs.map(([id, c]) => <button key={id} type="button" role="tab" id={'tab-' + id} tabIndex={tab === id ? 0 : -1} aria-selected={tab === id} aria-controls={'pnl-' + id} className={'tb' + (tab === id ? ' on' : '')} onClick={() => onTab(id)}>{t('tab.' + id)}<span className="ct">{num(lang, c)}</span></button>)}
      </div>
      <div className="tabpanel" role="tabpanel" id={'pnl-' + tab} aria-labelledby={'tab-' + tab}>
        {tab === 'logs' && <Logs runId={runId} view={view} filter={filter} />}
        {tab === 'tests' && <Tests view={view} />}
        {tab === 'plan' && <PlanTab view={view} />}
        {tab === 'files' && <Files view={view} />}
        {tab === 'costs' && <Costs view={view} />}
      </div>
    </section>
  );
}

function Logs({ runId, view, filter }: { runId: string | null; view: RunViewState; filter: string | null }) {
  const { t, lang } = useI18n();
  const modelOnline = useAssistantOnline();
  const [why, setWhy] = useState<string | null>(null); const [whyErr, setWhyErr] = useState<string | null>(null); const [asking, setAsking] = useState(false);
  const explain = async () => { if (!runId) return; setAsking(true); setWhyErr(null); try { setWhy((await api.explain(runId)).explanation); } catch (e) { setWhy(null); setWhyErr(errorText(lang, e)); } finally { setAsking(false); } };
  const [phase, setPhase] = useState<string>('all');
  const [q, setQ] = useState('');
  const [paused, setPaused] = useState(false);
  const [copied, setCopied] = useState(false);
  const frozen = useRef(view.logs);
  if (!paused) frozen.current = view.logs;
  const phases = ['all', 'analyze', 'plan', 'confirm', 'deploy'];
  const active = filter ?? (phase === 'all' ? null : phase);
  const lines = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return frozen.current.filter((l) => (!active || l.phase === active || (active === 'provision' || active === 'verify' || active === 'live' ? l.phase === 'deploy' : false)) && (!ql || (l.kind + ' ' + l.text).toLowerCase().includes(ql))).slice(-1000);
  }, [frozen.current, active, q]);
  const box = useRef<HTMLDivElement>(null);
  const { onScroll: onLogScroll } = useFollow(box, !paused);
  const copy = async () => { try { await navigator.clipboard.writeText(lines.map((l) => `${l.n}\t${rel(l.rel)}\t${l.kind}\t${l.text}`).join('\n')); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard can be blocked */ } };
  return (
    <div className="stack" style={{ gap: 12 }}>
      <div className="row log-tools">
        {phases.map((p) => <button key={p} type="button" className={'fchip' + (phase === p && !filter ? ' on' : '')} aria-pressed={phase === p && !filter} onClick={() => setPhase(p)}>{p === 'all' ? t('log.all') : t('phase.' + p)}</button>)}
        <span style={{ flex: 1 }} />
        <input className="inp log-search" aria-label={t('log.search')} placeholder={t('log.search')} value={q} onChange={(e) => setQ(e.target.value)} />
        <button type="button" className="btn" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? t('log.resume') : t('log.pause')}</button>
        <button type="button" className="btn" onClick={() => void copy()}>{copied ? t('log.copied') : t('log.copy')}</button>
        {runId && modelOnline !== false && <button type="button" className="btn btn-p" disabled={asking} onClick={() => void explain()}>{asking ? t('explain.asking') : t('explain.btn')}</button>}
      </div>
      {whyErr && <div className="err" role="alert"><Icon name="alert" />{whyErr}</div>}
      {why && <div className="card pad stack" style={{ gap: 6 }} role="status"><b>{t('explain.t')}</b><p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{why}</p><p className="hint" style={{ margin: 0 }}>{t('explain.note')}</p></div>}
      <div className="term logs" ref={box} tabIndex={0} role="log" aria-label={t('tab.logs')} onScroll={onLogScroll}>
        {lines.length === 0 && <div className="hint" style={{ padding: 16 }}>{t('log.empty')}</div>}
        {lines.map((l) => <div key={l.id} className={'ll ' + l.tone}><span className="n">{l.n}</span><span className="t">{rel(l.rel)}</span><span className="k">{l.kind}</span><span className="m">{l.text}</span></div>)}
      </div>
    </div>
  );
}

function Tests({ view }: { view: RunViewState }) {
  const { t } = useI18n();
  const planned = (view.plan?.apps ?? []);
  if (!view.tests.length && !planned.length) return <div className="empty"><h3>{t('test.none')}</h3><p className="hint">{t('test.none_sub')}</p></div>;
  const done = new Set(view.tests.map((x) => x.app));
  return (
    <div className="stack">
      {view.tests.length === 0 && <div className="empty"><h3>{t('test.none')}</h3><p className="hint">{t('test.none_sub')}</p></div>}
      <p className="sect">{view.tests.length ? t('tab.tests') : t('test.after')}</p>
      <ul className="stack" style={{ gap: 8 }}>
        {view.tests.map((x, i) => <li key={i} className="kv"><span className={'badge ' + (x.ok ? 's-ok' : 's-fail')}><Icon name={x.ok ? 'check' : 'x'} />{x.ok ? t('test.passed') : t('test.failed')}</span><span className="code">{x.name}</span><span>{x.url ?? x.app}</span><span className="hint code">{x.detail}</span></li>)}
        {planned.filter((a) => !done.has(a.name)).map((a) => a.port
          ? <li key={a.name} className="kv"><span className="badge s-pend"><Icon name="dashed" />{t('test.waiting')}</span><span className="code">http_200</span><span>{t('test.http_200', { app: a.name })}</span></li>
          : <li key={a.name} className="kv"><span className="badge s-pend"><Icon name="dots" />{t('test.skipped')}</span><span className="code">{a.name}</span><span>{t('test.no_port', { app: a.name })}</span></li>)}
      </ul>
    </div>
  );
}

function PlanTab({ view }: { view: RunViewState }) {
  const { t, lang } = useI18n(); const say = useSay();
  const p = view.plan;
  if (!p) return <div className="empty"><p className="hint">{t('plan.none')}</p></div>;
  return (
    <div className="plan-grid">
      <div><p className="sect">{t('plan.apps')}</p>{p.apps.map((a) => <div className="drow" key={a.name}><p><b className="mono">{a.name}</b> <span className="code">{a.platform}</span> <span className="code">{a.path}</span> {a.port ? t('plan.port', { port: a.port }) : t('plan.no_port')} · {t('plan.role', { role: a.role })}</p>
        <ul className="env">{a.env.map((e) => <li key={e.name}><span className="code">{e.name}</span> <span className="hint">{e.reference ? refLabel(t, e.reference) : e.has_value ? t('plan.value_set') : ''}</span></li>)}</ul></div>)}
        <p className="sect">{t('plan.databases')}</p>{p.databases.map((d) => <div className="drow" key={d.id}><p><b className="mono">{d.id}</b> <span className="code">{d.kind}</span> {d.version && <span className="code">{d.version}</span>}</p></div>)}
        {(p.storage ?? []).length > 0 && <><p className="sect">{t('plan.storage')}</p>{(p.storage ?? []).map((b) => <div className="drow" key={b.id}><p><b className="mono">{b.id}</b> <span className="code">{b.permission}</span></p></div>)}</>}
        {p.mail && <><p className="sect">{t('plan.mail')}</p><div className="drow"><p><b className="mono">{p.mail.domain}</b> <span className="code">{p.mail.mode}</span></p></div></>}
        {view.disks.length > 0 && <><p className="sect">{t('plan.disks')}</p>{view.disks.map((d) => <div className="drow" key={d.name}><p><b className="mono">{d.name}</b> <span className="code">{d.mount_to}</span> <span className="hint">{t('ap.included')}</span></p></div>)}</>}
      </div>
      <div>
        <p className="sect">{t('plan.readiness')}</p>
        {view.readiness.length === 0 && <p className="hint">{t('plan.none')}</p>}
        {view.readiness.map((f, i) => <div className="drow" key={i}><p><span className={'badge ' + (f.level === 'warning' ? 's-warn' : 's-run')}><Icon name={f.level === 'warning' ? 'warn' : 'alert'} />{t('plan.level.' + f.level)}</span> {say('readiness', f.code, f.params, f.code)}</p><span className="code">{f.file}{f.line ? `:${f.line}` : ''}</span></div>)}
        <p className="sect">{t('plan.would_send')}</p>
        <ul className="term sends">{view.wouldSend.map((w, i) => <li key={i}><b>{w.method}</b> {w.path}<span className="hint"> {sendText(lang, w)}</span></li>)}</ul>
      </div>
    </div>
  );
}

function Files({ view }: { view: RunViewState }) {
  const { t } = useI18n();
  const patches = view.timeline.filter((i) => i.type === 'patch');
  if (!patches.length) return <div className="empty"><h3>{t('files.none')}</h3><p className="hint">{t('files.none_sub')}</p></div>;
  return <div className="stack">{patches.map((p) => p.type === 'patch' && <div key={p.key}><p className="sect code">{p.file}</p><pre className="term diff">{p.diff}</pre></div>)}</div>;
}

function Costs({ view }: { view: RunViewState }) {
  const { t, lang } = useI18n();
  const lines = view.resources.filter((r) => r.kind !== 'disk');
  if (!lines.length) return <div className="empty"><p className="hint">{t('plan.none')}</p></div>;
  const totalH = lines.reduce((n, r) => n + (r.hourly ?? 0), 0), totalM = lines.reduce((n, r) => n + (r.monthly ?? 0), 0);
  return (
    <div className="stack">
      <table className="led">
        <thead><tr><th>{t('cost.resource')}</th><th>{t('cost.plan')}</th><th className="n">{t('cost.hour')}</th><th className="n">{t('cost.month')}</th></tr></thead>
        <tbody>{lines.map((r) => <tr key={r.id}><td className="mono">{r.id}</td><td>{r.type} · {r.plan} {r.tierLabel}{r.previous ? <span className="hint"> ({t('res.price_up', { old: money2(lang, r.previous.hourly ?? 0) })})</span> : null}</td><td className="n">{r.hourly !== null ? money2(lang, r.hourly) : '—'}</td><td className="n">{r.monthly !== null ? money(lang, r.monthly) : '—'}</td></tr>)}
          {view.resources.filter((r) => r.kind === 'disk').map((r) => <tr key={r.id}><td className="mono">{r.id}</td><td>{t('res.disk')} · {r.mountTo}</td><td className="n hint" colSpan={2}>{t('cost.disk')}</td></tr>)}</tbody>
        <tfoot><tr><td>{t('cost.total')}</td><td className="hint">{t('cost.vat')}</td><td className="n">{money(lang, totalH)}</td><td className="n">{money(lang, totalM)}</td></tr></tfoot>
      </table>
      <p className="hint">{t('cost.derived')}{view.money.fixDeltaHourly ? ' ' + t('cost.fix_delta', { delta: money2(lang, view.money.fixDeltaHourly) }) : ''}</p>
    </div>
  );
}

/** Where an environment variable's value comes from, said from its placeholder: a database, a bucket or an email server. */
export function refLabel(t: (k: string, p?: Record<string, string | number>) => string, ref: string): string {
  const m = ref.match(/\$(DB|S3|MAIL)_(.+?)_(?:[A-Z]+)/);
  if (!m) return '';
  return t(m[1] === 'DB' ? 'plan.from_db' : m[1] === 'S3' ? 'plan.from_bucket' : 'plan.from_mail', { db: m[2], id: m[2] });
}
