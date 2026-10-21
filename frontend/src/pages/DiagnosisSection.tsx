import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { when } from '../lib/time';
import { num } from '../run/format';
import { ActionCard } from '../run/parts/AgentChat';

const TONE: Record<string, string> = { healthy: 's-ok', degraded: 's-warn', down: 's-fail' };
type Sig = { http?: { status: number | null; ms: number | null; error: string | null }; release?: { state: string; age_minutes: number | null } | null; memory?: { peak_percent: number } | null; cpu?: { peak_percent: number } | null };

/** Why is my running app failing or slow? Reads the signals now and ranks the causes: no language model is involved, so it also works when the model is not reachable. */
export function DiagnosisSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [d, setD] = useState<G.LiveDiagnosisOut | null>(null);
  const [auto, setAuto] = useState<'outage' | 'daily' | null>(null);   // what is shown was found by the agent itself, and when
  const [ai, setAi] = useState<G.AiHypothesis[] | null>(null); const [aiBusy, setAiBusy] = useState(false);   // what is shown was found by the agent itself when the site went down
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const run = async () => { setBusy(true); setErr(null); setRecovered(false); try { setD(await api.diagnosis(pid)); setAuto(null); setAi(null); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  const [card, setCard] = useState<{ key: string; a: G.ChatActionOut; tool: string; args: Record<string, unknown> } | null>(null);
  const [cardBusy, setCardBusy] = useState(false); const [rechecking, setRechecking] = useState(false); const [recovered, setRecovered] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => { let dead = false; api.diagnosisLatest(pid).then((r) => { if (!dead && r.diagnosis) { setD(r.diagnosis); setAuto(r.source === 'daily' ? 'daily' : 'outage'); } }).catch(() => undefined); return () => { dead = true; }; }, [pid]);
  const preview = async (key: string, tool: string, args: Record<string, unknown>) => {
    setErr(null); setCardBusy(true);
    try { setCard({ key, a: await api.fixPreview(pid, tool, args), tool, args }); } catch (e) { setErr(errorText(lang, e)); } finally { setCardBusy(false); }
  };
  const decide = async (_id: string, b: { decision: 'approve' | 'decline'; value?: string }) => {
    if (!card) return;
    if (b.decision === 'decline') { setCard(null); return; }
    setCardBusy(true);
    try {
      await api.fixApply(pid, card.tool, card.args, b.value);
      setCard({ ...card, a: { ...card.a, status: 'done' } }); setRechecking(true);
      timer.current = setTimeout(async () => { setRechecking(false); try { const again = await api.diagnosis(pid); setD(again); setAuto(null); setAi(null); setRecovered(again.state === 'healthy'); } catch (e) { setErr(errorText(lang, e)); } setCard(null); }, 20000);
    } catch (e) { setCard({ ...card, a: { ...card.a, status: 'failed', error: errorText(lang, e) } }); } finally { setCardBusy(false); }
  };
  const askAi = async () => { setAiBusy(true); setErr(null); try { setAi((await api.analyzeDiagnosis(pid)).hypotheses); } catch (e) { setErr(errorText(lang, e)); } finally { setAiBusy(false); } };
  const aiShown = ai ?? d?.ai ?? null;
  const sig = (d?.signals ?? {}) as Sig;
  const pct = (v?: number | null) => (v == null ? t('diag.sig.nodata') : `${num(lang, v)}%`);
  const text = (key: string, fallback: string, params?: Record<string, unknown>) => { const v = t(key, (params ?? {}) as never); return v === key ? fallback : v; };
  const show = (v: unknown) => (Array.isArray(v) ? v.join(', ') : String(v ?? ''));
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('diag.t')} aria-busy={busy}>
      <div className="row" style={{ justifyContent: 'space-between', gap: 12 }}>
        <h2 className="sect">{t('diag.t')}</h2>
        <button type="button" className="btn btn-s btn-p" disabled={busy} onClick={() => void run()}>{busy ? t('diag.running') : t(d ? 'diag.again' : 'diag.run')}</button>
      </div>
      {!d && !err && <p className="hint">{t('diag.d')}</p>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      {rechecking && <p className="hint" role="status">{t('diag.rechecking')}</p>}
      {recovered && <div className="badge s-ok" role="status"><Icon name="check" />{t('diag.recovered')}</div>}
      {d && auto && <p className="hint" role="status"><Icon name="alert" /> {t(auto === 'daily' ? 'diag.auto.daily' : 'diag.auto')} · {when(lang, d.checked_at)}</p>}
      {d && <>
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <span className={'badge ' + (TONE[d.state] ?? '')}><Icon name={d.state === 'healthy' ? 'check' : d.state === 'down' ? 'x' : 'warn'} />{t(`diag.state.${d.state}`)}</span>
          <span className="chip">{t('diag.sig.http')}: <span className="mono ltr">{sig.http?.status ? `${sig.http.status}${sig.http.ms != null ? ` · ${num(lang, sig.http.ms)} ms` : ''}` : t('diag.sig.noanswer', { kind: sig.http?.error ?? '?' })}</span></span>
          <span className="chip">{t('diag.sig.memory')}: <span className="mono">{pct(sig.memory?.peak_percent)}</span></span>
          <span className="chip">{t('diag.sig.cpu')}: <span className="mono">{pct(sig.cpu?.peak_percent)}</span></span>
          {sig.release && <span className="chip">{t('diag.sig.release')}: <span className="mono">{sig.release.state}{sig.release.age_minutes != null ? ` · ${t('diag.sig.age', { min: num(lang, Math.round(sig.release.age_minutes)) })}` : ''}</span></span>}
        </div>
        {d.causes.length === 0 && <p className="hint">{t('diag.none')}</p>}
        {(d.advice ?? []).length > 0 && <div className="stack" style={{ gap: 4 }}><h3 className="lbl">{t('diag.advice.t')}</h3>
          {(d.advice ?? []).map((a) => <span key={a.code}>• {text(`diag.advice.${a.code}`, a.code, Object.fromEntries(Object.entries(a.params ?? {}).map(([k, v]) => [k, typeof v === 'number' ? num(lang, v) : show(v)])))}{(a.fixes ?? []).some((f) => f.tool) && <> <button type="button" className="btn btn-s" disabled={cardBusy || !!card} onClick={() => { const f = (a.fixes ?? []).find((x) => x.tool)!; void preview(`advice-${a.code}`, String(f.tool), (f.args ?? {}) as Record<string, unknown>); }}>{t('diag.apply')}</button></>}
            {card?.key === `advice-${a.code}` && <ActionCard a={card.a} busy={cardBusy} decide={decide} />}</span>)}</div>}
        {d.causes.length > 0 && <>
          <h3 className="lbl">{t('diag.causes')}</h3>
          <ol className="stack" style={{ gap: 12, paddingInlineStart: 18 }}>
            {d.causes.map((c, i) => {
              const evidence = c.evidence ?? []; const fixes = c.fixes ?? [];
              const params = Object.fromEntries(Object.entries(c.params ?? {}).map(([k, v]) => [k, typeof v === 'number' ? num(lang, v) : show(v)]));
              return (
                <li key={c.code} className="stack" style={{ gap: 4 }}>
                  <b>{num(lang, i + 1)}. {text(`diag.cause.${c.code}`, c.code, params)}</b>
                  <span className="hint">{text(`diag.cause.${c.code}.d`, '', params)}</span>
                  {evidence.length > 0 && <div className="stack" style={{ gap: 2 }}><span className="hint">{t('diag.evidence')}</span>{evidence.slice(0, 3).map((e, j) => <code key={j} className="mono ltr" style={{ display: 'block', overflowWrap: 'anywhere', opacity: 0.85 }}>{e}</code>)}</div>}
                  {fixes.length > 0 && <div className="stack" style={{ gap: 2 }}><span className="hint">{t('diag.fix')}</span>{fixes.map((f, j) => { const key = `${c.code}-${j}`; const tool = typeof f.tool === 'string' ? f.tool : null; return (
                    <div key={j} className="stack" style={{ gap: 6 }}>
                      <span>• {text(`diag.fix.${String(f.code)}`, String(f.text ?? ''), (f.params ?? {}) as Record<string, unknown>)}{tool && !card && !rechecking && <> <button type="button" className="btn btn-s" disabled={cardBusy} onClick={() => void preview(key, tool, (f.args ?? {}) as Record<string, unknown>)}>{t('diag.apply')}</button></>}</span>
                      {card?.key === key && <ActionCard a={card.a} busy={cardBusy} decide={decide} />}
                    </div>); })}</div>}
                </li>
              );
            })}
          </ol>
          <p className="hint">{t('diag.ask')}</p>
        </>}
        {d.state !== 'healthy' && <div className="stack" style={{ gap: 6 }}>
          {!aiShown && <div><button type="button" className="btn btn-s" disabled={aiBusy} onClick={() => void askAi()}>{aiBusy ? t('diag.ai.asking') : t('diag.ai.ask')}</button></div>}
          {aiShown && <><h3 className="lbl">{t('diag.ai.t')}</h3>
            {aiShown.length === 0 && <p className="hint">{t('diag.ai.none')}</p>}
            <ol className="stack" style={{ gap: 10, paddingInlineStart: 18 }}>{aiShown.map((h, i) => (
              <li key={i} className="stack" style={{ gap: 3 }}>
                <b>{h.title}</b> <span className="hint">{t(`diag.ai.${h.confidence === 'medium' ? 'medium' : 'low'}`)}</span>
                <span>{h.explanation}</span>
                {(h.evidence ?? []).map((e, j) => <code key={j} className="mono ltr" style={{ display: 'block', overflowWrap: 'anywhere', opacity: 0.85 }}>{e}</code>)}
                {!h.grounded && <span className="hint"><Icon name="warn" /> {t('diag.ai.ungrounded')}</span>}
                <span><span className="hint">{t('diag.ai.next')}: </span>{h.next_step}</span>
              </li>))}</ol>
            <p className="hint">{t('diag.ai.note')}</p></>}
        </div>}
      </>}
    </section>
  );
}
