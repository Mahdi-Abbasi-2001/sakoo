import { rovingKeys } from '../lib/roving';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type * as G from '../api/schema.gen';
import type { ProjectDetail } from '../api/types';
import { Icon } from '../components/Icon';
import { Ticker } from '../components/Ticker';
import { DomainSection } from './DomainSection';
import { ReleasesSection } from './ReleasesSection';
import { MetricsSection } from './MetricsSection';
import { DiagnosisSection } from './DiagnosisSection';
import { LimitsSection } from './LimitsSection';
import { UptimeSection } from './UptimeSection';
import { BranchSection } from './BranchSection';
import { PrSection } from './PrSection';
import { WordpressSection } from './WordpressSection';
import { useI18n, usePageTitle } from '../i18n';
import { errorText } from '../i18n/errors';
import { daysLeft, when } from '../lib/time';
import { describeRun } from '../projects/describe';
import { money, num, unit } from '../run/format';
import { reduceRun } from '../run/reduceRun';

export function ProjectPage() {
  const { id = '' } = useParams();
  const pid = Number(id);
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [p, setP] = useState<ProjectDetail | null>(null);
  usePageTitle('title.project', { name: p?.name ?? '' });
  const [state, setState] = useState<'loading' | 'ok' | 'missing' | 'error'>('loading');
  const [env, setEnv] = useState<{ name: string; kind: 'hidden' | 'secret' | 'set'; note: string }[] | null>(null);
  const [starting, setStarting] = useState<null | 'deploy' | 'preview' | 'release'>(null);
  const [search, setSearch] = useSearchParams();
  const [notice, setNotice] = useState<string | null>(null);
  const [pushes, setPushes] = useState<G.DeployRowOut[]>([]);   // releases of newer code (the button, the chat): they have no run page
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    try { setP(await api.project(pid)); setState('ok'); } catch (e) { setState(e instanceof ApiError && e.status === 404 ? 'missing' : 'error'); }
  }, [pid]);
  useEffect(() => { void load(); }, [load]);
  const isGitProject = p?.source_kind === 'git';
  useEffect(() => {      // releases of newer code, refreshed every few seconds while one is running
    if (!isGitProject) return; let dead = false; let timer: ReturnType<typeof setTimeout>;
    const tick = async () => { try { const r = await api.deploys(pid); if (dead) return; setPushes(r.deploys); timer = setTimeout(() => void tick(), r.deploys.some((d) => d.state === 'running') ? 4000 : 20000); } catch { if (!dead) timer = setTimeout(() => void tick(), 30000); } };
    void tick(); return () => { dead = true; clearTimeout(timer); };
  }, [pid, isGitProject]);
  useEffect(() => {      // names of the variables, from the plan of the live run. Values are never in any event.
    setEnv(null); const rid = p?.live?.run_id; if (!rid) return; let dead = false;
    api.runEvents(rid).then((r) => {
      const v = reduceRun(r.events); const out = new Map<string, { name: string; kind: 'hidden' | 'secret' | 'set'; note: string }>();
      for (const a of v.plan?.apps ?? []) for (const e of a.env) out.set(e.name, { name: e.name, kind: e.reference ? 'hidden' : 'set', note: e.reference ? t('proj.env.from', { db: (e.reference.match(/\$DB_(.+?)_/)?.[1]) ?? '' }) : '' });
      for (const e of v.plan?.env ?? []) out.set(e.name, { name: e.name, kind: e.secret ? 'secret' : (out.get(e.name)?.kind ?? 'set'), note: e.source === 'user' ? t('proj.env.you') : e.source === 'database' ? (out.get(e.name)?.note ?? '') : '' });
      if (!dead) setEnv([...out.values()]);
    }).catch(() => { if (!dead) setEnv([]); });
    return () => { dead = true; };
  }, [p?.live?.run_id, lang]);   // eslint-disable-line react-hooks/exhaustive-deps

  const start = async (mode: 'deploy' | 'preview') => {
    setStarting(mode); setErr(null);
    try { const r = await api.startRun(pid, { mode }); nav(`/runs/${r.id}`); } catch (e) { setErr(errorText(lang, e)); setStarting(null); }
  };

  if (state === 'loading') return <ProjectSkeleton />;
  if (state === 'missing' || !p) return <div className="page-narrow"><div className="card pad stack"><Icon name="alert" /><p>{t('proj.missing')}</p><div><Link className="btn" to="/dashboard">{t('nav.projects')}</Link></div></div></div>;

  const live = p.live;
  const url = live ? (Object.values(live.urls)[0] as string | undefined) : undefined;
  const left = daysLeft(live?.free_address_expires_at);
  const runs = p.runs ?? [];
  const head = p.last_run ? describeRun(lang, p.last_run) : null;
  const apps = live?.resources.filter((r) => r.kind === 'app').length ?? 0, dbs = live?.resources.filter((r) => r.kind === 'database').length ?? 0;

  const tab = (['overview', 'deploys', 'settings'] as const).find((x) => x === search.get('tab')) ?? 'overview';
  const setTab = (x: string) => setSearch(x === 'overview' ? {} : { tab: x }, { replace: true });
  const isGit = p.source_kind === 'git';
  // runs and releases of newer code in one list, newest first (a release with a run page opens it (its build log); an older one shows its result in the row)
  const history = [...runs.map((r, i) => ({ kind: 'run' as const, ts: r.created_at, r, n: runs.length - i })), ...pushes.map((d) => ({ kind: 'deploy' as const, ts: d.ts, d }))].sort((a, b) => b.ts - a.ts);
  const github = isGit && (p.git_url ?? '').includes('github.com');
  const release = async () => {
    setStarting('release'); setErr(null);
    try { const out = await api.redeploy(pid); if (out.run_id) { nav(`/runs/${out.run_id}`); return; } setSearch({ tab: 'deploys' }, { replace: true }); setNotice(t('proj.release.started')); }
    catch (e) { setErr(errorText(lang, e)); } finally { setStarting(null); }
  };

  return (
    <div className="projpage">
      <div className="crumb"><Link to="/dashboard">{t('proj.crumb.projects')}</Link> / <span className="mono">{p.name}</span></div>
      <div className="proj-h">
        <h1 className="title mono">{p.name}</h1>
        {live ? <span className="badge s-ok"><Icon name="check" />{t('status.succeeded')}</span> : head && <span className={'badge ' + head.tone}><Icon name={head.icon} />{head.label}</span>}
        {live && <span className="cost"><i>{t('proj.running_now')}</i> <b>≈ <Ticker value={live.hourly_incl_vat} format={(n) => money(lang, n)} /> {t('ar.hr')}</b> <i>{t('ap.incl_vat')}</i></span>}
        <span style={{ flex: 1 }} />
        {live && isGit && <button type="button" className="btn btn-p" disabled={!!starting} onClick={() => void release()}><Icon name="arrow" />{starting === 'release' ? t('proj.starting') : t('proj.release')}</button>}
        {!live && <>
          <button type="button" className="link" disabled={!!starting} onClick={() => void start('preview')}>{starting === 'preview' ? t('proj.starting') : t('proj.only_requests')}</button>
          <button type="button" className="btn btn-p" disabled={!!starting} onClick={() => void start('deploy')}><Icon name="arrow" />{starting === 'deploy' ? t('proj.starting') : t('proj.newrun')}</button>
        </>}
      </div>
      {notice && <p className="hint" role="status" style={{ marginTop: 10 }}><Icon name="check" /> {notice}</p>}
      {err && <div className="err" role="alert" style={{ marginTop: 10 }}><Icon name="alert" />{err}</div>}
      <div className="hint meta-line">
        <span>{isGit ? t('proj.meta.git', { url: (p.git_url ?? '').replace(/^https?:\/\//, '') }) : t('proj.meta.zip')}</span><span>·</span><span>{t('proj.meta.added', { when: when(lang, p.created_at) })}</span>
        {live && <><span>·</span><span>{t('proj.meta.res', { apps: unit(t, lang, apps, 'app'), databases: unit(t, lang, dbs, 'database') })}</span></>}
      </div>

      <div className="ptabs seg" role="tablist" aria-label={p.name} onKeyDown={rovingKeys('tab')} style={{ marginTop: 20 }}>
        {(['overview', 'deploys', 'settings'] as const).map((x) => <button key={x} type="button" role="tab" tabIndex={tab === x ? 0 : -1} aria-selected={tab === x} onClick={() => setTab(x)}>{t('proj.tab.' + x)}</button>)}
      </div>

      {tab === 'overview' && (
        <div className="ptab-grid" role="tabpanel">
        {live && (
          <section className="card pad stack result live" aria-label={t('proj.live.t')}>
            <div className="row" style={{ gap: 12, flexWrap: 'nowrap', alignItems: 'flex-start' }}><Icon name="check" className="ic big ok" />
              <div className="stack" style={{ gap: 8, flex: 1 }}><h2 className="card-t">{t('proj.live.t')}</h2>
                {url && <div className="url-row"><span className="chip"><span className="ltr">{url}</span></span>
                  <button type="button" className="btn btn-s" onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}>{copied ? t('log.copied') : t('live.copy')}</button>
                  <a className="btn btn-s btn-p" href={url} target="_blank" rel="noreferrer noopener">{t('live.open')}</a>
                  <Link className="btn btn-s" to={`/runs/${live.run_id}`}>{t('proj.live.run')}</Link></div>}
                {left !== null && <p className="hint">{t('proj.live.free', { n: num(lang, left) })}</p>}
              </div></div>
          </section>
        )}
          <section className="card pad stack" style={{ gap: 10 }}>
            <h2 className="sect">{t('proj.aside.now')}</h2>
            {live ? <>
              {live.resources.map((r) => <div className="rw" key={r.id} title={r.monthly_price != null ? t('proj.res.month', { monthly: money(lang, r.monthly_price) }) : undefined}><span className="code">{r.id}</span><span className="hint">{t('proj.kind.' + r.kind)} · {r.tier_label}</span><span className="cost">{money(lang, r.hourly_price)} <i>{t('ar.hr')}</i></span></div>)}
              <div className="rw total"><span>{t('proj.aside.total')}</span><span className="num">≈ {money(lang, live.hourly_incl_vat)} {t('ar.hr')}</span></div>
              <p className="hint">{t('proj.aside.month', { monthly: money(lang, live.monthly_incl_vat) })}</p>
              <div><a className="btn btn-s" href="https://console.liara.ir" target="_blank" rel="noreferrer noopener">{t('proj.aside.console')}</a></div>
            </> : <p className="hint">{t('proj.aside.none')}</p>}
          </section>
          {live && <DiagnosisSection pid={pid} />}
          {live && <UptimeSection pid={pid} />}
          {live && <MetricsSection pid={pid} />}
          {p.wordpress && <WordpressSection pid={pid} />}
        </div>
      )}

      {tab === 'deploys' && (
        <div className="stack" style={{ gap: 20, marginTop: 20 }} role="tabpanel">
          {github && <PrSection pid={pid} private={!!p.private} />}
          <section className="card" aria-label={t('proj.hist.t')}>
            <div className="card-h"><div><h2 className="card-t">{t('proj.hist.t')}</h2><p className="hint">{t('proj.hist.d')}</p></div></div>
            <div className="rtable">
              <div className="rrow rhead" aria-hidden="true"><span>{t('proj.th.run')}</span><span>{t('proj.th.status')}</span><span>{t('proj.th.started')}</span><span>{t('proj.th.result')}</span><span /></div>
              <ol>{history.map((h) => h.kind === 'deploy' ? (
                <li className={'rrow' + (h.d.run_id ? ' clickable' : '')} key={'d' + h.d.id} onClick={(e) => { if (h.d.run_id && !(e.target as HTMLElement).closest('a,button')) nav(`/runs/${h.d.run_id}`); }}>
                  <span className="mono rn" aria-hidden="true">↻</span>
                  <div><span className={'badge ' + (h.d.state === 'succeeded' ? 's-ok' : h.d.state === 'failed' ? 's-fail' : 's-wait')}><Icon name={h.d.state === 'succeeded' ? 'check' : h.d.state === 'failed' ? 'x' : 'dots'} />{t('ad.state.' + h.d.state)}</span><div className="hint">{t('proj.hist.release')}</div></div>
                  <span className="hint">{when(lang, h.d.ts)}</span>
                  <div className="rtext">{deployMessage(t, h.d.message ?? '')}{h.d.error && <> <span className="mono hint">{h.d.error}</span></>}</div>
                  <div />
                </li>
              ) : (() => { const r = h.r; const line = describeRun(lang, r, { current: live?.run_id === r.id }); return (
                <li className="rrow clickable" key={r.id} onClick={(e) => { if (!(e.target as HTMLElement).closest('a,button')) nav(`/runs/${r.id}`); }}>
                  <span className="mono rn">#{num(lang, h.n)}</span>
                  <div><span className={'badge ' + line.tone}><Icon name={line.icon} />{line.label}</span>{line.sub && <div className="hint">{line.sub}</div>}</div>
                  <span className="hint">{when(lang, r.created_at)}</span>
                  <div className="rtext">{line.text}{line.code && <> <span className="mono hint">{line.code}</span></>}</div>
                  <div><Link className="btn btn-s" to={`/runs/${r.id}`}>{t('proj.open')}</Link></div>
                </li>); })())}</ol>
            </div>
          </section>
          {live && <ReleasesSection pid={pid} />}
          {isGit && <BranchSection pid={pid} />}
        </div>
      )}

      {tab === 'settings' && (
        <div className="ptab-grid" role="tabpanel">
          {live && <DomainSection pid={pid} />}
          {live && <LimitsSection pid={pid} />}
          {live && (
            <section className="card pad stack" style={{ gap: 10 }}>
              <h2 className="sect">{t('proj.env.t')}</h2><p className="hint">{t('proj.env.d')}</p>
              {env === null ? <div className="sk" style={{ height: 72 }} /> : env.length === 0 ? <p className="hint">{t('proj.env.none')}</p> :
                env.map((e) => <div className="rw" key={e.name}><span className="code">{e.name}</span><span className={'badge ' + (e.kind === 'secret' ? 's-wait' : '')}><Icon name={e.kind === 'secret' ? 'shield' : 'check'} />{t('proj.env.' + e.kind)}</span><span className="hint">{e.note}</span></div>)}
            </section>
          )}
          <Danger p={p} onChanged={load} />
        </div>
      )}
    </div>
  );
}

function Danger({ p }: { p: ProjectDetail; onChanged: () => void }) {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [typed, setTyped] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const live = p.live;
  const runsN = p.runs?.length ?? 0;
  const names = useMemo(() => live?.resources.map((r) => r.id).join(', ') ?? '', [live]);
  const remove = async () => {
    setBusy(true); setErr(null);
    try { await api.deleteProject(p.id, typed); nav('/dashboard', { replace: true }); }
    catch (e) { setErr(errorText(lang, e)); setBusy(false); }
  };
  return (
    <section className="card pad stack danger" style={{ gap: 12 }}>
      <h2 className="sect">{t('proj.danger.t')}</h2>
      <b>{t('proj.delete.t')}</b>
      <p className="hint">{t('proj.delete.d', { runs: num(lang, runsN) })}</p>
      {live && <p className="hint">{t('proj.delete.live', { names, hourly: money(lang, live.hourly_incl_vat) })}</p>}
      <p className="hint"><b>{t('proj.delete.warn')}</b></p>
      <div className="field"><label className="lbl" htmlFor="c1">{t('proj.del.type')}</label>
        <div className="row" style={{ flexWrap: 'nowrap' }}><input id="c1" className="inp mono" placeholder={t('proj.del.ph', { name: p.name })} value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
          <button type="button" className="btn btn-d" disabled={busy || typed !== p.name} onClick={() => void remove()}>{busy ? t('proj.delete.working') : t('proj.delete.btn')}</button></div></div>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}

function ProjectSkeleton() {
  const { t } = useI18n();
  return (
    <div className="projpage" role="status" aria-busy="true" aria-label={t('proj.loading')}>
      <div className="sk" style={{ height: 18, width: 220 }} /><div className="sk" style={{ height: 52, width: 340, maxWidth: '100%', marginTop: 14 }} /><div className="sk" style={{ height: 16, width: 420, maxWidth: '100%', marginTop: 14 }} />
      <div className="proj-grid" style={{ marginTop: 24 }}><div className="stack"><div className="card pad"><div className="sk" style={{ height: 90 }} /></div><div className="card pad"><div className="sk" style={{ height: 300 }} /></div></div><div className="stack"><div className="card pad"><div className="sk" style={{ height: 160 }} /></div><div className="card pad"><div className="sk" style={{ height: 120 }} /></div></div></div>
    </div>
  );
}

/** The server writes the message of a release in English; the page words the ones it made itself. A push's message is the commit's own text. */
function deployMessage(t: (k: string, p?: Record<string, string | number>) => string, m: string): string {
  if (m === 'released from the page') return t('ad.msg.page');
  if (m === 'requested in the chat') return t('ad.msg.chat');
  if (m === 'edits made in the chat') return t('ad.msg.edits');
  const rb = /^rollback to (\S+)/.exec(m); if (rb) return t('ad.msg.rollback', { id: rb[1] });
  return m;
}
