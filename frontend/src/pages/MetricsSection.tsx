import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { num } from '../run/format';

function Spark({ points, label }: { points: G.MetricPointOut[]; label: string }) {
  if (points.length < 2) return <p className="hint">{label}: –</p>;
  const lo = Math.min(...points.map((p) => p.value)), hi = Math.max(...points.map((p) => p.value));
  const span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1)) * 200},${36 - ((p.value - lo) / span) * 32 - 2}`).join(' ');
  return <svg viewBox="0 0 200 36" width="100%" height="36" role="img" aria-label={label} preserveAspectRatio="none"><path d={d} fill="none" stroke="currentColor" strokeWidth="1.5" vectorEffect="non-scaling-stroke" /></svg>;
}

/** CPU and memory of the running app as Liara reports them (read-only). Liara does not document the units, so the numbers are shown as they come. */
export function MetricsSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [hours, setHours] = useState(6);
  const [m, setM] = useState<G.MetricsOut | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => { try { setErr(null); setM(await api.metrics(pid, hours)); } catch (e) { setErr(errorText(lang, e)); } }, [pid, hours, lang]);
  useEffect(() => { void load(); }, [load]);
  const last = (a: G.MetricPointOut[]) => (a.length ? a[a.length - 1].value : null);
  const mem = last(m?.memory ?? []), cpu = last(m?.cpu ?? []);
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('mt.t')}>
      <h2 className="sect">{t('mt.t')}</h2>
      <div className="row" style={{ gap: 6 }} role="group" aria-label={t('mt.range')}>
        {[1, 6, 24].map((h) => <button key={h} type="button" className={'fchip' + (hours === h ? ' on' : '')} aria-pressed={hours === h} onClick={() => setHours(h)}>{t('mt.hours', { n: num(lang, h) })}</button>)}
      </div>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      {m && <>
        <div><div className="row"><b>{t('mt.cpu')}</b><span style={{ flex: 1 }} />{cpu != null && <span className="hint mono ltr" dir="ltr">{cpu.toFixed(3)}</span>}</div><Spark points={m.cpu} label={t('mt.cpu')} /></div>
        <div><div className="row"><b>{t('mt.mem')}</b><span style={{ flex: 1 }} />{mem != null && <span className="hint mono ltr" dir="ltr">{mem > 1e6 ? (mem / 1048576).toFixed(0) + ' MB' : mem.toFixed(1)}</span>}</div><Spark points={m.memory} label={t('mt.mem')} /></div>
        {m.cpu.length === 0 && m.memory.length === 0 && <p className="hint">{t('mt.none')}</p>}
        <p className="hint">{t('mt.note')}</p>
      </>}
    </section>
  );
}
