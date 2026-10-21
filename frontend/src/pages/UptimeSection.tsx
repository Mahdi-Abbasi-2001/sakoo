import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { when } from '../lib/time';
import { num } from '../run/format';

/** Is it up? The last 24 hours of checks of the running app, one bar per check. */
export function UptimeSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [u, setU] = useState<G.UptimeOut | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => { try { setU(await api.uptime(pid)); } catch (e) { setErr(errorText(lang, e)); } }, [pid, lang]);
  useEffect(() => { void load(); const id = setInterval(() => void load(), 60000); return () => clearInterval(id); }, [load]);
  const check = async () => { setBusy(true); setErr(null); try { setU(await api.uptimeCheck(pid)); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  const open = u?.incidents.find((i) => i.up_at == null);
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('up.t')}>
      <h2 className="sect">{t('up.t')}</h2>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      {u && u.watching.length === 0 && <p className="hint">{t('up.none')}</p>}
      {u && u.watching.length > 0 && (u.checks === 0 ? <p className="hint">{t('up.wait', { min: num(lang, Math.round(u.interval_seconds / 60)) })}</p> : <>
        <div className="row" style={{ gap: 8 }}>
          <span className={'badge ' + (u.current ? 's-ok' : 's-fail')}><Icon name={u.current ? 'check' : 'x'} />{u.current ? t('up.up') : t('up.down')}</span>
          <span className="hint">{t('up.pct', { pct: num(lang, u.up_percent ?? 0), n: num(lang, u.checks) })}</span>
        </div>
        <UptimeChart series={u.series} />
        {u.avg_ms != null && <p className="hint">{t('up.avg', { ms: num(lang, u.avg_ms) })}</p>}
        {open && <p className="err" role="alert"><Icon name="alert" />{t('up.since', { when: when(lang, open.down_at) })}</p>}
        {u.incidents.filter((i) => i.up_at != null).slice(-3).map((i) => <p className="hint" key={i.down_at}>{t('up.incident', { from: when(lang, i.down_at), to: when(lang, i.up_at ?? 0) })}</p>)}
      </>)}
      {u && u.watching.length > 0 && <div><button type="button" className="btn btn-s" disabled={busy} onClick={() => void check()}>{busy ? t('dom.checking') : t('up.now')}</button></div>}
    </section>
  );
}

const BUCKETS = 48;

/** The last 24 hours as a status strip (one cell per half hour: all good, some failed, down, or no check) with the response time as a line under it. */
function UptimeChart({ series }: { series: G.UptimePointOut[] }) {
  const { t, lang } = useI18n();
  const end = Date.now() / 1000, start = end - 86400, step = 86400 / BUCKETS;
  const cells = Array.from({ length: BUCKETS }, (_, i) => {
    const from = start + i * step, pts = series.filter((p) => p.ts >= from && p.ts < from + step);
    const bad = pts.filter((p) => !p.ok).length;
    return { from, n: pts.length, bad, state: pts.length === 0 ? 'none' : bad === 0 ? 'ok' : bad === pts.length ? 'down' : 'part' };
  });
  const ok = series.filter((p) => p.ok && p.ts >= start);
  const max = Math.max(1, ...ok.map((p) => p.ms)), W = 480, H = 56;
  const x = (ts: number) => ((ts - start) / 86400) * W, y = (ms: number) => H - 4 - (ms / max) * (H - 10);
  const line = ok.map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(1)} ${y(p.ms).toFixed(1)}`).join(' ');
  return (
    <div className="stack" style={{ gap: 6 }} role="img" aria-label={t('up.chart')}>
      <div className="upstrip" dir="ltr">{cells.map((c, i) => <i key={i} className={'up-' + c.state} title={`${when(lang, c.from)}: ${c.n === 0 ? t('up.nocheck') : c.bad === 0 ? t('up.up') : t('up.failed_n', { n: num(lang, c.bad), total: num(lang, c.n) })}`} />)}</div>
      {ok.length > 1 && <svg className="upline" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ direction: 'ltr' }} aria-hidden="true"><path d={`${line} L${x(ok[ok.length - 1].ts).toFixed(1)} ${H} L${x(ok[0].ts).toFixed(1)} ${H} Z`} className="up-area" /><path d={line} className="up-stroke" /></svg>}
      <div className="row upaxis" dir="ltr" style={{ justifyContent: 'space-between' }}><span className="hint">{t('up.axis.start')}</span><span className="hint">{t('up.axis.end')}</span></div>
    </div>
  );
}
