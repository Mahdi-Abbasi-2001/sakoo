import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { useI18n } from '../i18n';
import { num } from '../run/format';

/** What Liara's API limits for this app and what the agent cannot do because the API has no call for it: said up front, so nothing is a surprise. */
export function LimitsSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [l, setL] = useState<G.AppLimitsOut | null>(null);
  useEffect(() => { let dead = false; api.appLimits(pid).then((r) => { if (!dead) setL(r); }).catch(() => undefined); return () => { dead = true; }; }, [pid]);
  const q = l?.daily_deployments;
  const pct = q && q.cap ? Math.min(100, Math.round((q.used / q.cap) * 100)) : 0;
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('lim.t')}>
      <h2 className="sect">{t('lim.t')}</h2>
      <p className="hint">{t('lim.d')}</p>
      {q && <div className="stack" style={{ gap: 6 }}>
        <span>{q.unlimited ? t('lim.daily.unlimited', { used: num(lang, q.used) }) : t('lim.daily', { used: num(lang, q.used), cap: num(lang, q.cap ?? 0), tier: q.tier ?? '' })}</span>
        {!q.unlimited && <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} style={{ height: 8, borderRadius: 4, background: 'var(--sunken)', overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: pct >= 85 ? 'var(--warn)' : 'var(--accent)' }} /></div>}
        <span className="hint">{t('lim.daily.note')}</span>
      </div>}
      {l && <div className="stack" style={{ gap: 4 }}><h3 className="lbl">{t('lim.known.t')}</h3>
        <ul className="stack" style={{ gap: 4, paddingInlineStart: 18, margin: 0 }}>{l.known.map((c) => <li key={c}>{t(`lim.known.${c}`)}</li>)}</ul></div>}
    </section>
  );
}
