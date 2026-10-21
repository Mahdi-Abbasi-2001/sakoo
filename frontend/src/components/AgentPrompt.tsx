import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Icon } from './Icon';
import { useI18n } from '../i18n';

export const AGENT_IDEAS = ['ag.start.ex.wp', 'ag.start.ex.n8n', 'ag.start.ex.build', 'ag.start.ex.git'];
const WISH = 'lp.wish';   // what a visitor typed on the landing page before signing in, offered again on the dashboard

export function keepWish(text: string) { try { if (text) sessionStorage.setItem(WISH, text); else sessionStorage.removeItem(WISH); } catch { /* storage blocked */ } }
function readWish() { try { return sessionStorage.getItem(WISH) ?? ''; } catch { return ''; } }

/** "Tell the agent what you want to deploy": the main way in on the dashboard. Sending opens the conversation on /agent. */
export function AgentPrompt({ connected }: { connected: boolean }) {
  const { t } = useI18n();
  const nav = useNavigate();
  const [wish, setWish] = useState(readWish);
  const tell = (text: string) => { const q = text.trim(); if (q) { keepWish(''); nav(`/agent?q=${encodeURIComponent(q)}`); } };
  return (
    <section className="card pad stack agent-hero" style={{ gap: 10 }} aria-label={t('dash.agent.t')}>
      <h2 className="sect" style={{ margin: 0 }}>{t('dash.agent.t')}</h2>
      <p className="hint" style={{ margin: 0 }}>{t('dash.agent.d')}</p>
      <form className="agent-compose" onSubmit={(e) => { e.preventDefault(); tell(wish); }}>
        <textarea className="inp" rows={2} maxLength={1000} value={wish} aria-label={t('dash.agent.t')} placeholder={t('ag.start.ph')} onChange={(e) => setWish(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); tell(wish); } }} />
        <button type="submit" className="btn btn-p" disabled={!wish.trim() || !connected}><Icon name="arrow" />{t('dash.agent.go')}</button>
      </form>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{AGENT_IDEAS.map((k) => <button type="button" key={k} className="chip chat-idea" disabled={!connected} onClick={() => tell(t(k))}>{t(k)}</button>)}</div>
    </section>
  );
}
