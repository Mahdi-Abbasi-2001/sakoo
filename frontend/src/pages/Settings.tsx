import { rovingKeys } from '../lib/roving';
import { useI18n, usePageTitle } from '../i18n';
import { useAuth } from '../auth/AuthContext';
import { ConnectFlow } from './ConnectFlow';
import { GithubConnect } from './GithubConnect';
import { AlertSettings } from './AlertSettings';
import { api } from '../api/client';
import { useNavigate } from 'react-router-dom';
import type { Lang } from '../api/types';
import type { Theme } from '../i18n';

function Choice<T extends string>({ value, options, onPick, label }: { value: T; options: { id: T; text: string; lang?: string }[]; onPick: (v: T) => void; label: string }) {
  return (
    <div className="opts" role="radiogroup" aria-label={label} style={{ maxWidth: 360 }} onKeyDown={rovingKeys('radio')}>
      {options.map((o) => (
        <button type="button" role="radio" aria-checked={value === o.id} tabIndex={value === o.id ? 0 : -1} className="opt no-lift" key={o.id} lang={o.lang} onClick={() => onPick(o.id)}><span className="rd" />{o.text}</button>
      ))}
    </div>
  );
}

export function Settings() {
  const { t, lang, theme, setLang, setTheme } = useI18n();
  usePageTitle('settings.title');
  const { me, logout } = useAuth();
  const nav = useNavigate();
  const pickLang = async (l: Lang) => { setLang(l); try { await api.saveSettings({ language: l }); } catch { /* applies locally either way */ } };
  return (
    <div className="page-narrow" style={{ margin: '0 auto' }}>
      <h1 className="title">{t('settings.title')}</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,180px) minmax(0,1fr)', gap: 40, marginTop: 28 }} className="settings-grid">
        <nav aria-label={t('settings.nav')} className="settings-nav stack" style={{ gap: 4, alignSelf: 'start', position: 'sticky', top: 80 }}>
          {['liara', 'language', 'theme', 'account'].map((k) => <a key={k} href={'#s-' + k} className="nav-link">{t('settings.' + k)}</a>)}
        </nav>
        <div className="stack" style={{ gap: 40 }}>
          <section id="s-liara" aria-labelledby="h-liara" className="stack">
            <h2 id="h-liara" style={{ fontSize: 26, lineHeight: '34px' }}>{t('connect.title')}</h2>
            <p className="lead">{t('connect.lead')}</p>
            <ConnectFlow />
          </section>
          <section id="s-language" aria-labelledby="h-lang" className="card pad stack">
            <h2 id="h-lang" style={{ fontSize: 20, lineHeight: '28px' }}>{t('connect.lang.title')}</h2>
            <p className="hint">{t('connect.lang.lead')}</p>
            <Choice<Lang> value={lang} label={t('settings.language')} onPick={(l) => void pickLang(l)} options={[{ id: 'en', text: 'English', lang: 'en' }, { id: 'fa', text: 'فارسی', lang: 'fa' }]} />
          </section>
          <GithubConnect />
          <AlertSettings />
          <section id="s-theme" aria-labelledby="h-theme" className="card pad stack">
            <h2 id="h-theme" style={{ fontSize: 20, lineHeight: '28px' }}>{t('settings.theme')}</h2>
            <Choice<Theme> value={theme} label={t('settings.theme')} onPick={setTheme} options={[{ id: 'dark', text: t('theme.dark') }, { id: 'light', text: t('theme.light') }]} />
          </section>
          <section id="s-account" aria-labelledby="h-acc" className="card pad stack">
            <h2 id="h-acc" style={{ fontSize: 20, lineHeight: '28px' }}>{t('settings.account')}</h2>
            <p>{t('home.signedin', { name: me?.username ?? '' })}</p>
            <div><button type="button" className="btn" onClick={async () => { await logout(); nav('/login', { replace: true }); }}>{t('auth.logout')}</button></div>
          </section>
        </div>
      </div>
    </div>
  );
}
