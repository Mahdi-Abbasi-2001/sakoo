import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';

/** Preview a branch: the same repository at another branch becomes its own project, with its own app and cleanup. */
export function BranchSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const nav = useNavigate();
  const [branch, setBranch] = useState('');
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    if (!branch.trim()) { setErr(t('br.err')); return; }
    setBusy(true); setErr(null);
    try { nav(`/projects/${(await api.previewBranch(pid, branch.trim())).id}`); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); }
  };
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-label={t('br.t')}>
      <h2 className="card-t">{t('br.t')}</h2>
      <p className="hint">{t('br.d')}</p>
      <form className="row" style={{ gap: 10 }} onSubmit={(e) => { e.preventDefault(); void go(); }}>
        <label className="lbl" htmlFor="br-name">{t('br.name')}</label>
        <input id="br-name" className="inp mono ltr" dir="ltr" value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="feature/login" autoComplete="off" spellCheck={false} style={{ maxWidth: 260 }} />
        <button type="submit" className="btn btn-p" disabled={busy}>{busy ? t('proj.starting') : t('br.go')}</button>
      </form>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}
