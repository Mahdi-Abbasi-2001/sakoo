import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { when } from '../lib/time';

/** Every release of the app on Liara, and a way back to an earlier one. A rollback is one explicit click after reading what it does. */
export function ReleasesSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [data, setData] = useState<G.ReleasesOut | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(async () => { try { setData(await api.releases(pid)); setLoadErr(null); } catch (e) { setLoadErr(errorText(lang, e)); } }, [pid, lang]);
  useEffect(() => { void load(); }, [load]);

  const go = async (r: G.ReleaseOut) => {
    setBusy(r.id); setResult(null);
    try {
      const out = await api.rollback(pid, r.id);
      setResult(out.ok ? { ok: true, text: out.serving === false ? t('rel.done_unchecked') : t('rel.done') } : { ok: false, text: t('rel.failed', { state: out.state ?? '?' }) });
      setAsking(null); await load();
    } catch (e) { setResult({ ok: false, text: errorText(lang, e) }); } finally { setBusy(null); }
  };

  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('rel.t')} aria-busy={!data && !loadErr}>
      <h2 className="card-t">{t('rel.t')}</h2>
      <p className="hint">{t('rel.d')}</p>
      {!data && !loadErr && <div className="stack" style={{ gap: 8 }} role="status" aria-label={t('dom.loading')}><div className="sk" style={{ height: 44 }} /><div className="sk" style={{ height: 44 }} /></div>}
      {loadErr && <p className="hint" role="status">{loadErr}</p>}
      {data && data.releases.length === 0 && <p className="hint">{t('rel.empty')}</p>}
      {data && data.releases.map((r, i) => (
        <div className="stack" style={{ gap: 6, borderTop: i ? '1px solid var(--line)' : undefined, paddingTop: i ? 10 : 0 }} key={r.id}>
          <div className="row" style={{ gap: 8 }}>
            <span className="mono">#{data.releases.length - i}</span>
            <span className={'badge ' + (String(r.state).toUpperCase() === 'READY' ? 's-ok' : String(r.state).toUpperCase() === 'FAILED' ? 's-fail' : 's-wait')}>{r.state ?? '?'}</span>
            {r.current && <span className="badge s-run"><Icon name="check" />{t('rel.current')}</span>}
            {r.created_at && <span className="hint">{when(lang, Date.parse(r.created_at) / 1000)}</span>}
            <span style={{ flex: 1 }} />
            {r.rollback && asking !== r.id && <button type="button" className="btn btn-s" disabled={busy !== null} onClick={() => { setAsking(r.id); setResult(null); }}>{t('rel.rollback')}</button>}
          </div>
          {(r.message || r.commit || r.branch) && <p className="hint">{[r.message, r.branch, r.commit?.slice(0, 7)].filter(Boolean).join(' · ')}</p>}
          {asking === r.id && (
            <div className="stack" style={{ gap: 6 }} role="alertdialog" aria-label={t('rel.rollback')}>
              <p>{t('rel.confirm')}</p>
              {r.rollback === 'image' && <p className="hint">{t('rel.via_image')}</p>}
              <div className="row">
                <button type="button" className="btn btn-p" disabled={busy !== null} onClick={() => void go(r)}>{busy === r.id ? t('dom.checking') : t('rel.yes')}</button>
                <button type="button" className="btn" disabled={busy !== null} onClick={() => setAsking(null)}>{t('proj.rm.no')}</button>
              </div>
            </div>
          )}
        </div>
      ))}
      {result && <p role="status" className={result.ok ? 'hint' : 'err'}>{result.text}</p>}
    </section>
  );
}
