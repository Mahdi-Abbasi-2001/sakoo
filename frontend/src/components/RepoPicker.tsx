import { useMemo, useState } from 'react';
import { useI18n } from '../i18n';

export interface Repo { full_name: string; private: boolean; default_branch: string; url: string }

/** A searchable list of the person's repositories, drawn inside the form instead of the browser's own dropdown (which grows wider than the card and opens from
 *  the wrong side in right-to-left pages). Names stay left-to-right; the lock marks private ones. */
export function RepoPicker({ repos, selected, onPick }: { repos: Repo[]; selected: string; onPick: (r: Repo) => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState('');
  const shown = useMemo(() => { const ql = q.trim().toLowerCase(); return ql ? repos.filter((r) => r.full_name.toLowerCase().includes(ql)) : repos; }, [repos, q]);
  return (
    <div className="field" style={{ marginTop: 12 }}>
      <label className="lbl" htmlFor="gh-q">{t('gh.pick')}</label>
      <input id="gh-q" className="inp" type="search" autoComplete="off" spellCheck={false} placeholder={t('gh.search')} value={q} onChange={(e) => setQ(e.target.value)} />
      <ul className="repo-list" role="listbox" aria-label={t('gh.pick')}>
        {shown.length === 0 && <li className="hint" style={{ padding: '10px 12px' }}>{t('gh.none')}</li>}
        {shown.map((r) => {
          const [owner, name] = r.full_name.split('/');
          return (
            <li key={r.full_name} role="option" aria-selected={r.url === selected}>
              <button type="button" className={'repo-item' + (r.url === selected ? ' on' : '')} onClick={() => onPick(r)}>
                <span className="repo-n ltr" dir="ltr"><b>{name}</b><i>{owner}</i></span>
                {r.private && <span className="repo-lock" title={t('gh.private')}><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg><span className="sr-only">{t('gh.private')}</span></span>}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
