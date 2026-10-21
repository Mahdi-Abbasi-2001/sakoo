import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, type ChatEvent } from '../../api/client';
import type * as G from '../../api/schema.gen';
import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { errorText } from '../../i18n/errors';
import { num } from '../format';

type Params = Record<string, string | number>;
const asParams = (p: Record<string, unknown>): Params => Object.fromEntries(Object.entries(p).map(([k, v]) => [k, typeof v === 'number' ? v : String(v ?? '')]));

/** One proposed change. It runs only when the person presses the button here; a secret is typed in this box and goes to the server, never to the model. */
export function ActionCard({ a, busy, decide: decideAction }: { a: G.ChatActionOut; busy: boolean; decide: (id: string, b: { decision: 'approve' | 'decline'; value?: string; confirm_name?: string }) => Promise<void> }) {
  const { t, lang } = useI18n();
  const [value, setValue] = useState(''); const [typed, setTyped] = useState('');
  const pending = a.status === 'pending';
  const decide = async (decision: 'approve' | 'decline') => {
    await decideAction(a.id, { decision, ...(a.needs_value ? { value } : {}), ...(a.confirm_name ? { confirm_name: typed } : {}) });
    setValue(''); setTyped('');
  };
  const p = asParams(a.params);
  const ready = (!a.needs_value || value.length > 0) && (!a.confirm_name || typed.trim().length > 0);
  return (
    <div className={'chat-card' + (a.confirm_name ? ' danger' : '')} role="group" aria-label={t('chat.card.label')}>
      <div className="row" style={{ gap: 8, flexWrap: 'nowrap', alignItems: 'flex-start' }}>
        <Icon name={a.confirm_name ? 'alert' : 'diamond'} className="ic" />
        <div className="stack" style={{ gap: 6, flex: 1, minWidth: 0 }}>
          <b>{t(`chat.card.${a.code}`, p)}</b>
          {a.warnings.map((w, i) => <p className="hint" key={i}>{t(`chat.warn.${w.code}`, asParams(w.params))}</p>)}
          {a.files && a.files.length > 0 && <ul className="chat-files">{a.files.map((f) => <li key={String(f.path)} className="mono ltr" dir="ltr"><span className={'chat-fs ' + String(f.status)}>{t(`chat.file.${String(f.status)}`)}</span> {String(f.path)} <i>+{String(f.added)} −{String(f.removed)}</i></li>)}</ul>}
          {a.diff && <details className="chat-diff"><summary>{t('chat.diff')}</summary><pre className="mono ltr" dir="ltr">{a.diff}</pre></details>}
          {a.price_delta_hourly != null && a.price_delta_hourly !== 0 && <p className={a.price_delta_hourly > 0 ? 'chat-price up' : 'chat-price'}>{t(a.price_delta_hourly > 0 ? 'chat.price.up' : 'chat.price.down', { n: num(lang, Math.abs(Math.round(a.price_delta_hourly * 720))) })}</p>}
        </div>
      </div>
      {pending && <>
        {a.needs_value && <div className="field"><label className="lbl" htmlFor={`v-${a.id}`}>{t('chat.value', { name: a.value_label ?? '' })}</label>
          <input id={`v-${a.id}`} className="inp mono ltr" dir="ltr" type="password" autoComplete="new-password" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)} />
          <p className="hint">{t('chat.value.note')}</p></div>}
        {a.confirm_name && <div className="field"><label className="lbl" htmlFor={`c-${a.id}`}>{t('chat.confirm', { name: String(a.params.project ?? '') })}</label>
          <input id={`c-${a.id}`} className="inp mono ltr" dir="ltr" autoComplete="off" spellCheck={false} value={typed} onChange={(e) => setTyped(e.target.value)} /></div>}
        <div className="row" style={{ gap: 10 }}>
          <button type="button" className={'btn btn-s ' + (a.confirm_name ? 'btn-d' : 'btn-p')} disabled={busy || !ready} onClick={() => void decide('approve')}>{t(a.confirm_name ? 'chat.do.danger' : 'chat.do')}</button>
          <button type="button" className="btn btn-s" disabled={busy} onClick={() => void decide('decline')}>{t('chat.no')}</button>
        </div>
      </>}
      {!pending && <div className="row" style={{ gap: 8 }}>
        <span className={'badge ' + (a.status === 'done' ? 's-ok' : a.status === 'failed' ? 's-fail' : '')}><Icon name={a.status === 'done' ? 'check' : a.status === 'failed' ? 'x' : 'dashed'} />{t(`chat.st.${a.status}`)}</span>
        {a.status === 'failed' && a.error && <span className="hint">{a.error}</span>}
        {a.status === 'done' && a.link && <Link className="btn btn-s" to={a.link}>{t('chat.open')}</Link>}
      </div>}
    </div>
  );
}

const IDEAS = ['chat.idea.diagnose', 'chat.idea.status', 'chat.idea.cost', 'chat.idea.versions'];

/** The conversation with the agent about one project (projectId 0: none, nothing is loaded). The agent's answers stream in as they are written. */
/** `since` (Unix seconds): show only what was said from then on, so a run's page does not carry an earlier run's talk (the agent still remembers it). */
export function useChatThread(projectId: number, since?: number | null, general = false) {   // general: the person's conversation about anything (project 0), on the page where they say what they want
  const { lang } = useI18n();
  const nav = useNavigate();
  const [chat, setChat] = useState<G.ChatOut | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState(false);
  const [live, setLive] = useState<{ text: string; looked: string[] }[] | null>(null);   // the answer being written right now
  const load = useCallback(async () => { if (!projectId && !general) return; try { setChat(await api.chat(projectId)); } catch (e) { setErr(errorText(lang, e)); } }, [projectId, general, lang]);
  useEffect(() => { void load(); }, [load]);
  const onEvent = (e: ChatEvent) => setLive((l) => {
    const parts = [...(l ?? [])];
    if (e.type === 'step' || parts.length === 0) parts.push({ text: '', looked: [] });
    const last = { ...parts[parts.length - 1] };
    if (e.type === 'delta') last.text += e.text;
    if (e.type === 'tool') last.looked = [...last.looked, e.name];
    parts[parts.length - 1] = last;
    return parts;
  });
  /** One step of the conversation: the answer shows up as it is written, then the saved conversation replaces it. */
  const run = async (step: () => Promise<G.ChatOut>, decided?: string) => {
    setBusy(true); setErr(null); setNote(false); setLive([]);
    try {
      const c = await step(); setChat(c); setNote(!!c.hidden_secret);
      const done = decided ? c.messages.flatMap((m) => m.actions ?? []).find((a) => a.id === decided) : undefined;   // a change that started a run (a deployment, a release): take the person to its page to watch it
      if (done?.status === 'done' && done.link?.startsWith('/runs/')) nav(done.link);
    }
    catch (e) { setErr(errorText(lang, e)); await load(); } finally { setBusy(false); setLive(null); }
  };
  const send = async (msg: string) => {
    const m = msg.trim(); if (!m || busy || (!projectId && !general)) return;
    setText('');
    setChat((c) => ({ ...(c ?? { messages: [], pending: null }), messages: [...(c?.messages ?? []), { id: -1, role: 'user', text: m }] }));
    await run(() => api.chatSendLive(projectId, m, onEvent));
  };
  const decide = (id: string, b: { decision: 'approve' | 'decline'; value?: string; confirm_name?: string }) => run(() => api.chatDecideLive(projectId, id, b, onEvent), id);
  const clear = async () => { setBusy(true); try { await api.chatClear(projectId); setChat({ messages: [], pending: null, available: chat?.available }); setNote(false); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  // only this visit's messages, except a change still waiting for a decision from before: it blocks sending, so the conversation is shown from the
  // request that led to it. The starting point is fixed the first time the conversation loads, so deciding on that change does not hide it again.
  const from = useRef<number | null>(null);
  if (since && chat && from.current === null) {
    const all = chat.messages, i = all.findIndex((m) => (m.actions ?? []).some((x) => x.status === 'pending'));
    const start = i < 0 ? null : all[i - 1]?.role === 'user' ? all[i - 1] : all[i];
    from.current = Math.min(since - 2, start?.at ?? Infinity);
  }
  const shown = chat && since ? { ...chat, messages: chat.messages.filter((m) => m.at == null || m.at >= (from.current ?? since - 2)) } : chat;
  return { projectId, chat: shown, text, setText, busy, err, note, live, send, decide, clear };
}
export type ChatThread = ReturnType<typeof useChatThread>;

/** The messages, the agent's proposed changes (cards) and the answer being written, inside the agent's own scrolling area. */
export function ChatMessages({ th, empty, title }: { th: ChatThread; empty?: React.ReactNode; title?: string }) {
  const { t } = useI18n();
  const { chat, busy, live, note, err } = th;
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [chat?.messages.length, busy, live?.length, live?.at(-1)?.text.length]);
  const msgs = chat?.messages ?? [];
  return (
    <div className="agent-chat" role="log" aria-live="polite" aria-relevant="additions">
      <div className="agent-chat-h"><b>{title ?? t('ag.talk')}</b>{msgs.length > 0 && <button type="button" className="link" disabled={busy} onClick={() => void th.clear()}>{t('chat.clear')}</button>}</div>
      {chat?.available === false && <p className="hint"><Icon name="warn" /> {t('chat.off')}</p>}
      {msgs.length === 0 && chat && chat.available !== false && empty}
      {msgs.length === 0 && chat && chat.available !== false && !empty && <div className="stack" style={{ gap: 8 }}><p className="hint">{t('chat.empty.project')}</p>
        <div className="row" style={{ gap: 8 }}>{IDEAS.map((k) => <button type="button" key={k} className="chip chat-idea" disabled={busy} onClick={() => void th.send(t(k))}>{t(k)}</button>)}</div></div>}
      {msgs.map((m, i) => (
        <div key={`${m.id}-${i}`} className={'chat-msg ' + m.role}>
          {m.role === 'assistant' && (m.looked?.length ?? 0) > 0 && <div className="chat-looked">{m.looked!.map((k, j) => <span className="chip" key={j}><Icon name="file" />{t(`chat.tool.${k}`)}</span>)}</div>}
          {m.text && <p className="chat-text">{m.text}</p>}
          {(m.actions ?? []).map((a) => <ActionCard key={a.id} a={a} busy={busy} decide={th.decide} />)}
        </div>
      ))}
      {(live ?? []).map((p, i) => (
        <div key={`live-${i}`} className="chat-msg assistant">
          {p.looked.length > 0 && <div className="chat-looked">{p.looked.map((k, j) => <span className="chip" key={j}><Icon name="file" />{t(`chat.tool.${k}`)}</span>)}</div>}
          {p.text && <p className="chat-text">{p.text}</p>}
        </div>
      ))}
      {busy && !(live && live.some((p) => p.text)) && <div className="chat-msg assistant"><p className="hint chat-think"><Icon name="loader" />{t('chat.thinking')}</p></div>}
      {note && <p className="hint" role="status"><Icon name="shield" /> {t('chat.hidden')}</p>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      <div ref={end} />
    </div>
  );
}

/** Where the person writes to the agent: always at the bottom of the agent's panel. */
export function ChatComposer({ th, placeholder }: { th: ChatThread; placeholder?: string }) {
  const { t } = useI18n();
  const off = th.chat?.available === false;
  const waiting = !!th.chat?.pending;
  return (
    <form className="agent-compose" onSubmit={(e) => { e.preventDefault(); void th.send(th.text); }}>
      <textarea className="inp" rows={2} maxLength={4000} value={th.text} disabled={th.busy || waiting || off} aria-label={t('chat.ph.label')}
        placeholder={off ? t('chat.off') : waiting ? t('chat.decide_first') : placeholder ?? t('chat.ph.project')}
        onChange={(e) => th.setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void th.send(th.text); } }} />
      <button type="submit" className="btn btn-p" disabled={th.busy || waiting || off || !th.text.trim()}><Icon name="arrow" />{t('chat.send')}</button>
    </form>
  );
}
