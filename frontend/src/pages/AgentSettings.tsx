import { useCallback, useEffect, useState } from 'react';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { when } from '../lib/time';

/** The person's own coding agent (Claude Code, Cursor...) and how much it, and Sakoo, may do about errors in the code.
 *  A token is shown once, when it is made. It can read this person's projects and runs and send code fixes; it cannot delete anything or spend money. */
export function AgentSettings() {
  const { t, lang } = useI18n();
  const [list, setList] = useState<G.AgentTokensOut | null>(null);
  const [auto, setAuto] = useState(false);
  const [name, setName] = useState('');
  const [fresh, setFresh] = useState<G.AgentTokenCreatedOut | null>(null);
  const [copied, setCopied] = useState('');
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { api.agentTokens().then(setList).catch((e) => setErr(errorText(lang, e))); }, [lang]);
  useEffect(() => { load(); api.settings().then((s) => setAuto(!!s.auto_fix_code)).catch(() => undefined); }, [load]);
  const run = async (f: () => Promise<void>) => { setBusy(true); setErr(null); try { await f(); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); } };
  const copy = async (key: string, text: string) => { try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(''), 1500); } catch { /* blocked */ } };
  const url = list?.mcp_url ?? '';
  const command = fresh ? `claude mcp add --transport http sakoo ${url} --header "Authorization: Bearer ${fresh.token}"` : '';
  return (
    <section id="s-agent" aria-labelledby="h-agent" className="card pad stack">
      <h2 id="h-agent" style={{ fontSize: 20, lineHeight: '28px' }}>{t('agt.t')}</h2>
      <p className="hint">{t('agt.d')}</p>

      <label className="row agt-auto" style={{ gap: 10, alignItems: 'flex-start', flexWrap: 'nowrap' }}>
        <input type="checkbox" checked={auto} disabled={busy} onChange={(e) => { const v = e.target.checked; void run(async () => { setAuto((await api.saveSettings({ auto_fix_code: v })).auto_fix_code ?? v); }); }} />
        <span><b>{t('agt.auto')}</b><br /><span className="hint">{t('agt.auto_d')}</span></span>
      </label>

      <div className="stack" style={{ gap: 8 }}>
        <b>{t('agt.tokens')}</b>
        {url && <p className="hint">{t('agt.url')} <span className="code ltr" dir="ltr">{url}</span> <button type="button" className="btn btn-s" onClick={() => void copy('url', url)}>{copied === 'url' ? t('log.copied') : t('live.copy')}</button></p>}
        {list && list.tokens.length === 0 && <p className="hint">{t('agt.none')}</p>}
        {list?.tokens.map((k) => (
          <div className="rw" key={k.id}><span><b>{k.name}</b> <span className="hint">{t('agt.made', { when: when(lang, k.created_at) })}{k.last_used_at ? ' · ' + t('agt.used', { when: when(lang, k.last_used_at) }) : ''}</span></span>
            <button type="button" className="btn btn-s btn-d" disabled={busy} onClick={() => void run(async () => { await api.revokeAgentToken(k.id); load(); })}>{t('agt.revoke')}</button></div>
        ))}
        <form className="row" style={{ gap: 10, alignItems: 'flex-end' }} onSubmit={(e) => { e.preventDefault(); void run(async () => { setFresh(await api.createAgentToken(name.trim() || t('ho.token_name'))); setName(''); load(); }); }}>
          <div className="field" style={{ flex: 1, minWidth: 220 }}><label className="lbl" htmlFor="agt-name">{t('agt.name')}</label>
            <input id="agt-name" className="inp" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder={t('ho.token_name')} autoComplete="off" /></div>
          <button type="submit" className="btn btn-p" disabled={busy}>{t('agt.make')}</button>
        </form>
      </div>

      {fresh && (
        <div className="stack ho-snip" style={{ gap: 8 }}>
          <p className="row" style={{ gap: 6 }}><Icon name="check" />{t('ho.token_once')}</p>
          <div className="row" style={{ justifyContent: 'space-between' }}><span className="hint">{t('ho.claude')}</span><button type="button" className="btn btn-s" onClick={() => void copy('cmd', command)}>{copied === 'cmd' ? t('log.copied') : t('live.copy')}</button></div>
          <pre className="term ltr" dir="ltr" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{command}</pre>
          <button type="button" className="btn btn-s" style={{ alignSelf: 'flex-start' }} onClick={() => setFresh(null)}>{t('agt.hide')}</button>
        </div>
      )}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
