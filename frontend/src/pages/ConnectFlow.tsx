import { rovingKeys } from '../lib/roving';
import { useState } from 'react';
import { api } from '../api/client';
import type { Team } from '../api/types';
import { useI18n, lookup, fill } from '../i18n';
import { errorText } from '../i18n/errors';
import { useAuth } from '../auth/AuthContext';
import { Icon } from '../components/Icon';

type Phase = 'idle' | 'checking' | 'invalid' | 'team' | 'ready' | 'saving';
interface Checked { teams: Team[]; networks: string[]; apps: string[]; teamRequired: boolean }

/** Connect-Liara flow: check without saving, a mandatory team choice when the account has teams, then save. The token lives only in this
 *  component's state while the user is typing and is dropped the moment it is saved or the flow is closed. */
export function ConnectFlow() {
  const { t, lang } = useI18n();
  const { me, refresh } = useAuth();
  const [replacing, setReplacing] = useState(false);
  const [token, setToken] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<Checked | null>(null);
  const [team, setTeam] = useState('');
  const [error, setError] = useState<string | null>(null);

  const reset = () => { setToken(''); setPhase('idle'); setResult(null); setTeam(''); setError(null); };
  const connected = !!me?.has_liara_token && !replacing;

  const check = async (teamId: string) => {
    setPhase('checking'); setError(null);
    try {
      const r = await api.liaraCheck({ token: token.trim(), team_id: teamId });
      if (!r.valid) {
        const code = r.code || 'liara_token_invalid';             // liara_token_invalid / liara_forbidden / liara_rejected / liara_unreachable
        const text = lookup(lang, 'err.' + code);
        setResult(null); setPhase('invalid');
        setError(text ? fill(text, { status: String((r.params as { status?: number } | undefined)?.status ?? '') }) : t('err.unknown'));
        return;
      }
      const c: Checked = { teams: r.teams ?? [], networks: r.networks ?? [], apps: r.apps ?? [], teamRequired: !!r.team_required };
      setResult(c); setPhase(c.teamRequired ? 'team' : 'ready');
    } catch (e) { setResult(null); setPhase('invalid'); setError(errorText(lang, e)); }
  };

  const save = async () => {
    setPhase('saving'); setError(null);
    const chosen = result?.teams.find((x) => x.id === team) ?? (result?.teams.length === 1 ? result.teams[0] : undefined);
    try {
      await api.saveSettings({ liara_token: token.trim(), team_id: chosen?.id, team_name: chosen?.name });   // team_name is display-only, from the check response
      reset(); setReplacing(false); await refresh();
    } catch (e) { setPhase('ready'); setError(errorText(lang, e)); }
  };

  const disconnect = async () => {
    setPhase('saving');
    try { await api.deleteToken(); reset(); setReplacing(false); await refresh(); } catch (e) { setPhase('idle'); setError(errorText(lang, e)); }
  };

  const teamName = (id: string) => result?.teams.find((x) => x.id === id)?.name ?? id;

  if (connected) {
    return (
      <section className="card pad row" aria-labelledby="done-h" style={{ justifyContent: 'space-between' }}>
        <div className="stack" style={{ gap: 8 }}>
          <span className="badge s-ok"><Icon name="check" />{t('connect.done')}</span>
          <div id="done-h">{me?.team_id ? t('connect.done.team', { team: me.team_name || me.team_id }) : t('connect.done.noteam')}</div>
        </div>
        <div className="row">
          <button type="button" className="btn" onClick={() => setReplacing(true)}>{t('connect.replace')}</button>
          <button type="button" className="btn" disabled={phase === 'saving'} onClick={() => void disconnect()}>{phase === 'saving' ? t('connect.disconnecting') : t('connect.disconnect')}</button>
        </div>
      </section>
    );
  }

  const asking = phase === 'team' || (phase === 'checking' && !!result?.teamRequired);
  const stateBadge =
    phase === 'invalid' ? <span className="badge s-fail"><Icon name="x" />{t('connect.state.invalid')}</span> :
    phase === 'team' ? <span className="badge s-wait"><Icon name="diamond" />{t('connect.state.team')}</span> :
    (phase === 'ready' || phase === 'saving') && result ? <span className="badge s-ok"><Icon name="check" />{t('connect.state.ready')}</span> : null;

  return (
    <div className="stack">
      <section className="card pad" aria-labelledby="how-h">
        <h3 id="how-h" className="sect" style={{ marginBottom: 6 }}>{t('connect.howto')}</h3>
        {[1, 2, 3, 4].map((n) => (
          <div className="stp" key={n}><div className="stn">{lang === 'fa' ? '۱۲۳۴'[n - 1] : n}</div><div>{t('connect.step' + n)}{n === 1 && <div className="hint"><span className="code">console.liara.ir</span></div>}</div></div>
        ))}
      </section>

      <section className="card pad stack" aria-live="polite" aria-labelledby="chk-h">
        <h3 id="chk-h" className="sr-only">{t('connect.check')}</h3>
        {stateBadge}
        <div className="field">
          <label className="lbl" htmlFor="tok">{t('connect.token')}</label>
          <input id="tok" className={'inp mono' + (phase === 'invalid' ? ' bad' : '')} type="password" name="liara-token" autoComplete="off" autoCapitalize="none" spellCheck={false}
            value={token} onChange={(e) => { setToken(e.target.value); if (phase !== 'idle') { setPhase('idle'); setResult(null); setTeam(''); } }} aria-invalid={phase === 'invalid'} aria-describedby="tok-h" onKeyDown={(e) => { if (e.key === 'Enter' && token.trim().length >= 10 && (phase === 'idle' || phase === 'invalid')) { e.preventDefault(); void check(''); } }} />
          <span id="tok-h" className="hint" style={{ marginTop: 6 }}>{t('connect.token.hint')}</span>
        </div>
        {error && <div className="err" role="alert"><Icon name="alert" />{error}</div>}

        {asking && result && (
          <div className="field">
            <span className="lbl" id="team-l">{t('connect.team.q')} <span className="badge s-wait" style={{ marginInlineStart: 8 }}>{t('connect.team.required')}</span></span>
            <div className="opts" role="radiogroup" aria-labelledby="team-l" onKeyDown={rovingKeys('radio')}>
              {result.teams.map((x, i) => (
                <button type="button" role="radio" aria-checked={team === x.id} tabIndex={team === x.id || (!result.teams.some((y) => y.id === team) && i === 0) ? 0 : -1} className="opt no-lift" key={x.id} onClick={() => setTeam(x.id)}>
                  <span className="rd" /><span>{x.name}</span><span className="meta">{t('connect.team.kind')}</span>
                </button>
              ))}
            </div>
            <p className="hint" style={{ marginTop: 10 }}>{t('connect.team.why')}</p>
          </div>
        )}

        {(phase === 'ready' || phase === 'saving') && result && (
          <div className="stack" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 8 }}><Icon name="check" />{t('connect.valid')}</div>
            {team && <div><span className="lbl">{t('connect.team.label')}</span><div className="opt" aria-checked="true" role="note"><span className="rd" /><span>{teamName(team)}</span></div></div>}
            <p className="sect">{t('connect.sees')}</p>
            {result.networks.length > 0 && <div className="row"><Icon name="plug" />{result.networks.length === 1 ? <>{t('connect.sees.network', { name: '' })}<span className="code">{result.networks[0]}</span></> : t('connect.sees.networks', { count: result.networks.length })}</div>}
            <div className="row"><Icon name="server" />{result.apps.length ? t('connect.sees.apps', { count: result.apps.length, names: '' }) : t('connect.sees.none')}{result.apps.slice(0, 8).map((a) => <span className="code" key={a}>{a}</span>)}</div>
            <p className="hint">{t('connect.sees.why')}</p>
          </div>
        )}

        <div className="row">
          {asking ? (
            <button type="button" className="btn btn-p" disabled={!team || phase === 'checking'} onClick={() => void check(team)}>{phase === 'checking' ? t('connect.checking') : t('connect.team.check')}</button>
          ) : phase === 'ready' || phase === 'saving' ? (
            <>
              <button type="button" className="btn btn-p" disabled={phase === 'saving'} onClick={() => void save()}>{phase === 'saving' ? t('connect.saving') : t('connect.save')}</button>
              <button type="button" className="btn" disabled={phase === 'saving'} onClick={() => void check(team)}>{t('connect.again')}</button>
            </>
          ) : (
            <button type="button" className="btn btn-p" disabled={token.trim().length < 10 || phase === 'checking'} onClick={() => void check('')}>{phase === 'checking' ? t('connect.checking') : t('connect.check')}</button>
          )}
          {replacing && <button type="button" className="btn" onClick={() => { reset(); setReplacing(false); }}>{t('connect.cancel')}</button>}
        </div>
        {phase === 'team' && !team && <p className="hint">{t('connect.team.pick')}</p>}
        {(phase === 'ready' || phase === 'saving') && <p className="hint">{t('connect.stored')}</p>}
      </section>
    </div>
  );
}
