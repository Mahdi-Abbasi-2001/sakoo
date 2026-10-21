import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { GithubButton } from '../components/GithubButton';
import { lookup, useI18n, usePageTitle } from '../i18n';
import { errorText } from '../i18n/errors';
import { useAuth } from '../auth/AuthContext';
import { Icon } from '../components/Icon';

export function AuthPage({ mode }: { mode: 'login' | 'register' }) {
  const { t, lang } = useI18n();
  usePageTitle('title.' + mode);
  const { state, login, register } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [invite, setInvite] = useState(''); const [needInvite, setNeedInvite] = useState(false); const [ghOn, setGhOn] = useState(false);
  const [params] = useSearchParams(); const ghError = params.get('github_error');
  useEffect(() => { api.limits().then((l) => { setNeedInvite(mode === 'register' && !!l.registration_code_required); setGhOn(!!l.github_oauth); }).catch(() => undefined); }, [mode]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const to = useRef('/dashboard');
  if (state.status === 'user') return <Navigate to={to.current} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    to.current = mode === 'register' ? '/settings' : '/dashboard';
    try {
      if (mode === 'login') await login(username.trim(), password); else await register(username.trim(), password, invite.trim());
      setPassword('');                       // the password never outlives the request
      nav(to.current, { replace: true });
    } catch (err) { setError(errorText(lang, err)); setPassword(''); }
    finally { setBusy(false); }
  };

  return (
    <div className="auth"><div className="auth-in">
      <h1 className="title">{t(`auth.${mode}.title`)}</h1>
      <p className="lead" style={{ marginTop: 8 }}>{t(`auth.${mode}.lead`)}</p>
      <form className="card pad stack" style={{ marginTop: 28 }} onSubmit={submit} noValidate>
        <div className="field">
          <label className="lbl" htmlFor="u">{t('auth.username')}</label>
          <input id="u" className="inp mono" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required value={username} onChange={(e) => setUsername(e.target.value)} aria-describedby={mode === 'register' ? 'u-h' : undefined} />
          {mode === 'register' && <span id="u-h" className="hint" style={{ marginTop: 6 }}>{t('auth.username.hint')}</span>}
        </div>
        <div className="field">
          <label className="lbl" htmlFor="p">{t('auth.password')}</label>
          <input id="p" className="inp" type="password" name="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required value={password} onChange={(e) => setPassword(e.target.value)} aria-describedby={mode === 'register' ? 'p-h' : undefined} />
          {mode === 'register' && <span id="p-h" className="hint" style={{ marginTop: 6 }}>{t('auth.password.hint')}</span>}
        </div>
        {mode === 'register' && needInvite && <div className="field"><label className="lbl" htmlFor="inv">{t('auth.invite')}</label>
          <input id="inv" className="inp mono" name="invite" autoComplete="off" spellCheck={false} required value={invite} onChange={(e) => setInvite(e.target.value)} aria-describedby="inv-h" />
          <span id="inv-h" className="hint" style={{ marginTop: 6 }}>{t('auth.invite.hint')}</span></div>}
        {(error || ghError) && <div className="err" role="alert"><Icon name="alert" />{error ?? (lookup(lang, 'err.' + ghError!) !== undefined ? t('err.' + ghError!) : t('err.unknown'))}</div>}
        <button className="btn btn-p btn-block" type="submit" disabled={busy || !username || !password}>
          {busy ? t('auth.working') : t(`auth.submit.${mode}`)}
        </button>
        {ghOn && <>
          <div className="or" style={{ textAlign: 'center' }}><span className="tag">{t('land.or')}</span></div>
          <GithubButton mode="signin" label={t(mode === 'login' ? 'gh.signin' : 'gh.signup')} invite={invite} next="/dashboard" before={() => (mode === 'register' && needInvite && !invite.trim() ? t('auth.invite_first') : null)} />
        </>}
        <Link className="hint" style={{ textAlign: 'center', padding: '12px 0' }} to={mode === 'login' ? '/register' : '/login'}>{t(mode === 'login' ? 'auth.to_register' : 'auth.to_login')}</Link>
      </form>
    </div></div>
  );
}
