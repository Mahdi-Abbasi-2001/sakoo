import { useEffect, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { ChatComposer, ChatMessages, useChatThread } from '../run/parts/AgentChat';

const EXAMPLES = ['ag.start.ex.wp', 'ag.start.ex.n8n', 'ag.start.ex.build', 'ag.start.ex.git'];

/** "Tell the agent what you want to put online": a conversation instead of a form. The agent finds a ready-made app, takes a GitHub project, or builds what is described,
 *  and the person approves each step on a card; a deployment then continues on its own run page. */
export function AgentStart() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const th = useChatThread(0, null, true);
  const started = useRef(false);
  const q = params.get('q');
  useEffect(() => {
    // The general conversation (outside any project) starts fresh on every visit to this page, with or without a request typed in the prompt box: the agent
    // remembers the whole saved conversation, so an unrelated earlier request would colour its answers. Only this chat (project 0) is cleared here: a project's
    // own conversation, on its run page, is never touched.
    if (started.current || !th.chat) return;
    started.current = true; if (q) setParams({}, { replace: true });
    if (th.chat.available === false) return;
    void (async () => { if (th.chat!.messages.length > 0) await th.clear(); if (q) await th.send(q); })();
  }, [q, th.chat]);   // eslint-disable-line react-hooks/exhaustive-deps
  const off = th.chat?.available === false;
  return (
    <div className="agent-start page-narrow" style={{ margin: '0 auto', maxWidth: 860 }}>
      <div className="page-h"><div><h1 className="title">{t('ag.start.t')}</h1><p className="lead">{t('ag.start.lead')}</p></div></div>
      <section className="card stack agent-start-card" style={{ gap: 0 }} aria-label={t('ag.start.t')}>
        <ChatMessages th={th} title={t('ag.start.chat')} empty={
          <div className="stack" style={{ gap: 10 }}>
            <p className="hint">{t('ag.start.empty')}</p>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{EXAMPLES.map((k) => <button type="button" key={k} className="chip chat-idea" disabled={th.busy} onClick={() => void th.send(t(k))}>{t(k)}</button>)}</div>
          </div>} />
        <div style={{ padding: '0 16px 16px' }}><ChatComposer th={th} placeholder={t('ag.start.ph')} /></div>
      </section>
      {off && <p className="hint" style={{ marginTop: 12 }}><Icon name="warn" /> {t('ag.start.off')} <Link className="link" to="/new">{t('ag.start.manual')}</Link></p>}
      <p className="hint" style={{ marginTop: 12 }}>{t('ag.start.note')}</p>
    </div>
  );
}
