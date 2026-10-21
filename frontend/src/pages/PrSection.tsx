import { useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';

/** The agent had to change the project to make it build. Without those changes the next deploy from the repository fails the same way, so offer them as a pull request. */
export function PrSection({ pid, private: isPrivate }: { pid: number; private: boolean }) {
  const { t, lang } = useI18n();
  const [prev, setPrev] = useState<G.PrPreviewOut | null>(null);
  const [token, setToken] = useState('');
  const [connected, setConnected] = useState(false);
  useEffect(() => { api.settings().then((x) => setConnected(!!x.github_login)).catch(() => undefined); }, []);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [done, setDone] = useState<string | null>(null);
  useEffect(() => { api.prPreview(pid).then(setPrev).catch((e) => { if (!(e instanceof ApiError && ['pr_no_patch', 'pr_not_github'].includes(e.code))) setErr(errorText(lang, e)); }); }, [pid, lang]);
  if (!prev && !err) return null;
  const open = async () => { setBusy(true); setErr(null); try { setDone((await api.prCreate(pid, token.trim())).url); setToken(''); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('pr.t')}>
      <h2 className="card-t">{t('pr.t')}</h2>
      {prev && <>
        <p className="hint">{t('pr.d', { repo: prev.repo })}</p>
        <ul className="stack" style={{ gap: 4, margin: 0, paddingInlineStart: 20 }}>{prev.files.map((f) => <li key={f.path} className="mono ltr" dir="ltr">{f.path}</li>)}</ul>
        <details><summary>{t('pr.diff')}</summary><pre className="code ltr" dir="ltr" style={{ whiteSpace: 'pre-wrap', maxHeight: 260, overflow: 'auto' }}>{prev.diff}</pre></details>
        {!done && <>
          {!connected && !isPrivate && <div className="field" style={{ maxWidth: 380 }}><label className="lbl" htmlFor="pr-token">{t('pr.token')}</label>
            <input id="pr-token" className="inp mono ltr" dir="ltr" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
            <p className="hint" style={{ marginTop: 6 }}>{t('pr.token_h')}</p></div>}
          <div><button type="button" className="btn btn-p" disabled={busy || (!connected && !isPrivate && !token.trim())} onClick={() => void open()}>{busy ? t('proj.starting') : t('pr.open')}</button></div>
        </>}
      </>}
      {done && <p className="row" style={{ gap: 8 }}><Icon name="check" /><a className="link" href={done} target="_blank" rel="noreferrer noopener">{t('pr.done')}</a></p>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
