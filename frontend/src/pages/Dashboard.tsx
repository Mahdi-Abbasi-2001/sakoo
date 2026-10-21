import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, serverNow } from '../api/client';
import type { OneClickAppOut } from '../api/schema.gen';
import { errorText } from '../i18n/errors';
import type { ProjectRow } from '../api/types';
import { Icon } from '../components/Icon';
import { AgentPrompt } from '../components/AgentPrompt';
import { Ticker } from '../components/Ticker';
import { useAuth } from '../auth/AuthContext';
import { useI18n, usePageTitle } from '../i18n';
import { clock, money, num } from '../run/format';
import { describeRun } from '../projects/describe';
import { useProjects } from '../projects/useProjects';
import { daysLeft, when } from '../lib/time';

function useWaitingExpiry(projects: ProjectRow[] | null) {
  const w = projects?.find((p) => p.last_run?.status === 'waiting');
  const [exp, setExp] = useState<number | null>(null);
  const [, tick] = useState(0);
  useEffect(() => { setExp(null); if (!w?.last_run) return; let dead = false; api.run(w.last_run.id).then((d) => { const e = (d.open_requests?.[0]?.data as { expires_at?: number | null } | undefined)?.expires_at; if (!dead) setExp(e ?? null); }).catch(() => undefined); return () => { dead = true; }; }, [w?.last_run?.id]);
  useEffect(() => { if (exp === null) return; const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, [exp]);
  return { project: w, left: exp === null ? null : Math.max(0, exp - serverNow() / 1000) };
}

export function Dashboard() {
  const { t, lang } = useI18n();
  usePageTitle('dash.title');
  const { me } = useAuth();
  const { projects, error, reload } = useProjects();
  const waiting = useWaitingExpiry(projects);

  if (error && !projects) return <div className="page-narrow" style={{ margin: '0 auto' }}><div className="card pad stack"><Icon name="alert" /><p>{t('dash.error')}</p><div><button type="button" className="btn" onClick={() => void reload()}>{t('dash.retry')}</button></div></div></div>;
  if (!projects) return <DashboardSkeleton />;
  if (projects.length === 0) return <EmptyDashboard connected={!!me?.has_liara_token} />;

  const waitingRuns = projects.filter((p) => p.last_run?.status === 'waiting');
  const live = projects.filter((p) => p.live);
  const soonest = live.filter((p) => p.live?.free_address_expires_at).sort((a, b) => (a.live!.free_address_expires_at! - b.live!.free_address_expires_at!))[0];
  const perHour = live.reduce((n, p) => n + (p.live?.hourly_incl_vat ?? 0), 0);
  const unitW = (n: number, k: 'run' | 'project') => `${num(lang, n)} ${t(`dash.${k}.${n === 1 ? 'one' : 'other'}`)}`;

  return (
    <div className="dash">
      <div className="page-h">
        <div><h1 className="title">{t('dash.title')}</h1><p className="lead">{t('dash.lead')}</p></div>
        <Link to="/new" className="btn"><Icon name="file" />{t('dash.new.manual')}</Link>
      </div>
      <AgentPrompt connected={!!me?.has_liara_token} />
      <div className="sumcards" style={{ marginTop: 20 }}>
        <div className="card pad stack lift" style={{ gap: 8 }}>
          <span className={'badge ' + (waitingRuns.length ? 's-wait' : 's-pend')}><Icon name={waitingRuns.length ? 'diamond' : 'dashed'} />{t('status.waiting')}</span>
          <div className="big">{t('dash.card.approve', { n: num(lang, waitingRuns.length) })}</div>
          <p className="hint">{waitingRuns.length === 0 ? t('dash.card.wait.none') : waitingRuns.length === 1 && waiting.project ? t('dash.card.wait.one', { name: waiting.project.name, t: waiting.left === null ? '…' : clock(lang, waiting.left) }) : t('dash.card.wait.many')}</p>
        </div>
        <div className="card pad stack lift" style={{ gap: 8 }}>
          <span className={'badge ' + (live.length ? 's-ok' : 's-pend')}><Icon name={live.length ? 'check' : 'dashed'} />{t('status.succeeded')}</span>
          <div className="big">{unitW(live.length, 'project')}</div>
          <p className="hint">{live.length === 0 ? t('dash.card.live.none') : soonest ? t('dash.card.live.one', { name: soonest.name, n: num(lang, daysLeft(soonest.live!.free_address_expires_at) ?? 0) }) : t('dash.card.live.noexp')}</p>
        </div>
        <div className="card pad stack lift" style={{ gap: 8 }}>
          <span className="badge">{t('dash.card.cost.badge')}</span>
          <div className="big mono-num">≈ <Ticker value={perHour} format={(n) => money(lang, n)} /> <span className="hint">{t('dash.card.cost.unit')}</span></div>
          <p className="hint">{t('dash.card.cost.note')}</p>
        </div>
      </div>

      <section className="card plist" aria-label={t('dash.title')}>
        <div className="prow phead" aria-hidden="true"><span>{t('dash.th.project')}</span><span>{t('dash.th.last')}</span><span>{t('dash.th.live')}</span><span>{t('dash.th.cost')}</span><span /></div>
        <ul>{projects.map((p) => <ProjectItem key={p.id} p={p} />)}</ul>
      </section>
    </div>
  );
}

function ProjectItem({ p }: { p: ProjectRow }) {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [copied, setCopied] = useState(false);
  const lr = p.last_run;
  const line = lr ? describeRun(lang, lr, { current: false }) : null;
  const url = p.live ? (Object.values(p.live.urls)[0] as string | undefined) : undefined;
  const left = daysLeft(p.live?.free_address_expires_at);
  const nres = p.live?.resources.length ?? 0;
  const act = lr?.status === 'waiting' ? { to: `/runs/${lr.id}`, label: t('dash.act.review'), primary: true }
    : lr?.status === 'running' ? { to: `/runs/${lr.id}`, label: t('dash.act.watch'), primary: true }
    : lr?.status === 'failed' || lr?.status === 'interrupted' ? { to: `/runs/${lr.id}`, label: t('dash.act.why'), primary: false } : null;
  return (
    <li className="prow">
      <div className="pcell">
        <Link className="nm mono" to={`/projects/${p.id}`}>{p.name}</Link>{p.down && <> <span className="badge s-fail"><Icon name="x" />{t('up.down')}</span></>}
        <div className="hint">{p.source_kind === 'git' ? t('dash.src.git', { url: (p.git_url ?? '').replace(/^https?:\/\//, '') }) : t('dash.src.zip')}{nres ? ' · ' + t(`dash.res.${nres === 1 ? 'one' : 'other'}`, { n: num(lang, nres) }) : ''}</div>
      </div>
      <div className="pcell">
        {line ? <><span className={'badge ' + (p.live ? 's-ok' : line.tone)}><Icon name={p.live ? 'check' : line.icon} />{p.live ? t('status.succeeded') : line.label}</span><div className="hint">{lr?.status === 'waiting' || lr?.status === 'running' ? t('dash.started', { when: when(lang, lr.created_at) }) : lr?.status === 'succeeded' && !lr.result?.dry_run ? when(lang, lr.created_at) : p.live ? t('dash.lastrun', { when: when(lang, lr?.created_at ?? 0) }) : line.text}</div></> : <span className="hint">{t('dash.never')}</span>}
      </div>
      <div className="pcell">
        {url ? <><span className="chip"><span className="ltr">{url}</span><button type="button" className="chip-btn" aria-label={t('dash.copy')} onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}><Icon name={copied ? 'check' : 'link'} /></button></span>{left !== null && <div className="hint">{t('dash.addr.ends', { n: num(lang, left) })}</div>}</> : <span className="hint">{t('dash.addr.none')}</span>}
      </div>
      <div className="pcell">{p.live ? <span className="cost">{money(lang, p.live.hourly_incl_vat)} <i>{t('ar.hr')}</i></span> : <span className="cost"><i>{t('dash.cost.none')}</i></span>}</div>
      <div className="pcell acts-cell">
        {act && <button type="button" className={'btn btn-s' + (act.primary ? ' btn-p' : '')} onClick={() => nav(act.to)}>{act.label}</button>}
        {url && <a className="btn btn-s" href={url} target="_blank" rel="noreferrer noopener">{t('dash.act.open')}</a>}
        <Link className="btn btn-s" to={`/projects/${p.id}`}>{lr ? t('dash.act.newrun') : t('dash.act.details')}</Link>
      </div>
    </li>
  );
}

export function DashboardSkeleton() {
  const { t } = useI18n();
  return (
    <div className="dash" role="status" aria-busy="true" aria-label={t('dash.loading')}>
      <div className="sk" style={{ height: 48, width: 360, maxWidth: '100%' }} /><div className="sk" style={{ height: 20, width: 520, maxWidth: '100%', marginTop: 14 }} />
      <div className="sumcards" style={{ marginTop: 28 }}>{[0, 1, 2].map((i) => <div className="card pad stack" key={i}><div className="sk" style={{ height: 24, width: 140 }} /><div className="sk" style={{ height: 34, width: 120 }} /><div className="sk" style={{ height: 16 }} /></div>)}</div>
      <div className="stack" style={{ marginTop: 20 }}>{[0, 1, 2].map((i) => <div className="card pad row" key={i} style={{ flexWrap: 'nowrap' }}><div className="sk" style={{ width: 44, height: 44, borderRadius: '50%', flex: 'none' }} /><div style={{ flex: 1 }} className="stack"><div className="sk" style={{ height: 18, width: 220, maxWidth: '100%' }} /><div className="sk" style={{ height: 14, width: '60%' }} /></div><div className="sk" style={{ height: 26, width: 96, borderRadius: 999 }} /></div>)}</div>
    </div>
  );
}

/** No project yet: the ways to start, a few ready-made apps to install, and what happens next. */
function EmptyDashboard({ connected }: { connected: boolean }) {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [apps, setApps] = useState<OneClickAppOut[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.oneclickApps().then((r) => setApps(r.apps.filter((a) => !a.needs_vm).slice(0, 4))).catch(() => setApps([])); }, []);
  const install = async (a: OneClickAppOut) => {
    setBusy(a.app); setErr(null);
    try { const p = await api.createOneClick(a.app, a.app); nav(`/runs/${(await api.startRun(p.id, { mode: 'deploy' })).id}`); }
    catch (e) { setErr(errorText(lang, e)); setBusy(null); }
  };
  const starts: { to: string; icon: string; t: string; d: string }[] = [
    { to: '/new', icon: 'file', t: t('new.tab.zip'), d: t('dash.start.zip.d') },
    { to: '/new?source=git', icon: 'link', t: t('new.tab.git'), d: t('dash.start.git.d') },
  ];
  return (
    <div className="dash">
      <div className="page-h"><div><h1 className="title">{t('dash.title')}</h1><p className="lead">{t('dash.empty.lead')}</p></div></div>
      {!connected && <div className="banner" role="alert" style={{ marginBottom: 20 }}><Icon name="warn" className="ic big" /><div><b>{t('new.noconn')}</b> <Link to="/settings" className="link">{t('new.noconn.link')}</Link></div></div>}
      <AgentPrompt connected={connected} />
      <h2 className="sect" style={{ margin: '24px 0 12px' }}>{t('dash.or')}</h2>
      <div className="startgrid two">
        {starts.map((s) => (
          <Link key={s.to} to={s.to} className="card pad stack lift startcard"><Icon name={s.icon} className="ic big" /><b>{s.t}</b><span className="hint">{s.d}</span></Link>
        ))}
      </div>
      {err && <div className="err" role="alert" style={{ marginTop: 16 }}><Icon name="alert" />{err}</div>}
      {apps && apps.length > 0 && <>
        <div className="row" style={{ justifyContent: 'space-between', margin: '32px 0 12px' }}><h2 className="sect" style={{ margin: 0 }}>{t('dash.popular.t')}</h2><Link className="link" to="/apps">{t('dash.popular.all')}</Link></div>
        <div className="startgrid four">
          {apps.map((a) => (
            <div key={a.app} className="card pad stack" style={{ gap: 8 }}>
              <b className="mono ltr" dir="ltr">{a.app}</b>
              {/* Liara writes the descriptions in Persian: they are shown to Persian readers, and categories to everyone else */}
              {lang === 'fa' && a.about ? <p className="hint" style={{ margin: 0, flex: 1 }}>{a.about.slice(0, 90)}{a.about.length > 90 ? '…' : ''}</p>
                : <div className="row" style={{ gap: 6, flex: 1, alignContent: 'flex-start' }}>{a.categories.slice(0, 2).map((c) => <span key={c} className="chip">{c}</span>)}</div>}
              <div><button type="button" className="btn btn-s" disabled={!connected || !!busy} onClick={() => void install(a)}>{busy === a.app ? t('proj.starting') : t('rm.install')}</button></div>
            </div>
          ))}
        </div>
      </>}
    </div>
  );
}
