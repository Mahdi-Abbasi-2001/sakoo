import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';

/** Where outage alerts go. The address is stored encrypted on the server and never shown again. */
export function AlertSettings() {
  const { t, lang } = useI18n();
  const [has, setHas] = useState<boolean | null>(null);
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false); const [msg, setMsg] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.settings().then((s) => setHas(!!s.has_alert_webhook)).catch(() => setHas(false)); }, []);
  const run = async (f: () => Promise<void>, ok: string) => { setBusy(true); setErr(null); setMsg(null); try { await f(); setMsg(ok); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  return (
    <section id="s-alerts" aria-labelledby="h-alerts" className="card pad stack">
      <h2 id="h-alerts" style={{ fontSize: 20, lineHeight: '28px' }}>{t('al.t')}</h2>
      <p className="hint">{t('al.d')}</p>
      {has && <p className="row" style={{ gap: 8 }}><Icon name="check" />{t('al.set')}</p>}
      <form className="row" style={{ gap: 10, alignItems: 'flex-end' }} onSubmit={(e) => { e.preventDefault(); if (url.trim()) void run(async () => { const s = await api.setAlertWebhook(url.trim()); setHas(!!s.has_alert_webhook); setUrl(''); }, t('al.saved')); }}>
        <div className="field" style={{ flex: 1, minWidth: 240 }}><label className="lbl" htmlFor="al-url">{t('al.url')}</label>
          <input id="al-url" className="inp mono ltr" dir="ltr" type="url" autoComplete="off" spellCheck={false} placeholder="https://hooks.example.com/…" value={url} onChange={(e) => setUrl(e.target.value)} /></div>
        <button type="submit" className="btn btn-p" disabled={busy || !url.trim()}>{t('al.save')}</button>
      </form>
      {has && <div className="row" style={{ gap: 10 }}>
        <button type="button" className="btn btn-s" disabled={busy} onClick={() => void run(async () => { await api.testAlertWebhook(); }, t('al.tested'))}>{t('al.test')}</button>
        <button type="button" className="btn btn-s btn-d" disabled={busy} onClick={() => void run(async () => { await api.deleteAlertWebhook(); setHas(false); }, t('al.removed'))}>{t('al.remove')}</button>
      </div>}
      {msg && <p role="status" className="hint">{msg}</p>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
