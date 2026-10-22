import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useI18n } from '../../i18n';
import { serverNow } from '../../api/client';
import { Icon } from '../../components/Icon';
import { Ticker } from '../../components/Ticker';
import { useSay } from '../wording';
import { clock, money, signed } from '../format';
import { CodeFixChoice } from './CodeFixChoice';
import type { OpenReq, RunViewState } from '../types';

export interface Responder { (req: OpenReq, value: unknown): Promise<void> }

/** Seconds left before an unanswered request is declined safely. The server sends the moment (`expires_at`, its own clock); the browser's clock is only
 *  used through the offset measured from the server's Date header. The demo has no server and passes the moment it showed the request instead. */
export function useCountdown(req: OpenReq | undefined, limitSeconds: number, startedAt?: number) {
  const [, tick] = useState(0);
  useEffect(() => { if (!req) return; const t = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(t); }, [req]);
  if (!req) return null;
  if (startedAt === undefined && req.expiresAt != null) return Math.max(0, req.expiresAt - serverNow() / 1000);
  const base = startedAt ?? Math.min(serverNow(), req.ts * 1000);
  return Math.max(0, Math.min(limitSeconds, limitSeconds - (serverNow() - base) / 1000));
}

function useAction(onRespond: Responder, req: OpenReq) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { t } = useI18n();
  const run = async (tag: string, value: unknown) => {
    setBusy(tag); setErr(null);
    try { await onRespond(req, value); } catch (e) { const code = (e as { code?: string }).code; setErr(code === 'no_such_request' ? t('ap.gone') : (e as Error).message); } finally { setBusy(null); }
  };
  return { busy, err, run };
}

/** The approval card: the most prominent element of a waiting run. Apply and Decline never move or resize. */
export function ApprovalCard({ view, req, onRespond, limitSeconds, startedAt, applyRef }: { view: RunViewState; req: OpenReq; onRespond: Responder; limitSeconds: number; startedAt?: number; applyRef?: React.Ref<HTMLButtonElement> }) {
  const { t, lang } = useI18n();
  const say = useSay();
  const left = useCountdown(req, limitSeconds, startedAt);
  const { busy, err, run } = useAction(onRespond, req);
  const [text, setText] = useState('');
  const secretRef = useRef<HTMLInputElement>(null);
  const warnings = view.planNotes.filter((n) => n.level === 'warning').length + view.readiness.filter((f) => f.level === 'warning').length;
  const blockers = view.planNotes.filter((n) => n.level === 'blocker').length;
  const hasDisk = view.resources.some((r) => r.kind === 'disk' && r.priceUnverified);
  const minutes = Math.round(limitSeconds / 60);

  const head = (
    <div className="ap-head">
      <span className="badge s-wait"><Icon name="diamond" />{t('ap.waiting')}</span>
      {left !== null && <span className="mono ap-exp">{t('ap.expires', { t: clock(lang, left) })}</span>}
    </div>
  );

  if (req.kind === 'confirm' && req.confirmKind === 'plan') {
    const lines = view.resources.filter((r) => r.kind !== 'disk');
    const disks = view.resources.filter((r) => r.kind === 'disk');
    return (
      <section className="ap appr" role="region" aria-labelledby="ap-title">
        {head}
        <h2 className="ap-title" id="ap-title">{say('confirm', req.code, req.params, req.prompt)}</h2>
        <ul className="ap-lines">
          {lines.map((r) => (
            <li key={r.id}><span className="ap-ic"><Icon name={r.kind === 'database' ? 'db' : r.kind === 'bucket' ? 'disk' : r.kind === 'mail' ? 'link' : 'server'} /></span><span className="mono ap-name">{r.id}<span className="ap-sub"> {r.type}{r.plan ? ` · ${r.plan}` : ''}{r.tierLabel ? ` · ${r.tierLabel}` : ''}</span></span><span className="cost">{r.hourly !== null ? <Ticker value={r.hourly} format={(n) => money(lang, n)} /> : '—'} <i>{t('ar.hr')}</i></span></li>
          ))}
          {disks.map((r) => (
            <li key={r.id}><span className="ap-ic"><Icon name="disk" /></span><span className="mono ap-name">{r.id}<span className="ap-sub"> {t('res.disk')} · {r.mountTo}</span></span><span className="cost"><i>{t('ap.included')}</i></span></li>
          ))}
        </ul>
        <div className="ap-total" id="ap-total">
          <div><div className="hint">{t('ap.total')}</div><div className="num">≈ <Ticker value={view.money.plannedHourly ?? 0} format={(n) => money(lang, n)} /> <span>{t('ap.toman_hr')}</span></div></div>
          <div><div className="hint">{t('ap.month')}</div><div className="num"><Ticker value={view.money.plannedMonthly ?? 0} format={(n) => money(lang, n)} /> <span>{t('ap.toman')}</span></div></div>
        </div>
        <p className="ap-note">{blockers ? t('ap.blockers', { count: blockers }) : t('ap.no_blockers')}{warnings ? ' ' + t('ap.warnings', { count: warnings }) : ''}{hasDisk ? ' ' + t('ap.disk_note') : ''}</p>
        <div className="ap-acts">
          <button ref={applyRef} type="button" className="btn btn-p no-lift" aria-describedby="ap-total" disabled={!!busy || blockers > 0} onClick={() => void run('apply', true)}>{busy === 'apply' ? t('ap.working') : t('ap.apply_plan')}</button>
          <button type="button" className="btn no-lift" aria-describedby="ap-decline" disabled={!!busy} onClick={() => void run('decline', false)}>{t('ap.decline')}</button>
        </div>
        {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
        <p className="hint" id="ap-decline">{t('ap.decline_plan', { minutes })}</p>
      </section>
    );
  }

  if (req.kind === 'confirm') {            // a fix (or a patch) that needs a yes
    const delta = req.priceDeltaHourly ?? 0;
    const fix = view.diagnoses.at(-1)?.fixes.find((f) => f.code === req.code);
    return (
      <section className="ap appr" role="region" aria-labelledby="ap-title">
        {head}
        <h2 className="ap-title" id="ap-title">{fix ? say('fix', fix.code, fix.params, fix.text) : say('confirm', req.code, req.params, req.prompt)}{delta ? `? ${signed(lang, delta)} ${t('ap.toman_hr')}` : ''}</h2>
        {fix && <p className="ap-text">{say('fix', fix.code, fix.params, '', 'detail')}</p>}
        {delta > 0 && <div className="ap-delta"><span className="cost up">{signed(lang, delta)} <i>{t('ap.toman_hr')}</i></span><span className="cost up">{t('ap.delta_month', { delta: signed(lang, delta * 720) })}</span><span className="hint">{t('ap.incl_vat')}</span></div>}
        {delta > 0 && <p className="ap-note">{t('ap.delta_note')}</p>}
        {typeof req.params?.diff === 'string' && req.params.diff && <pre className="term diff ltr cf-ev" dir="ltr" aria-label={t('ag.patch')}>{req.params.diff}</pre>}
        <div className="ap-acts">
          <button ref={applyRef} type="button" className="btn btn-p no-lift" disabled={!!busy} onClick={() => void run('apply', true)}>{busy === 'apply' ? t('ap.working') : delta > 0 ? t('ap.apply_fix', { delta: signed(lang, delta) }) : t('ap.apply')}</button>
          <button type="button" className="btn no-lift" aria-describedby="ap-decline" disabled={!!busy} onClick={() => void run('decline', false)}>{t('ap.decline')}</button>
        </div>
        {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
        <p className="hint" id="ap-decline">{t('ap.decline_fix', { hourly: money(lang, view.money.currentHourly) })}</p>
      </section>
    );
  }

  if (req.kind === 'question' && req.code === 'code_fix_choice') {
    return <CodeFixChoice req={req} head={head} busy={busy} err={err && <div className="err" role="alert"><Icon name="alert" />{err}</div>} onPick={(a) => void run(a, a)} />;
  }

  if (req.kind === 'question') {
    const opts = req.options ?? [];
    const submitFree = (e: FormEvent) => { e.preventDefault(); if (text.trim()) void run('free', text.trim()); };
    return (
      <section className="ap appr" role="region" aria-labelledby="ap-title">
        {head}
        <h2 className="ap-title" id="ap-title">{say('question', req.code, req.params, req.text)}</h2>
        
        {opts.length > 0 ? (
          <div className="opts" role="group" aria-label={t('ap.answer')}>
            {opts.map((o) => <button key={o} type="button" className="opt no-lift" disabled={!!busy} onClick={() => void run(o, o)}><span className="rd" />{say('option', req.code ? `${req.code}.${o}` : null, req.params, o)}</button>)}
          </div>
        ) : (
          <form className="stack" onSubmit={submitFree}>
            <label className="lbl" htmlFor="qa">{t('ap.answer')}</label>
            <input id="qa" className="inp" value={text} onChange={(e) => setText(e.target.value)} />
            <div className="ap-acts"><button type="submit" className="btn btn-p no-lift" disabled={!!busy || !text.trim()}>{busy ? t('ap.working') : t('ap.send')}</button></div>
          </form>
        )}
        {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      </section>
    );
  }

  // a secret: the value lives in the input's DOM node only until it is sent, and is cleared in the same breath. It is never in React state.
  const sendSecret = async (e: FormEvent) => {
    e.preventDefault();
    const el = secretRef.current; if (!el || !el.value) return;
    const v = el.value; el.value = '';
    await run('secret', v);
  };
  return (
    <section className="ap appr" role="region" aria-labelledby="ap-title">
      {head}
      <h2 className="ap-title" id="ap-title">{t('ag.secret_wait', { name: req.name ?? '' })}</h2>
      <form className="stack" onSubmit={sendSecret} autoComplete="off">
        <label className="lbl" htmlFor="sv">{t('ap.secret_label', { name: req.name ?? '' })}</label>
        <input id="sv" ref={secretRef} className="inp mono" type="password" name="agent-secret" autoComplete="off" autoCapitalize="none" spellCheck={false} aria-describedby="sv-note" />
        <div className="ap-acts"><button type="submit" className="btn btn-p no-lift" disabled={!!busy}>{busy ? t('ap.working') : t('ap.send')}</button></div>
      </form>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      <p className="hint" id="sv-note">{t('ap.secret_note')}</p>
    </section>
  );
}

/** A compact bar that stays in view while a request is open and its Apply button has scrolled away. */
export function StickyBar({ req, view, onRespond, show }: { req: OpenReq; view: RunViewState; onRespond: Responder; show: boolean }) {
  const { t, lang } = useI18n();
  const { busy, run } = useAction(onRespond, req);
  if (!show || req.kind !== 'confirm') return null;
  const plan = req.confirmKind === 'plan';
  const price = plan ? money(lang, view.money.plannedHourly ?? 0) : signed(lang, req.priceDeltaHourly ?? 0);
  return (
    <div className="sticky-bar" role="region" aria-label={t('ap.waiting')}>
      <div className="grow"><span className="badge s-wait"><Icon name="diamond" />{t('ap.waiting')}</span><span className="tot">{price} <span className="hint">{t('ap.toman_hr')}</span></span></div>
      <button type="button" className="btn btn-p no-lift" disabled={!!busy} onClick={() => void run('apply', true)}>{plan ? t('ap.apply') : t('ap.apply')}</button>
      <button type="button" className="btn no-lift" disabled={!!busy} onClick={() => void run('decline', false)}>{t('ap.decline')}</button>
    </div>
  );
}
