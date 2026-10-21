import { ChatComposer, ChatMessages, useChatThread } from './parts/AgentChat';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Orb } from '../components/Orb';
import { Ticker } from '../components/Ticker';
import { Icon } from '../components/Icon';
import { api } from '../api/client';
import { errorText } from '../i18n/errors';
import { useI18n, usePageTitle } from '../i18n';
import { clock, compact, money, num } from './format';
import { ApprovalCard, useCountdown, type Responder } from './parts/Approval';
import { Announcer } from './Announcer';
import { Pipeline } from './parts/Pipeline';
import { Architecture } from './parts/Architecture';
import { Timeline } from './parts/Timeline';
import { Details } from './parts/Details';
import { Cleanup, FailBanner, ResultCard } from './parts/Results';
import type { RunViewState } from './types';
import type { Conn } from './useRunStream';

type Sit = 'stopped' | 'removed' | 'reading' | 'thinking' | 'waiting' | 'question' | 'secret' | 'fix' | 'working' | 'live' | 'preview' | 'cancelled' | 'failed' | 'buildfailed' | 'interrupted';
export function situation(v: RunViewState): Sit {
  if (v.removed) return 'removed';
  if (v.status === 'interrupted') return 'interrupted';
  const o = v.openRequests[0];
  if (o) return o.kind === 'question' ? 'question' : o.kind === 'secret' ? 'secret' : o.confirmKind === 'plan' ? 'waiting' : 'fix';
  if (v.result) { if (v.result.ok) return v.result.dryRun ? 'preview' : 'live'; if (v.result.stopped === 'cancelled') return v.result.reasonCode === 'run_cancelled' ? 'stopped' : 'cancelled'; return v.diagnoses.length ? 'buildfailed' : 'failed'; }
  if (v.phase === 'deploy' || v.phase === 'confirm') return 'working';
  return v.phase === 'plan' ? 'thinking' : 'reading';
}

export interface RunViewProps {
  view: RunViewState; conn: Conn; runId: string; projectId?: number; projectName: string;
  onRespond: Responder; limitSeconds: number; startedAt?: number; freeDays?: number | null; demo?: boolean;
  onRefresh?: () => void; onCleaned?: (deleted: { apps: string[]; databases: string[]; buckets?: string[]; mail_servers?: string[] }) => void; maxAttempts?: number | null;
}

export function RunView({ view, conn, runId, projectId, projectName, onRespond, limitSeconds, startedAt, freeDays = null, demo, onRefresh, onCleaned }: RunViewProps) {
  const { t, lang } = useI18n();
  const sit = situation(view);
  usePageTitle('title.run_state', { state: t('agent.' + view.agent), project: projectName });
  const req = view.openRequests[0];
  const [filter, setFilter] = useState<string | null>(null);
  const [tab, setTab] = useState<'logs' | 'tests' | 'plan' | 'files' | 'costs'>('logs');
  const [cleaning, setCleaning] = useState(false);
  const applyRef = useRef<HTMLButtonElement>(null);
  const [resultOpen, setResultOpen] = useState(false);   // the outcome (and what to do about it) opens when a run ends in front of you; it never takes room from the main cards
  const [stopAsk, setStopAsk] = useState(false); const [stopBusy, setStopBusy] = useState(false); const [stopErr, setStopErr] = useState<string | null>(null);
  const [askOpen, setAskOpen] = useState(true);   // a question or approval opens in the middle of the screen; it can be closed and opened again from the page
  useEffect(() => { setAskOpen(!!req); }, [req?.eventId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const left = useCountdown(req, limitSeconds, startedAt);

  const h1Ref = useRef<HTMLHeadingElement>(null);
  const lastReq = useRef<string | undefined>(undefined);
  useEffect(() => {
    const now = req ? String(req.eventId) : undefined;
    if (lastReq.current !== undefined && lastReq.current !== now) {
      const a = document.activeElement;
      if (!a || a === document.body || !document.body.contains(a)) h1Ref.current?.focus({ preventScroll: true });
    }
    lastReq.current = now;
  }, [req?.eventId]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (view.removed) h1Ref.current?.focus({ preventScroll: true }); }, [!!view.removed]);   // eslint-disable-line react-hooks/exhaustive-deps


  const hmoney = view.status === 'succeeded' || view.status === 'failed' || sit === 'fix' || sit === 'working' ? view.money.currentHourly : view.money.plannedHourly ?? 0;
  const mmoney = view.status === 'succeeded' || view.status === 'failed' || sit === 'fix' || sit === 'working' ? view.money.currentMonthly : view.money.plannedMonthly ?? 0;
  const thread = useChatThread(demo ? 0 : projectId ?? 0, view.t0);
  const chatOn = !demo && !!projectId;
  const active = view.status === 'running' || view.status === 'waiting';
  const watched = useRef(false);
  useEffect(() => { if (active) watched.current = true; else if (watched.current && (view.result || view.removed)) { watched.current = false; setResultOpen(true); } }, [active, !!view.result, !!view.removed]);   // eslint-disable-line react-hooks/exhaustive-deps
  const c = view.result?.created;   // the result card already has a delete button when it lists something created
  const shownInCard = !!c && (c.apps.length + c.databases.length + (c.buckets ?? []).length + (c.mail_servers ?? []).length) > 0;
  const going = !demo && (view.status === 'running' || view.status === 'waiting');
  const stop = async () => { setStopBusy(true); setStopErr(null); try { await api.cancelRun(runId); setStopAsk(false); } catch (e) { setStopErr(errorText(lang, e)); } finally { setStopBusy(false); } };
  const gone = sit === 'removed';
  const finished = !!(view.result || view.removed || view.status === 'interrupted');
  const showingNow = sit === 'fix' || sit === 'working' || sit === 'live' || sit === 'buildfailed' || sit === 'failed';
  const live = sit === 'live';

  return (
    <div className="run" data-situation={sit} data-agent={view.agent} data-card="none">
      <header className="dash-head">
        {conn === 'reconnecting' && <div className="conn warn" role="status"><Icon name="warn" />{t('conn.reconnecting')}</div>}
        {conn === 'error' && <div className="conn fail" role="alert"><Icon name="x" />{t('conn.error')} {onRefresh && <button type="button" className="link" onClick={onRefresh}>{t('conn.retry')}</button>}</div>}
        <div className="h-id">
          <div className="orb-box"><Orb state={view.agent} size={56} /></div>
          <div className="h-text">
            <div className="crumb">{demo ? t('run.crumb.projects') : <Link to="/">{t('run.crumb.projects')}</Link>} / {demo || !projectId ? <span className="mono">{projectName}</span> : <Link to={`/projects/${projectId}`} className="mono">{projectName}</Link>} / {t('run.crumb.run')}</div>
            <div className="h-line">{req ? <button type="button" className={'badge badge-btn ' + AGENT_TONE[view.agent]} onClick={() => setAskOpen(true)}><Icon name={AGENT_ICON[view.agent]} />{t('agent.' + view.agent)}</button> : <span className={'badge ' + AGENT_TONE[view.agent]}><Icon name={AGENT_ICON[view.agent]} />{t('agent.' + view.agent)}</span>}
              <h1 className="h1" ref={h1Ref} tabIndex={-1}><span className="gt">{t(`run.h1.${sit}.a`)}</span> {t(`run.h1.${sit}.b`)}</h1></div>
              {req && !askOpen && <button type="button" className="btn btn-p btn-s ask-again" onClick={() => setAskOpen(true)}><Icon name="diamond" />{t('ask.open')}</button>}
              {finished && !resultOpen && <button type="button" className="btn btn-s ask-again" onClick={() => setResultOpen(true)}><Icon name="diamond" />{t('res.show')}</button>}
              {going && !stopAsk && <button type="button" className="btn btn-s btn-d stop-run" onClick={() => setStopAsk(true)}><Icon name="x" />{t('stop.btn')}</button>}
              {going && stopAsk && <span className="stop-ask" role="alertdialog" aria-label={t('stop.btn')}><span className="hint">{t('stop.sure')}</span>
                <button type="button" className="btn btn-s btn-d" disabled={stopBusy} onClick={() => void stop()}>{stopBusy ? t('proj.starting') : t('stop.yes')}</button>
                <button type="button" className="btn btn-s" disabled={stopBusy} onClick={() => setStopAsk(false)}>{t('stop.no')}</button></span>}
              {stopErr && <span className="err" role="alert"><Icon name="alert" />{stopErr}</span>}
          </div>
        </div>
        <div className="tiles">
          <div className="tile"><div className="ml">{t(live || gone ? 'tile.running_hr' : showingNow ? 'tile.billing_hr' : 'tile.cost_hr')}</div><div className="v"><span className="n gt"><Ticker value={hmoney} format={(n) => money(lang, n)} /></span> <span className="hint">{t('tile.toman_hr')}</span></div></div>
          <div className="tile"><div className="ml">{t(live || gone ? 'tile.running_month' : showingNow ? 'tile.billing_month' : 'tile.cost_month')}</div><div className="v"><span className="n"><Ticker value={mmoney} format={(n) => compact(lang, n)} /></span> <span className="hint">{t('tile.toman_month')}</span></div></div>
          {gone
            ? <div className="tile"><div className="ml">{t('tile.deleted')}</div><div className="v"><span className="n">{num(lang, (view.removed?.apps.length ?? 0) + (view.removed?.databases.length ?? 0))}</span> <span className="hint">{t('tile.deleted_sub')}</span></div></div>
            : req && left !== null ? <div className="tile"><div className="ml">{t('tile.expires')}</div><div className="v mono big-clock">{clock(lang, left)}</div></div>
            : null}
        </div>
      </header>
      <Pipeline view={view} filter={filter} onFilter={setFilter} />
      <Timeline view={view} filter={filter} after={chatOn ? <ChatMessages th={thread} /> : null} footer={chatOn ? <ChatComposer th={thread} /> : null} />
      <Architecture view={view} />
      <Details runId={demo ? null : runId} view={view} filter={filter} tab={tab} onTab={setTab} />
      {finished && resultOpen && <AskModal label={t('res.show')} onClose={() => setResultOpen(false)}><div className="stack result-modal" style={{ gap: 12 }}>
            {cleaning && <Cleanup runId={runId} projectName={projectName} demo={demo} onDone={(d) => { setCleaning(false); onCleaned?.(d); onRefresh?.(); }} onCancel={() => setCleaning(false)} />}
            <ResultCard view={view} projectId={projectId} freeDays={freeDays} demo={demo} onCleanup={() => setCleaning(true)} />
            <div className="acts dash-acts">
              {view.dryRun && view.wouldSend.length > 0 && <button type="button" className="link" onClick={() => { setResultOpen(false); setTab('plan'); document.getElementById('details')?.scrollIntoView({ block: 'nearest' }); }}>{t('run.see_requests')}</button>}
              {!demo && view.plan && <a className="link" href={`/api/runs/${runId}/report`} download>{t('run.report')}</a>}
              {!gone && !view.release && !shownInCard && view.status !== 'running' && view.status !== 'waiting' && view.resources.some((r) => r.kind !== 'disk' && r.state !== 'proposed' && r.state !== 'removed') && !view.result?.dryRun && <button type="button" className="btn btn-d" onClick={() => setCleaning(true)}>{t('run.delete_created')}</button>}
            </div>
            <FailBanner view={view} />
          </div></AskModal>}
      {req && askOpen && <AskModal label={t('ap.waiting')} onClose={() => setAskOpen(false)}><ApprovalCard view={view} req={req} onRespond={onRespond} limitSeconds={limitSeconds} startedAt={startedAt} applyRef={applyRef} /></AskModal>}
      <Announcer view={view} projectName={projectName} limitSeconds={limitSeconds} />
    </div>
  );
}

export const AGENT_TONE = { reading: 's-pend', thinking: 's-run', waiting: 's-wait', working: 's-run', done: 's-ok', failed: 's-fail' } as const;
export const AGENT_ICON = { reading: 'file', thinking: 'dots', waiting: 'diamond', working: 'loader', done: 'check', failed: 'x' } as const;

/** The question or approval in the middle of the screen. Escape, the close button and a click outside close it. */
function AskModal({ children, onClose, label }: { children: React.ReactNode; onClose: () => void; label: string }) {
  const { t } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>('input, textarea, button.opt, .btn-p')?.focus({ preventScroll: true });
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', key);
    return () => { window.removeEventListener('keydown', key); before?.focus?.({ preventScroll: true }); };
  }, [onClose]);
  return (
    <div className="ask-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="ask-modal" role="dialog" aria-modal="true" aria-label={label} ref={box}>
        <button type="button" className="icon-btn ask-x" aria-label={t('ask.close')} onClick={onClose}><Icon name="x" /></button>
        {children}
      </div>
    </div>
  );
}
