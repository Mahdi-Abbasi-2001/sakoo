import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { GithubButton } from '../components/GithubButton';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';

/** Connect a GitHub account: private repositories can then be downloaded without pasting a token, and fixes can be proposed as pull requests. */
export function GithubConnect() {
  const { t, lang } = useI18n();
  const [on, setOn] = useState<boolean | null>(null);
  const [login, setLogin] = useState<string | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.limits().then((l) => setOn(!!l.github_oauth)).catch(() => setOn(false));
    api.settings().then((s) => setLogin(s.github_login ?? null)).catch(() => undefined);
  }, []);
  if (!on) return null;
  const disconnect = async () => { setBusy(true); setErr(null); try { await api.githubDisconnect(); setLogin(null); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  return (
    <section id="s-github" aria-labelledby="h-github" className="card pad stack">
      <h2 id="h-github" style={{ fontSize: 20, lineHeight: '28px' }}>{t('gh.t')}</h2>
      <p className="hint">{t('gh.d')}</p>
      {login ? <div className="row" style={{ gap: 10 }}><Icon name="check" /><span>{t('gh.as', { login })}</span><button type="button" className="btn btn-s btn-d" disabled={busy} onClick={() => void disconnect()}>{t('gh.off')}</button></div>
        : <div><GithubButton mode="connect" label={t('gh.on')} className="btn btn-p" next="/settings?github=connected" /></div>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
