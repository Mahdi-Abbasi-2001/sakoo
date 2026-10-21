import { rovingKeys } from '../lib/roving';
import { useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import type { Limits } from '../api/types';
import { RepoPicker } from '../components/RepoPicker';
import { GithubButton } from '../components/GithubButton';
import { Icon } from '../components/Icon';
import { useAuth } from '../auth/AuthContext';
import { useI18n, usePageTitle } from '../i18n';
import { errorText } from '../i18n/errors';
import { fileSize } from '../lib/time';
import { num } from '../run/format';

const ZIP_CODES = new Set(['source_required', 'upload_too_large', 'bad_zip', 'too_many_files', 'too_large', 'path_traversal', 'symlink', 'empty']);
const GIT_CODES = new Set(['git_scheme', 'git_credentials', 'git_host', 'git_path', 'git_failed', 'git_timeout', 'git_missing']);
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
const nameFromGit = (u: string) => { try { const p = new URL(u).pathname.split('/').filter(Boolean); return slug((p.at(-1) ?? '').replace(/\.git$/, '')); } catch { return ''; } };

export function NewDeployment() {
  const { t, lang } = useI18n();
  usePageTitle('new.title');
  const { me } = useAuth();
  const nav = useNavigate();
  const [limits, setLimits] = useState<Limits | null>(null);
  const [params] = useSearchParams();
  const [kind, setKind] = useState<'zip' | 'git'>(params.get('source') === 'git' ? 'git' : 'zip');
  const [file, setFile] = useState<File | null>(null);
  const [gitUrl, setGitUrl] = useState('');
  const [repos, setRepos] = useState<{ full_name: string; private: boolean; default_branch: string; url: string }[]>([]);
  const [ghUser, setGhUser] = useState<string | null>(null);
  const [gitToken, setGitToken] = useState(''); const [gitBranch, setGitBranch] = useState('');
  const [name, setName] = useState('');
  const [edited, setEdited] = useState(false);
  const [busy, setBusy] = useState<null | 'deploy' | 'preview'>(null);
  const [errs, setErrs] = useState<{ source?: string; name?: string; banner?: string; project?: number }>({});
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const connected = !!me?.has_liara_token;
  useEffect(() => { api.limits().then(setLimits).catch(() => setLimits(null)); }, []);
  useEffect(() => { api.settings().then((x) => setGhUser(x.github_login ?? null)).catch(() => undefined); }, []);
  useEffect(() => { if (kind === 'git' && limits?.github_oauth && ghUser) api.githubRepos().then((r) => setRepos(r.repos)).catch(() => setRepos([])); }, [kind, limits?.github_oauth, ghUser]);
  useEffect(() => { if (!edited) setName(kind === 'git' ? nameFromGit(gitUrl) : slug((file?.name ?? '').replace(/\.zip$/i, ''))); }, [gitUrl, file, kind, edited]);

  const pick = (f: File | null) => {
    setErrs((e) => ({ ...e, source: undefined }));
    if (!f) { setFile(null); return; }
    if (!/\.zip$/i.test(f.name)) { setFile(null); setErrs((e) => ({ ...e, source: t('new.err.notzip') })); return; }
    if (limits && f.size > limits.max_upload_mb * 1024 * 1024) { setFile(f); setErrs((e) => ({ ...e, source: t('new.err.size', { mb: num(lang, limits.max_upload_mb) }) })); return; }
    setFile(f);
  };
  const onDrop = (e: DragEvent) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files?.[0] ?? null); };

  const go = async (mode: 'deploy' | 'preview') => {
    const next: typeof errs = {};
    if (kind === 'zip' && !file) next.source = t('new.err.nosource');
    if (kind === 'git' && !gitUrl.trim()) next.source = t('new.err.url');
    if (!name.trim()) next.name = t('new.err.name');
    if (kind === 'zip' && file && limits && file.size > limits.max_upload_mb * 1024 * 1024) next.source = t('new.err.size', { mb: num(lang, limits.max_upload_mb) });
    if (next.source || next.name) { setErrs(next); return; }
    setBusy(mode); setErrs({});
    let projectId: number | undefined;
    try {
      const fd = new FormData(); fd.append('name', name.trim());
      if (kind === 'zip' && file) fd.append('file', file, file.name); else { fd.append('git_url', gitUrl.trim()); if (gitToken.trim()) fd.append('git_token', gitToken.trim()); if (gitBranch.trim()) fd.append('git_branch', gitBranch.trim()); }
      projectId = (await api.createProject(fd)).id;
      const run = await api.startRun(projectId, { mode });
      nav(`/runs/${run.id}`);
    } catch (e) {
      const code = e instanceof ApiError ? e.code : '';
      const text = errorText(lang, e);
      if (ZIP_CODES.has(code) && kind === 'zip') setErrs({ source: text });
      else if (GIT_CODES.has(code) && kind === 'git') setErrs({ source: text });
      else if (ZIP_CODES.has(code) || GIT_CODES.has(code)) setErrs({ source: text });
      else setErrs({ banner: text, project: projectId });
      setBusy(null);
    }
  };
  const submit = (e: FormEvent) => { e.preventDefault(); void go('deploy'); };
  const hosts = (limits?.git_hosts ?? ['github.com', 'gitlab.com', 'bitbucket.org']).join(', ');

  return (
    <div className="newdep">
      <div className="crumb"><Link to="/">{t('proj.crumb.projects')}</Link> / {t('new.title')}</div>
      <h1 className="title">{t('new.title')}</h1>
      <p className="hint"><Link to="/apps" className="link">{t('rm.link')}</Link></p>
      {!connected && me && <div className="banner" role="alert" style={{ marginTop: 20 }}><Icon name="warn" className="ic big" /><div><b>{t('new.noconn')}</b> <Link to="/settings" className="link">{t('new.noconn.link')}</Link></div></div>}
      <div className="newgrid">
        <form className="card pad stack" onSubmit={submit} noValidate>
          <span className="lbl" id="where">{t('new.where')}</span>
          <div className="seg" role="tablist" aria-labelledby="where" onKeyDown={rovingKeys('tab')}>
            <button type="button" role="tab" tabIndex={kind === 'zip' ? 0 : -1} aria-selected={kind === 'zip'} onClick={() => { setKind('zip'); setErrs({}); }}><Icon name="file" /> {t('new.tab.zip')}</button>
            <button type="button" role="tab" tabIndex={kind === 'git' ? 0 : -1} aria-selected={kind === 'git'} onClick={() => { setKind('git'); setErrs({}); }}><Icon name="link" /> {t('new.tab.git')}</button>
          </div>

          {kind === 'zip' ? (
            <div className="field">
              <div className={'drop' + (over ? ' over' : '') + (errs.source ? ' bad' : '')} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={onDrop}>
                <input ref={input} id="zipfile" className="sr-only" type="file" accept=".zip,application/zip" aria-describedby={errs.source ? 'zip-h zip-e' : 'zip-h'} onChange={(e) => pick(e.target.files?.[0] ?? null)} />
                {file ? (
                  <div className="row" style={{ justifyContent: 'space-between', width: '100%' }}><span className="mono">{t('new.picked', { name: file.name, size: fileSize(lang, file.size) })}</span><button type="button" className="btn btn-s" onClick={() => input.current?.click()}>{t('new.drop.replace')}</button></div>
                ) : (
                  <><Icon name="file" className="ic big" /><div>{t('new.drop.title')}</div><div className="hint">{t('new.drop.or')}</div><button type="button" className="btn" onClick={() => input.current?.click()}>{t('new.drop.choose')}</button></>
                )}
              </div>
              <p id="zip-h" className="hint" style={{ marginTop: 8 }}>{limits ? t('new.drop.limits', { mb: num(lang, limits.max_upload_mb), ext: num(lang, limits.max_extracted_mb), files: num(lang, limits.max_files) }) : ''}</p>
              {errs.source && <div id="zip-e" className="err" role="alert"><Icon name="alert" />{errs.source}</div>}
            </div>
          ) : (
            <div className="field">
              <label className="lbl" htmlFor="giturl">{t('new.git.label')}</label>
              <input id="giturl" className={'inp mono' + (errs.source ? ' bad' : '')} type="url" inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://github.com/owner/repo" value={gitUrl} onChange={(e) => { setGitUrl(e.target.value); setErrs((x) => ({ ...x, source: undefined })); }} aria-describedby={errs.source ? 'git-h git-e' : 'git-h'} aria-invalid={!!errs.source} />
              {limits?.github_oauth && !ghUser && <div className="card pad stack" style={{ marginTop: 12, gap: 8 }}>
                <b>{t('gh.new.t')}</b><p className="hint" style={{ margin: 0 }}>{t('gh.new.d')}</p>
                <div><GithubButton mode="connect" label={t('gh.on')} className="btn btn-p" next="/new" /></div></div>}
              {repos.length > 0 && <RepoPicker repos={repos} selected={gitUrl} onPick={(r) => { setGitUrl(r.url); setGitBranch(r.default_branch); setEdited(false); }} />}
              <p id="git-h" className="hint" style={{ marginTop: 8 }}>{t(limits?.github_oauth ? 'new.git.hint_oauth' : 'new.git.hint', { hosts })}</p>
              {errs.source && <div id="git-e" className="err" role="alert"><Icon name="alert" />{errs.source}</div>}
              <div className="row" style={{ marginTop: 12, gap: 12, alignItems: 'flex-start' }}>
                {!limits?.github_oauth
                  ? <div className="field" style={{ flex: 2, minWidth: 220 }}><label className="lbl" htmlFor="gittoken">{t('new.git.token')}</label>
                      <input id="gittoken" className="inp mono ltr" dir="ltr" type="password" autoComplete="off" spellCheck={false} value={gitToken} onChange={(e) => setGitToken(e.target.value)} aria-describedby="gittoken-h" />
                      <p id="gittoken-h" className="hint" style={{ marginTop: 6 }}>{t('new.git.token_h')}</p></div>
                  : <details style={{ flex: 2, minWidth: 220 }}><summary className="hint">{t('gh.token_instead')}</summary>
                      <div className="field" style={{ marginTop: 8 }}><label className="lbl" htmlFor="gittoken">{t('new.git.token')}</label>
                        <input id="gittoken" className="inp mono ltr" dir="ltr" type="password" autoComplete="off" spellCheck={false} value={gitToken} onChange={(e) => setGitToken(e.target.value)} aria-describedby="gittoken-h" />
                        <p id="gittoken-h" className="hint" style={{ marginTop: 6 }}>{t('new.git.token_h')}</p></div></details>}
                <div className="field" style={{ flex: 1, minWidth: 140 }}><label className="lbl" htmlFor="gitbranch">{t('new.git.branch')}</label>
                  <input id="gitbranch" className="inp mono ltr" dir="ltr" autoComplete="off" spellCheck={false} value={gitBranch} onChange={(e) => setGitBranch(e.target.value)} /></div>
              </div>
              {!errs.source && nameFromGit(gitUrl) && <div className="row hint" style={{ marginTop: 8, gap: 6 }}><Icon name="check" />{t('new.git.suggest', { name: nameFromGit(gitUrl) })}</div>}
            </div>
          )}

          <div className="field">
            <label className="lbl" htmlFor="pname">{t('new.name.label')}</label>
            <input id="pname" className={'inp mono' + (errs.name ? ' bad' : '')} autoComplete="off" spellCheck={false} value={name} onChange={(e) => { setName(e.target.value); setEdited(true); setErrs((x) => ({ ...x, name: undefined })); }} aria-describedby={errs.name ? 'pn-h pn-e' : 'pn-h'} aria-invalid={!!errs.name} />
            <p id="pn-h" className="hint" style={{ marginTop: 8 }}>{t('new.name.hint', { web: (name || 'blog') + '-web', db: (name || 'blog') + '-db' })}</p>
            {errs.name && <div id="pn-e" className="err" role="alert"><Icon name="alert" />{errs.name}</div>}
          </div>

          {errs.banner && <div className="err" role="alert"><Icon name="alert" />{errs.banner}{errs.project && <> <Link className="link" to={`/projects/${errs.project}`}>{t('dash.act.details')}</Link></>}</div>}
          <div className="row">
            <button type="submit" className="btn btn-p" disabled={!!busy || !connected}>{busy === 'deploy' ? t('new.busy') : t('new.go')}</button>
            <button type="button" className="link" disabled={!!busy || !connected} onClick={() => void go('preview')}>{busy === 'preview' ? t('new.busy') : t('new.preview')}</button>
          </div>
          <p className="hint">{t('new.next')}</p>
        </form>
        <aside className="stack">
          <section className="card pad stack" style={{ gap: 10 }}><h2 className="card-t">{t('new.read.t')}</h2>{[1, 2, 3].map((n) => <div className="li" key={n}><Icon name="check" /><span>{t('new.read.' + n)}</span></div>)}</section>
          <section className="card pad stack" style={{ gap: 10 }}><h2 className="card-t">{t('new.never.t')}</h2>{[1, 2, 3, 4].map((n) => <div className="li" key={n}><Icon name="shield" /><span>{t('new.never.' + n)}</span></div>)}</section>
        </aside>
      </div>
    </div>
  );
}
