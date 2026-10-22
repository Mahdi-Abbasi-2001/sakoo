import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../auth/AuthContext';
import { Icon, Mark } from '../components/Icon';
import { AGENT_IDEAS, keepWish } from '../components/AgentPrompt';
import { LangToggle, ThemeToggle } from '../components/Shell';
import { useI18n, usePageTitle } from '../i18n';
import '../styles/landing.gen.css';
import '../styles/landing.css';
import { mountLanding } from './engine';
import { LaunchPad } from './LaunchPad';
import { CreatesDiagram } from './CreatesDiagram';
import { SCENES, STATE_ICON } from './scenes.gen';

const STATES = ['reading', 'thinking', 'waiting', 'working', 'done', 'failed'] as const;
const WHY = [
  { k: 'yes', icon: <path d="M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z M9 12l2 2 4-4" /> },
  { k: 'price', icon: <><path d="M3 12 12 3h8v8l-9 9z" /><circle cx="15.5" cy="8.5" r="1.2" /></> },
  { k: 'fix', icon: <path d="M14.5 6.5a4 4 0 0 0-5 5L4 17l3 3 5.5-5.5a4 4 0 0 0 5-5L15 12l-3-3z" /> },
  { k: 'own', icon: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></> },
];

const MCP = [
  { icon: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4.5 4.5" /></> },
  { icon: <><path d="m8 8-4 4 4 4M16 8l4 4-4 4M13.5 6l-3 12" /></> },
  { icon: <><path d="M20 12a8 8 0 1 1-2.6-5.9" /><path d="M20 4v5h-5" /></> },
];

const Html = ({ html, className }: { html: string; className?: string }) => <span className={className} style={{ display: 'contents' }} dangerouslySetInnerHTML={{ __html: html }} />;


/** One blueprint sheet. Its inside is SVG drawn by the engine; the words come from the catalog. */
function Sheet({ mode, header }: { mode: 'loop' | 'scrub'; header?: boolean }) {
  const { t, lang } = useI18n();
  const rtl = lang === 'fa' ? '1' : '0';
  const scene = SCENES[lang];
  const fill = (html: string) => html.replace(/\{\{w\.([a-z_]+)\}\}/g, (_, k) => t('land.w.' + k));
  const wide = mode === 'loop' ? scene.heroWide : scene.scrubWide, tall = mode === 'loop' ? scene.heroTall : scene.scrubTall;
  const stage = useMemo(() => fill(wide) + fill(tall), [lang]);   // eslint-disable-line react-hooks/exhaustive-deps
  const words = Object.fromEntries(STATES.map((s) => [`data-${s}`, t('land.state.' + s)]));
  const apprs = Object.fromEntries(STATES.filter((s) => s !== 'failed').map((s) => [`data-${s}`, t('land.appr.' + s)]));
  return (
    <div className="sheet" role="group" aria-label={t('land.sheet.title')} data-mode={mode} data-rtl={rtl}>
      {header && <div className="sheet-h"><span className="tag">{t('land.sheet.title')}</span><span className="state-pill"><Html html={STATE_ICON} /><span className="state-word" {...words}>{t('land.state.reading')}</span></span></div>}
      <div className="readout"><div><div className="lp-big big-h" aria-hidden="true">{lang === 'fa' ? '۰' : '0'}</div><div className="unit">{t('land.unit_h')}</div></div><div className="lp-m"><b className="big-m" aria-hidden="true">{lang === 'fa' ? '۰' : '0'}</b>{t('land.per_month')}</div></div>
      <div className="stage"><Html html={stage} /></div>
      <div className="prog"><div className="prog-bar" /></div>
      <div className="lp-appr" data-state="reading"><p className="appr-txt" {...apprs}>{t('land.appr.reading')}</p><div className="lp-acts"><button type="button" className="lp-btn lp-btn-s">{t('land.decline')}</button><button type="button" className="lp-btn lp-btn-p">{t('land.apply')}</button></div></div>
      <div className="ctl">
        <button type="button" className="lp-chip pause" aria-pressed="false" data-pause={t('land.pause')} data-play={t('land.play')}>{t('land.pause')}</button>
        {[0, 1, 2, 3, 4].map((i) => <button type="button" key={i} className="lp-chip" data-frame={i}>{t('land.frame.' + i)}</button>)}
        <span className="clock" aria-hidden="true">00.0 s</span>
      </div>
    </div>
  );
}

function Section({ id, children, aside }: { id?: string; n?: string; children: ReactNode; aside: ReactNode }) {
  return <section className="sec" id={id}><div className="wrap sec-grid"><div>{aside}</div>{children}</div></section>;
}

export default function Landing() {
  const { t, lang } = useI18n();
  const { state } = useAuth();
  usePageTitle('land.tab');
  const root = useRef<HTMLDivElement>(null);
  const [reduce, setReduce] = useState(() => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  const nav = useNavigate();
  const [wish, setWish] = useState('');
  const setupKey = lang + (reduce ? 'r' : 'm');

  useEffect(() => {
    const mq = window.matchMedia?.('(prefers-reduced-motion: reduce)'); if (!mq) return;
    const on = () => setReduce(mq.matches); mq.addEventListener('change', on); return () => mq.removeEventListener('change', on);
  }, []);
  useEffect(() => {
    // the replay needs the web fonts' metrics settled only for the scene text; it never waits for them to start
    if (!root.current) return;
    const api2 = mountLanding(root.current, lang, reduce);
    return () => api2.stop();
  }, [setupKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  const authed = state.status === 'user';
  const nf = (n: number) => new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US').format(n);
  const cta = authed ? { to: '/new', text: t('land.cta.authed') } : { to: '/register', text: t('land.cta.anon') };
  // a visitor's words wait in the browser while they sign up, and are offered again on the dashboard
  const tell = () => { const q = wish.trim(); if (!q) return; if (authed) nav(`/agent?q=${encodeURIComponent(q)}`); else { keepWish(q); nav('/register'); } };

  return (
    <div className="lp" lang={lang} dir={lang === 'fa' ? 'rtl' : 'ltr'} ref={root} data-live="1">
      <div className="lpbg" aria-hidden="true"><i className="g1" /><i className="g2" /><i className="vg" /><i className="gn" /><i className="gm" /></div>
      <a className="skip" href="#top">{t('nav.skip')}</a>
      <header className="wrap bar">
        <a className="lp-brand" href="#top"><Mark /><span>{t('app.name')}</span></a>
        <nav className="lp-nav" aria-label={t('land.nav.label')}><a href="#how">{t('land.nav.how')}</a><a href="#creates">{t('land.nav.creates')}</a><a href="#why">{t('land.nav.safety')}</a><a href="#agent">{t('land.nav.agent')}</a></nav>
        <span className="lp-sp" />
        {authed
          ? <Link className="acct" to="/dashboard"><span className="at">{t('nav.dashboard')}</span></Link>
          : <><Link className="acct wide-only" to="/login"><span>{t('land.login_short')}</span></Link><Link className="btn reg" to="/register"><span className="wide-only">{t('land.register_short')}</span><span className="narrow-only">{t('land.register_tiny')}</span></Link></>}
        <span className="wide-only bar-ctl"><LangToggle /><ThemeToggle /></span>
        <BarMenu authed={authed} />
      </header>
      <main id="top" tabIndex={-1}>
        <div className="hero"><div className="wrap hero-grid">
          <div className="tool-col">
            <div className="eyebrow"><i /><span className="tag">{t('land.eyebrow')}</span></div>
            <h1>{t('land.h1')}</h1><p className="lp-lead">{t('land.lead')}</p>
            <div className="tool" role="group" aria-label={t('land.h1')}>
              <form className="ask" onSubmit={(e) => { e.preventDefault(); tell(); }}>
                <label htmlFor="ask-p">{t('land.ask.label')}</label>
                <textarea id="ask-p" rows={3} maxLength={1000} value={wish} placeholder={t('land.ask.ph')} onChange={(e) => setWish(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); tell(); } }} />
                <div className="ask-ideas">{AGENT_IDEAS.map((k) => <button type="button" key={k} className="lp-chip" onClick={() => setWish(t(k))}>{t(k)}</button>)}</div>
                <button type="submit" className="cta" disabled={!wish.trim()}><span className="cta-t">{t(authed ? 'land.ask.go' : 'land.ask.go.anon')}</span><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg></button>
              </form>
              <p className="toolnote"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /></svg><span>{t(authed ? 'land.toolnote.authed' : 'land.toolnote.anon')}</span></p>
              <p className="toolnote"><Link className="lp-link" to={cta.to}>{t('land.ask.manual')}</Link></p>
              {!authed && <p className="toolnote"><Link className="lp-link" to="/login">{t('land.login')}</Link></p>}
            </div>
          </div>
          <div className="sheet-col"><LaunchPad reduce={reduce} /></div>
        </div>
      </div>

        <section className="sec story" id="how"><div className="wrap">
          <div className="sec-grid" style={{ marginBottom: 40 }}><div><span className="tag">{t('land.s1.tag')}</span><h2>{t('land.s1.h')}</h2><p>{t('land.s1.p')}</p></div><div /></div>
          <div className="story-grid"><div className="sheet-wrap"><Sheet mode="scrub" /></div>
            <ol className="lp-steps">{[0, 1, 2, 3].map((i) => (
              <li key={i} data-step={i}><span className="tag">{t('land.step', { n: nf(i + 1) })}</span><h3>{t(`land.step.${i}.h`)}</h3><p>{t(`land.step.${i}.p`)}</p>
                <div className={'ex' + (lang === 'fa' && (i === 1 || i === 2) ? ' ex-fa' : '')}>{['docker-compose.yml · service web', t('land.step.1.ex'), t('land.step.2.ex'), 'http_200 · blog-web.liara.run'][i]}</div></li>))}
            </ol></div>
        </div></section>

        <Section id="creates" n="02" aside={<><span className="tag">{t('land.s2.tag')}</span><h2>{t('land.s2.h')}</h2><p>{t('land.s2.p')}</p></>}>
          <CreatesDiagram reduce={reduce} />
        </Section>

        <section className="sec why" id="why"><div className="wrap">
          <div className="why-head"><span className="tag">{t('land.s3.tag')}</span><h2>{t('land.s3.h')}</h2><p>{t('land.s3.p')}</p></div>
          <ul className="why-grid">{WHY.map((w) => (
            <li className="why-card" key={w.k}><span className="why-ic" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">{w.icon}</svg></span>
              <h3>{t(`land.why.${w.k}.h`)}</h3><p>{t(`land.why.${w.k}.p`)}</p></li>))}
          </ul>
        </div></section>

        <section className="sec why" id="agent"><div className="wrap">
          <div className="why-head"><span className="tag">{t('land.s4.tag')}</span><h2>{t('land.s4.h')}</h2><p>{t('land.s4.p')}</p></div>
          <ul className="why-grid why-three">{MCP.map((w, i) => (
            <li className="why-card" key={i}><span className="why-ic" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">{w.icon}</svg></span>
              <h3>{t(`land.mcp.${i}.h`)}</h3><p>{t(`land.mcp.${i}.p`)}</p></li>))}
          </ul>
          <p className="mcp-note"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--ac)" strokeWidth="2" strokeLinejoin="round" aria-hidden="true"><path d="M12 3 20 6v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /></svg><span>{t('land.mcp.note')}</span></p>
          <Link className="lp-link" to={authed ? '/settings' : '/register'}>{t('land.mcp.cta')}</Link>
        </div></section>

        <section className="sec go"><div className="wrap go-in">
          <h2>{t('land.go.h')}</h2><p>{t('land.go.p')}</p>
          <Link className="cta go-cta" to={cta.to}><span className="cta-t">{cta.text}</span><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" /></svg></Link>
        </div></section>
      </main>
      <footer className="ft"><div className="wrap ft-row"><span>{t('app.name')}</span><span className="ft-links"><Link to="/login">{t('land.login_short')}</Link><Link to="/register">{t('land.register_short')}</Link></span><LangToggle /></div></footer>
    </div>
  );
}

// on a phone the header keeps the brand and one call to action; the sections, sign in, language and theme move into this menu
function BarMenu({ authed }: { authed: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: PointerEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); btn.current?.focus(); } };
    document.addEventListener('pointerdown', away); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', away); document.removeEventListener('keydown', esc); };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div className="lp-menu narrow-only" ref={box}>
      <button type="button" ref={btn} className="icon-btn" aria-expanded={open} aria-controls="lp-menu-panel" aria-label={t('land.menu')} onClick={() => setOpen(o => !o)}>
        <Icon name={open ? 'x' : 'menu'} />
      </button>
      {open && (
        <div className="lp-menu-panel" id="lp-menu-panel">
          <nav aria-label={t('land.nav.label')}>
            <a href="#how" onClick={close}>{t('land.nav.how')}</a>
            <a href="#creates" onClick={close}>{t('land.nav.creates')}</a>
            <a href="#why" onClick={close}>{t('land.nav.safety')}</a>
            <a href="#agent" onClick={close}>{t('land.nav.agent')}</a>
          </nav>
          {!authed && <Link className="lp-menu-login" to="/login">{t('land.login_short')}</Link>}
          <div className="lp-menu-ctl"><LangToggle /><ThemeToggle /></div>
        </div>
      )}
    </div>
  );
}
