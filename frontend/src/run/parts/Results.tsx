import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../api/client';
import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { errorText } from '../../i18n/errors';
import { when } from '../../lib/time';
import { useSay } from '../wording';
import type { RunViewState } from '../types';
import type { FollowUp } from '../../api/events';

/** What the right column shows when no request is open: the outcome of the run. */
export function ResultCard({ view, projectId, freeDays, demo, onCleanup }: { view: RunViewState; projectId?: number; freeDays: number | null; demo?: boolean; onCleanup?: () => void }) {
  const { t, lang } = useI18n();
  const say = useSay();
  const [copied, setCopied] = useState(false);
  const r = view.result;
  if (view.removed) {
    const rm = view.removed, names = [...rm.apps.map((n) => ['app', n]), ...rm.databases.map((n) => ['database', n]), ...(rm.buckets ?? []).map((n) => ['bucket', n]), ...(rm.mail_servers ?? []).map((n) => ['mail', n])];
    const abs = rm.at ? new Intl.DateTimeFormat(lang === 'fa' ? 'fa-IR' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(rm.at * 1000)) : null;
    return (
      <section className="card pad result removed" aria-labelledby="rm-h">
        <span className="badge s-pend" style={{ alignSelf: 'flex-start' }}><Icon name="dashed" />{t('rm.badge')}</span>
        <h2 className="ap-title" id="rm-h">{t('rm.title')}</h2>
        <p className="ap-text">{t('rm.text')}</p>
        {names.length > 0 && <ul className="stack" style={{ gap: 6 }} aria-label={t('rm.list')}>{names.map(([k, n]) => <li key={k + n} className="row" style={{ gap: 8 }}><Icon name={k === 'database' ? 'db' : k === 'bucket' ? 'disk' : k === 'mail' ? 'link' : 'server'} /><span className="mono">{n}</span><span className="hint">{t('rm.kind.' + k)}</span></li>)}</ul>}
        {rm.at && <p className="hint">{t('rm.when', { when: '' })}<time dateTime={new Date(rm.at * 1000).toISOString()} title={abs ?? undefined}>{when(lang, rm.at)}</time></p>}
        {projectId && !demo && <div className="row"><Link className="btn btn-p" to={`/projects/${projectId}`}>{t('res.back')}</Link></div>}
      </section>
    );
  }
  if (view.status === 'interrupted') return <section className="card pad result"><h2 className="ap-title">{t('run.h1.interrupted.a')} {t('run.h1.interrupted.b')}</h2><p className="ap-text">{t('run.sub.interrupted')}</p></section>;
  if (!r) return null;
  const url = Object.values(r.urls)[0];
  if (r.ok && !r.dryRun) {
    const test = view.tests.at(-1);
    return (
      <div className="stack">
        <section className="card pad result live" aria-label={t('live.title')}>
          <div className="row" style={{ justifyContent: 'space-between' }}><span className="badge s-ok"><Icon name="check" />{t('live.badge')}</span>{test && <span className="hint">{t('live.checked', { detail: test.detail })}</span>}</div>
          <h2 className="ap-title">{t('live.title')}</h2>
          {Object.entries(r.urls).map(([app, u]) => (
            <div className="url-row" key={app}><span className="mono url">{u}</span>
              <button type="button" className="btn" onClick={async () => { try { await navigator.clipboard.writeText(u); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* blocked */ } }}>{copied ? t('log.copied') : t('live.copy')}</button>
              <a className="btn btn-p" href={u} target="_blank" rel="noreferrer noopener">{t('live.open')}</a></div>
          ))}
          {!url && <p className="ap-text">{t('pl.live.none')}</p>}
        </section>
        <section className="card pad result free" aria-label={t('live.free_title', { n: freeDays ?? 7 })}>
          <div className="row" style={{ alignItems: 'flex-start', flexWrap: 'nowrap' }}><Icon name="warn" className="ic big warn" />
            <div className="stack" style={{ gap: 8 }}><h3 className="card-t">{t('live.free_title', { n: freeDays ?? 7 })}</h3><p className="hint">{t('live.free_text')}</p>
              <div className="row">{projectId && !demo
                ? <Link className="btn btn-p" to={`/projects/${projectId}#domain`}>{t('live.domain')}</Link>
                : <><button type="button" className="btn btn-p no-lift" disabled>{t('live.domain')}</button><span className="badge s-pend"><Icon name="dashed" />{t('live.domain_planned')}</span></>}</div></div></div>
        </section>
        <FollowUps items={r.followUps} />
      </div>
    );
  }
  if (r.ok && r.dryRun) return <section className="card pad result"><span className="badge s-ok"><Icon name="check" />{t('run.h1.preview.b')}</span><h2 className="ap-title">{t('run.h1.preview.a')} {t('run.h1.preview.b')}</h2><p className="ap-text">{t('run.sub.preview')}</p><p className="hint mono">{r.wouldSend.length} · POST/PUT</p></section>;
  if (r.stopped === 'cancelled' && r.reasonCode === 'run_cancelled') return (
    <section className="card pad result"><span className="badge s-warn"><Icon name="diamond" />{t('status.cancelled')}</span><h2 className="ap-title">{t('run.h1.stopped.a')} {t('run.h1.stopped.b')}</h2><p className="ap-text">{t('run.sub.stopped')}</p>
      {projectId && <div className="row"><Link className="btn btn-p" to={`/projects/${projectId}`}>{t('res.back')}</Link></div>}</section>
  );
  if (r.stopped === 'cancelled') return (
    <><section className="card pad result"><span className="badge s-warn"><Icon name="diamond" />{t('node.declined')}</span><h2 className="ap-title">{t('run.h1.cancelled.a')} {t('run.h1.cancelled.b')}</h2><p className="ap-text">{t('run.sub.cancelled')}</p>
      {projectId && <div className="row"><Link className="btn btn-p" to={`/projects/${projectId}`}>{t('res.back')}</Link></div>}</section><FollowUps items={r.followUps} /></>
  );
  const code = r.diagnosis?.code ?? r.reasonCode;
  return (
    <section className="card pad result fail" aria-label={t('fail.reason')}>
      <span className="badge s-fail"><Icon name="x" />{t('agent.failed')}</span>
      <h2 className="ap-title">{view.diagnoses.length ? say('diagnosis', code, r.diagnosis?.params ?? r.reasonParams, '', 'title') || t('run.h1.buildfailed.a') : t('run.h1.failed.a') + ' ' + t('run.h1.failed.b')}</h2>
      {code && <p className="ap-text">{say('outcome', r.reasonCode, r.reasonParams, '') || say('diagnosis', code, r.diagnosis?.params ?? r.reasonParams, r.diagnosis?.summary ?? code)}</p>}
      {(r.reasonCode === 'liara_token_invalid' || r.reasonCode === 'liara_forbidden' || r.reasonCode === 'team_required') && <div className="row"><Link className="btn btn-p" to="/settings">{t('nav.settings')}</Link></div>}
      {r.reasonCode === 'network_required' && projectId && <div className="row"><Link className="btn btn-p" to={`/projects/${projectId}`}>{t('res.back')}</Link></div>}
      {r.blockers.length > 0 && <><p className="sect">{t('fail.blockers')}</p><ul className="stack" style={{ gap: 6 }}>{r.blockers.map((b, i) => <li key={i}>{say('note', b.code, b.params, b.message)}</li>)}</ul></>}
      <p className="hint">{(r.created.apps.length + r.created.databases.length + (r.created.buckets ?? []).length + (r.created.mail_servers ?? []).length) ? `${t('fail.created')}: ${[...r.created.apps, ...r.created.databases, ...(r.created.buckets ?? []), ...(r.created.mail_servers ?? [])].join(', ')}` : t('fail.nothing_created')}</p>
      {(r.created.apps.length + r.created.databases.length + (r.created.buckets ?? []).length + (r.created.mail_servers ?? []).length) > 0 && <div className="row"><button type="button" className="btn btn-d" onClick={onCleanup} disabled={demo}>{t('run.delete_created')}</button></div>}
    </section>
  );
}

export function FailBanner({ view }: { view: RunViewState }) {
  const { t } = useI18n();
  if (view.removed) return null;
  const created = view.resources.filter((r) => r.kind !== 'disk' && (r.state === 'creating' || r.state === 'live' || r.state === 'failed'));
  if (!created.length || view.status === 'succeeded' || view.status === 'running') return null;
  return <div className="banner"><Icon name="alert" className="ic big" /><div><b>{t('fail.banner')}</b><p className="hint">{t('fail.banner_sub')}</p></div></div>;
}

/** Deletes what this run created. Typing the project name is the confirmation the API asks for. */
export function Cleanup({ runId, projectName, onDone, onCancel, demo }: { runId: string; projectName: string; onDone: (deleted: { apps: string[]; databases: string[]; buckets?: string[]; mail_servers?: string[] }) => void; onCancel: () => void; demo?: boolean }) {
  const { t, lang } = useI18n();
  const [name, setName] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null); const [done, setDone] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // a dialog moves focus in, closes on Escape and gives focus back to what opened it
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    inputRef.current?.focus();
    return () => { if (opener && document.body.contains(opener)) opener.focus(); };
  }, []);
  const go = async () => {
    setBusy(true); setErr(null);
    try { const r = await api.cleanup(runId, { confirm: name }); setDone([...r.deleted.apps, ...r.deleted.databases, ...(r.deleted.buckets ?? []), ...(r.deleted.mail_servers ?? [])].join(', ')); onDone({ apps: r.deleted.apps, databases: r.deleted.databases, buckets: r.deleted.buckets ?? [], mail_servers: r.deleted.mail_servers ?? [] }); }
    catch (e) { setErr(errorText(lang, e)); } finally { setBusy(false); }
  };
  return (
    <section className="card pad stack" aria-labelledby="cl-h" role="dialog" aria-modal="false" onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } }}>
      <h3 id="cl-h" className="card-t">{t('clean.title')}</h3><p className="hint">{t('clean.lead')}</p>
      {done ? <p>{t('clean.done', { names: done })}</p> : <>
        <label className="lbl" htmlFor="cl-n">{t('clean.type')}: <span className="code">{projectName}</span></label>
        <input id="cl-n" ref={inputRef} className="inp mono" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
        <div className="row"><button type="button" className="btn btn-d" disabled={busy || demo || name !== projectName} onClick={() => void go()}>{t('clean.confirm')}</button><button type="button" className="btn" onClick={onCancel}>{t('clean.cancel')}</button></div></>}
    </section>
  );
}

/** What the agent cannot do for the person: DNS records for mail, a mode switch, the way to the managed WordPress. Shown under the result. */
export function FollowUps({ items }: { items: FollowUp[] }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState('');
  if (!items.length) return null;
  const copy = async (v: string) => { try { await navigator.clipboard.writeText(v); setCopied(v); setTimeout(() => setCopied(''), 1500); } catch { /* blocked */ } };
  return (
    <section className="card pad stack result" style={{ gap: 12 }} aria-label={t('res.next.t')}>
      <h3 className="card-t">{t('res.next.t')}</h3>
      {items.map((f) => (
        <div className="stack" style={{ gap: 6 }} key={f.code}>
          <b>{t(`msg.followup.${f.code}.t`)}</b>
          <p className="hint">{t(`msg.followup.${f.code}`, Object.fromEntries(Object.entries(f.params ?? {}).map(([k, v]) => [k, String(v)])))}</p>
          {f.records.map((r) => (
            <div className="rw" key={r.type + r.name + r.value} style={{ flexWrap: 'wrap' }}>
              <span className="badge">{r.type}</span><span className="code ltr" dir="ltr">{r.name}</span>
              <span className="code ltr" dir="ltr" style={{ overflowWrap: 'anywhere' }}>{r.value}</span>
              <button type="button" className="btn btn-s" onClick={() => void copy(r.value)}>{copied === r.value ? t('log.copied') : t('live.copy')}</button>
              {r.status && <span className={'badge ' + (r.status.toUpperCase() === 'VALID' ? 's-ok' : 's-fail')}><Icon name={r.status.toUpperCase() === 'VALID' ? 'check' : 'x'} />{r.status}</span>}
            </div>
          ))}
          {f.code === 'wordpress_console' && <div><a className="btn btn-p" href={String(f.params?.url ?? 'https://console.liara.ir')} target="_blank" rel="noreferrer noopener">{t('proj.aside.console')}</a></div>}
        </div>
      ))}
    </section>
  );
}
