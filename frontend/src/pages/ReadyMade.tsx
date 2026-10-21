import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n, usePageTitle } from '../i18n';
import { errorText } from '../i18n/errors';
import { num } from '../run/format';

/** Liara's ready-made apps. Installing one starts an ordinary run: the plan and its cost are shown and nothing is created until it is approved. */
export function ReadyMade() {
  const { t, lang } = useI18n();
  usePageTitle(t('rm.title'));
  const nav = useNavigate();
  const [apps, setApps] = useState<G.OneClickAppOut[] | null>(null);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.oneclickApps().then((r) => setApps(r.apps)).catch((e) => setErr(errorText(lang, e))); }, [lang]);
  const shown = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return (apps ?? []).filter((a) => !ql || (a.app + ' ' + a.categories.join(' ')).toLowerCase().includes(ql));
  }, [apps, q]);
  const install = async (a: G.OneClickAppOut) => {
    setBusy(a.app); setErr(null);
    try {
      const p = await api.createOneClick(a.app, a.app);
      const run = await api.startRun(p.id, { mode: 'deploy' });
      nav(`/runs/${run.id}`);
    } catch (e) { setErr(errorText(lang, e)); setBusy(null); }
  };
  return (
    <div className="newdep">
      <div className="crumb"><Link to="/">{t('proj.crumb.projects')}</Link> / {t('rm.title')}</div>
      <h1 className="title">{t('rm.title')}</h1>
      <p className="hint" style={{ maxWidth: 680 }}>{t('rm.d')}</p>
      <div className="field" style={{ margin: '16px 0', maxWidth: 360 }}>
        <label className="lbl" htmlFor="rm-q">{t('rm.search')}</label>
        <input id="rm-q" className="inp" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
      </div>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
      {!apps && !err && <div className="sk" style={{ height: 120 }} />}
      <ul className="stack" style={{ gap: 12, listStyle: 'none', padding: 0 }}>
        {shown.map((a) => (
          <li key={a.app} className="card pad stack" style={{ gap: 6 }}>
            <div className="row" style={{ gap: 10 }}>
              <b className="mono ltr" dir="ltr">{a.app}</b>
              {a.categories.slice(0, 3).map((c) => <span key={c} className="chip">{c}</span>)}
              <span style={{ flex: 1 }} />
              <span className="hint">{t('rm.installs', { n: num(lang, a.installs) })}</span>
            </div>
            {a.about && <p className="hint" style={{ margin: 0 }}>{a.about}</p>}
            <div className="row" style={{ gap: 8 }}>
              {a.databases.map((d) => <span key={d} className="badge"><Icon name="check" />{d}</span>)}
              <span style={{ flex: 1 }} />
              {a.needs_vm
                ? <span className="hint">{t('rm.vm')}</span>
                : <button type="button" className="btn btn-p" disabled={!!busy} onClick={() => void install(a)}>{busy === a.app ? t('proj.starting') : t('rm.install')}</button>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
