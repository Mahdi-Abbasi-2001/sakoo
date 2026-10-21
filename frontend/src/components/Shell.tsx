import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { Icon, Mark } from './Icon';
import { Background } from './Background';
import { useI18n } from '../i18n';
import { useAuth } from '../auth/AuthContext';
import { api } from '../api/client';

export function LangToggle() {
  const { lang, setLang, t } = useI18n();
  const { me } = useAuth();
  const pick = async (l: 'en' | 'fa') => {
    setLang(l);
    if (me) { try { await api.saveSettings({ language: l }); } catch { /* the choice still applies locally */ } }
  };
  return (
    <div className="seg" role="group" aria-label={t('lang.label')}>
      <button type="button" aria-pressed={lang === 'en'} lang="en" aria-label={t('lang.en')} onClick={() => void pick('en')}>EN</button>
      <button type="button" aria-pressed={lang === 'fa'} lang="fa" aria-label={t('lang.fa')} onClick={() => void pick('fa')}>فا</button>
    </div>
  );
}

export function ThemeToggle() {
  const { theme, setTheme, t } = useI18n();
  return (
    <button type="button" className="icon-btn" aria-label={t('theme.toggle')} onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
  );
}

export function Shell({ bare = false }: { bare?: boolean }) {
  const { t } = useI18n();
  const { me, logout } = useAuth();
  const nav = useNavigate();
  return (
    <div className="app">
      <Background />
      <a className="skip" href="#main">{t('nav.skip')}</a>
      <header className="shell">
        <Link className="brand" to="/"><Mark /><span>{t('app.name')}</span></Link>
        <span style={{ flex: 1 }} />
        {me && (
          <Link className={'badge ' + (me.has_liara_token ? 's-ok' : 's-warn')} to="/settings" title={t('nav.settings')}>
            <Icon name={me.has_liara_token ? 'check' : 'warn'} />{t(me.has_liara_token ? 'status.connected' : 'status.not_connected')}
          </Link>
        )}
        {me && !bare && <NavLink to="/settings" className="icon-btn" aria-label={t('nav.settings')} title={t('nav.settings')}><Icon name="sliders" /></NavLink>}
        <LangToggle />
        <ThemeToggle />
        {me && !bare && (
          <button type="button" className="btn" onClick={async () => { await logout(); nav('/login', { replace: true }); }}>{t('auth.logout')}</button>
        )}
      </header>
      <main id="main" tabIndex={-1} className="page enter"><Outlet /></main>
    </div>
  );
}
