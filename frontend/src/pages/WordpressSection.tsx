import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { num } from '../run/format';

/** Send a WordPress project's own code to a managed Liara WordPress site. Liara has no deploy API for it, so this uses the FTPS access made in the console. */
export function WordpressSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const [uploads, setUploads] = useState(false);
  const [plan, setPlan] = useState<G.WpPlanOut | null>(null);
  const [f, setF] = useState({ host: '', port: '21', user: '', password: '', implicit: false });
  const [job, setJob] = useState<G.WpJobOut | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setErr(null); api.wpPlan(pid, uploads).then(setPlan).catch((e) => { setPlan(null); setErr(errorText(lang, e)); }); }, [pid, uploads, lang]);
  useEffect(() => {
    if (job?.state !== 'running') return;
    const id = setInterval(() => { api.wpStatus(pid).then(setJob).catch(() => undefined); }, 1000);
    return () => clearInterval(id);
  }, [job?.state, pid]);
  const send = async () => {
    setBusy(true); setErr(null);
    try { setJob(await api.wpUpload(pid, { host: f.host.trim(), port: Number(f.port) || 21, user: f.user, password: f.password, implicit: f.implicit, uploads, confirm: true })); setF((x) => ({ ...x, password: '' })); }
    catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); }
  };
  const set = (k: 'host' | 'port' | 'user' | 'password') => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('wp.t')}>
      <h2 className="card-t">{t('wp.t')}</h2>
      <p className="hint">{t('wp.d')}</p>
      <ol className="stack" style={{ gap: 4, paddingInlineStart: 20, margin: 0 }}>
        <li>{t('wp.s1')} <a className="link" href="https://console.liara.ir/wordpress" target="_blank" rel="noreferrer noopener">console.liara.ir/wordpress</a></li>
        <li>{t('wp.s2')}</li><li>{t('wp.s3')}</li>
      </ol>
      {plan && <p className="row" style={{ gap: 8 }}><Icon name="check" />{t('wp.plan', { n: num(lang, plan.files), mb: num(lang, Math.max(1, Math.round(plan.total_bytes / 1048576))), parts: plan.parts.join(', ') })}</p>}
      <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={uploads} onChange={(e) => setUploads(e.target.checked)} />{t('wp.uploads')}</label>
      {plan && <details><summary>{t('wp.files')}</summary><ul className="mono ltr" dir="ltr" style={{ margin: 0, paddingInlineStart: 18 }}>{plan.sample.map((x) => <li key={x}>{x}</li>)}</ul></details>}
      <form className="stack" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <div className="row" style={{ gap: 10, alignItems: 'flex-end' }}>
          <div className="field" style={{ flex: 2, minWidth: 200 }}><label className="lbl" htmlFor="wp-host">{t('wp.host')}</label><input id="wp-host" className="inp mono ltr" dir="ltr" autoComplete="off" spellCheck={false} value={f.host} onChange={set('host')} /></div>
          <div className="field" style={{ width: 90 }}><label className="lbl" htmlFor="wp-port">{t('wp.port')}</label><input id="wp-port" className="inp mono ltr" dir="ltr" inputMode="numeric" value={f.port} onChange={set('port')} /></div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          <div className="field" style={{ flex: 1, minWidth: 160 }}><label className="lbl" htmlFor="wp-user">{t('wp.user')}</label><input id="wp-user" className="inp mono ltr" dir="ltr" autoComplete="off" value={f.user} onChange={set('user')} /></div>
          <div className="field" style={{ flex: 1, minWidth: 160 }}><label className="lbl" htmlFor="wp-pass">{t('wp.pass')}</label><input id="wp-pass" className="inp mono ltr" dir="ltr" type="password" autoComplete="off" value={f.password} onChange={set('password')} /></div>
        </div>
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={f.implicit} onChange={(e) => setF((x) => ({ ...x, implicit: e.target.checked, port: e.target.checked && x.port === '21' ? '990' : !e.target.checked && x.port === '990' ? '21' : x.port }))} />{t('wp.implicit')}</label>
        <p className="hint">{t('wp.never')}</p>
        <div><button type="submit" className="btn btn-p" disabled={busy || job?.state === 'running' || !plan || !f.host.trim() || !f.user || !f.password}>{t('wp.send')}</button></div>
      </form>
      {job?.state === 'running' && <p role="status">{t('wp.running', { sent: num(lang, job.sent), total: num(lang, job.total) })}</p>}
      {job?.state === 'done' && <div className="stack" style={{ gap: 4 }} role="status"><p className="row" style={{ gap: 8 }}><Icon name="check" />{t('wp.done', { n: num(lang, job.sent) })}</p><p className="hint">{t('wp.after')}</p></div>}
      {job?.state === 'failed' && <div className="err" role="alert"><Icon name="alert" />{t('err.' + (job.error ?? ''))}</div>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
