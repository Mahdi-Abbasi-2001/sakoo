import { useEffect, useState } from 'react';
import { api, serverNow } from '../../api/client';
import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { errorText } from '../../i18n/errors';
import { clock } from '../format';
import { useSay } from '../wording';
import type { HandoffView } from '../types';

const PLACEHOLDER = '<your-token>';
/** What to tell the agent. It is written for a machine, so it stays in English whatever language the page is in. */
const agentPrompt = (run: string) => `Sakoo (Liara Launchpad) could not deploy my project because of an error in my code. Use the "sakoo" MCP tools: call get_failure for run ${run}, fix the code in this repository, then commit and push and call redeploy (or call submit_fix with the changed files if the project is not on Git), and call wait_for_run until the app is healthy.`;

/** The run waits for the person's own coding agent. This card is the whole hand-over: connect the agent (one command), see what it does as it does it,
 *  or stop waiting and let Sakoo fix the code. The token is shown once, here, and lives only in this component. */
export function AgentHandoff({ h, runId, demo }: { h: HandoffView; runId: string; demo?: boolean }) {
  const { t, lang } = useI18n();
  const say = useSay();
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [copied, setCopied] = useState('');
  const [, tick] = useState(0);
  useEffect(() => { const i = setInterval(() => tick((n) => n + 1), 1000); return () => clearInterval(i); }, []);
  const left = h.expiresAt != null ? Math.max(0, h.expiresAt - serverNow() / 1000) : null;
  const tok = token ?? PLACEHOLDER;
  const claude = `claude mcp add --transport http sakoo ${h.mcpUrl} --header "Authorization: Bearer ${tok}"`;
  const json = JSON.stringify({ mcpServers: { sakoo: { url: h.mcpUrl, headers: { Authorization: `Bearer ${tok}` } } } }, null, 2);
  const prompt = agentPrompt(h.runId || runId);
  const copy = async (key: string, text: string) => { try { await navigator.clipboard.writeText(text); setCopied(key); setTimeout(() => setCopied(''), 1500); } catch { /* blocked */ } };
  const act = async (tag: string, f: () => Promise<void>) => { setBusy(tag); setErr(null); try { await f(); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(null); } };
  const loc = (h.diagnosis.params as { location?: string }).location;
  return (
    <section className="ap appr ho" role="region" aria-labelledby="ho-title">
      <div className="ap-head">
        <span className="badge s-wait"><Icon name="plug" />{t('ho.waiting')}</span>
        {left !== null && <span className="mono ap-exp">{t('ap.expires', { t: clock(lang, left) })}</span>}
      </div>
      <h2 className="ap-title" id="ho-title">{t('ho.title')}</h2>
      <p className="ap-text">{say('diagnosis', h.diagnosis.code, h.diagnosis.params, h.diagnosis.summary)}{loc ? ' ' : ''}{loc && <span className="chip"><Icon name="file" /><b className="mono ltr" dir="ltr">{loc}</b></span>}</p>

      <div className="ho-step">
        <b>{t('ho.s1')}</b>
        {token === null
          ? <div><button type="button" className="btn btn-p no-lift" disabled={!!busy || demo} onClick={() => void act('token', async () => setToken((await api.createAgentToken(t('ho.token_name'))).token))}>{busy === 'token' ? t('ap.working') : t('ho.make_token')}</button> <span className="hint">{t('ho.token_hint')}</span></div>
          : <p className="hint row" style={{ gap: 6 }}><Icon name="check" />{t('ho.token_once')}</p>}
        <Snippet label={t('ho.claude')} text={claude} id="claude" copied={copied} onCopy={copy} />
        <details><summary>{t('ho.other')}</summary><Snippet label={t('ho.json')} text={json} id="json" copied={copied} onCopy={copy} /></details>
      </div>
      <div className="ho-step">
        <b>{t('ho.s2')}</b>
        <Snippet label={t('ho.prompt')} text={prompt} id="prompt" copied={copied} onCopy={copy} />
      </div>

      <div className="ho-calls" aria-live="polite">
        <b>{t('ho.s3')}</b>
        {h.calls.length === 0
          ? <p className="hint row" style={{ gap: 6 }}><Icon name="loader" />{t('ho.nothing_yet')}</p>
          : <ul className="stack" style={{ gap: 4 }}>{h.calls.map((c, i) => <li key={i} className="row" style={{ gap: 8 }}><Icon name={c.ok ? 'check' : 'x'} />{t('ho.call.' + c.tool) === 'ho.call.' + c.tool ? c.tool : t('ho.call.' + c.tool)}{c.detail && <span className="hint">{c.detail}</span>}</li>)}</ul>}
      </div>

      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      <div className="ap-acts">
        <button type="button" className="btn no-lift" disabled={!!busy || demo} onClick={() => void act('sakoo', async () => { await api.handoffToSakoo(runId); })}>{busy === 'sakoo' ? t('ap.working') : t('ho.sakoo')}</button>
      </div>
      <p className="hint">{t('ho.note')}</p>
    </section>
  );
}

function Snippet({ label, text, id, copied, onCopy }: { label: string; text: string; id: string; copied: string; onCopy: (k: string, v: string) => void }) {
  const { t } = useI18n();
  return (
    <div className="ho-snip">
      <div className="row" style={{ justifyContent: 'space-between' }}><span className="hint">{label}</span><button type="button" className="btn btn-s" onClick={() => onCopy(id, text)}>{copied === id ? t('log.copied') : t('live.copy')}</button></div>
      <pre className="term ltr" dir="ltr" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{text}</pre>
    </div>
  );
}
