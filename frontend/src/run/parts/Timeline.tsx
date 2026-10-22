import { useRef, useState, type ReactNode } from 'react';
import { useFollow } from '../../lib/useFollow';
import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { rel, unit } from '../format';
import { howText } from '../../i18n/messages';
import { useSay } from '../wording';
import type { RunViewState, TimelineItem } from '../types';

const DOT: Record<string, { tone: string; icon: string }> = { ok: { tone: 's-ok', icon: 'check' }, warn: { tone: 's-warn', icon: 'warn' }, fail: { tone: 's-fail', icon: 'x' }, wait: { tone: 's-wait', icon: 'diamond' }, run: { tone: 's-run', icon: 'loader' } };
const FILE = (fs: Record<string, string[]>) => Object.entries(fs).flatMap(([dir, files]) => files.map((f) => (dir === '.' ? f : `${dir}/${f}`)));

export function Timeline({ view, filter, after, footer }: { view: RunViewState; filter: string | null; after?: ReactNode; footer?: ReactNode }) {
  const { t } = useI18n();
  const items = filter ? view.timeline.filter((it) => matches(it, filter)) : view.timeline;
  const t0 = view.t0 ?? 0;
  // The conversation scrolls inside its own box, so a long one never pushes the reports far down the page. While the run is going it follows the newest line,
  // unless the person has scrolled up to read.
  const box = useRef<HTMLDivElement>(null);
  const live = view.status === 'running' || view.status === 'waiting';
  const wasLive = useRef(live);          // a run watched while it went keeps following to its last line; a finished run opened later starts at the top
  if (live) wasLive.current = true;
  const { onScroll } = useFollow(box, wasLive.current);
  return (
    <section className="card agent-card" aria-label={t('ag.title')}>
      <div className="card-h"><div><h2 className="card-t">{t('ag.title')}</h2><p className="hint">{t('ag.hint')}</p></div></div>
      <div className="agent-scroll" ref={box} tabIndex={0} role="region" aria-label={t('ag.title')} onScroll={onScroll}>
        <ol className="steps">
          {items.map((it) => <Step key={it.key} it={it} t0={t0} view={view} />)}
        </ol>
        {after}
      </div>
      {footer}
    </section>
  );
}

function matches(it: TimelineItem, f: string) {
  if (f === 'analyze') return it.type === 'read';
  if (f === 'plan') return ['question', 'plan', 'notes'].includes(it.type);
  if (f === 'confirm') return ['confirm', 'secret', 'expired'].includes(it.type) || (it.type === 'finished');
  if (f === 'provision') return it.type === 'resource' && (it.kind === 'database' || it.kind === 'bucket' || it.kind === 'mail');
  if (f === 'deploy') return (it.type === 'resource' && it.kind === 'app') || ['diagnosis', 'fix', 'patch', 'warning', 'handoff'].includes(it.type);
  if (f === 'verify' || f === 'live') return it.type === 'test' || it.type === 'finished';
  return true;
}

function Chips({ list }: { list: { file: string; note?: string }[] }) {
  return <div className="chips">{list.map((c, i) => <span className="chip" key={i}><Icon name="file" /><b>{c.file}</b>{c.note ? <span>{c.note}</span> : null}</span>)}</div>;
}

function Step({ it, t0, view }: { it: TimelineItem; t0: number; view: RunViewState }) {
  const { t, lang } = useI18n();
  const say = useSay();
  const [more, setMore] = useState(false);
  const d = DOT[it.state] ?? DOT.ok;
  let title = ''; let body: React.ReactNode = null;
  switch (it.type) {
    case 'read': {
      const files = FILE(it.manifests);
      title = t('ag.read'); body = <><p className="sp">{t('ag.read_text', { files: files.join(', ') })}{it.hints.length ? ' ' + t('ag.hints', { hints: it.hints.join(', ') }) : ''}</p><Chips list={files.map((f) => ({ file: f }))} /></>; break;
    }
    case 'question': {
      title = it.answer === null ? t('ag.asked') : t('ag.answered');
      body = <div className="drow"><p>{say('question', it.code, it.params, it.text)}</p>{it.answer !== null && <div className="kv"><Icon name="check" /><b>{say('option', it.code ? `${it.code}.${it.answer}` : null, it.params, it.answer)}</b></div>}</div>; break;
    }
    case 'plan': {
      title = t('ag.planned', { apps: unit(t, lang, it.apps, 'app'), databases: unit(t, lang, it.databases, 'database') });
      body = <><p className="sp">{t('ag.planned_from', { how: howText(lang, it.howCode, it.howParams, it.how) })}</p><div className="stack" style={{ gap: 8 }}>
        {it.plan.apps.map((a) => <div className="drow" key={a.name}><p><b className="mono">{a.name}</b> <span className="hint">{a.platform}{a.port ? ` :${a.port}` : ''} · {t('res.role_' + a.role)}</span></p><Chips list={a.evidence.map((e) => ({ file: e.file, note: e.note }))} /></div>)}
        {it.plan.databases.map((a) => <div className="drow" key={a.id}><p><b className="mono">{a.id}</b> <span className="hint">{a.kind}{a.version ? ' ' + a.version : ''}</span></p><Chips list={a.evidence.map((e) => ({ file: e.file, note: e.note }))} /></div>)}
      </div></>; break;
    }
    case 'notes': {
      const list = it.notes.filter((n) => n.level !== 'info'); const info = it.notes.filter((n) => n.level === 'info');
      const flagged = it.readiness;
      title = it.warnings ? t('ag.warnings', { count: it.warnings }) : t('ag.notes_ok');
      body = <div className="stack" style={{ gap: 8 }}>
        {list.map((n, i) => <div className={'note ' + (n.level === 'blocker' ? 'blk' : '')} key={i}><p><span className={'badge ' + (n.level === 'blocker' ? 's-fail' : 's-warn')}><Icon name={n.level === 'blocker' ? 'x' : 'warn'} />{t('plan.level.' + n.level)}</span> {say('note', n.code, n.params, n.message)}</p>{n.evidence.length > 0 && <Chips list={n.evidence.map((e) => ({ file: e.file, note: e.note }))} />}</div>)}
        {flagged.map((f, i) => <div className="note" key={'r' + i}><p><span className={'badge ' + (f.level === 'warning' ? 's-warn' : 's-run')}><Icon name={f.level === 'warning' ? 'warn' : 'alert'} />{t('plan.level.' + f.level)}</span> {say('readiness', f.code, f.params, f.code)}</p><Chips list={[{ file: f.file, note: f.line ? `:${f.line}` : undefined }]} /></div>)}
        {info.length > 0 && <div className="note"><p>{t('ag.notes_more', { count: info.length })} <button type="button" className="link" onClick={() => setMore(!more)} aria-expanded={more}>{more ? t('ag.hide') : t('ag.show')}</button></p>
          {more && info.map((n, i) => <p key={i} className="hint">{say('note', n.code, n.params, n.message)}</p>)}</div>}
      </div>; break;
    }
    case 'confirm': title = it.state === 'wait' ? t('ag.waiting_you') : it.accepted ? t('ag.you_approved') : t('ag.you_declined'); body = it.state === 'wait' ? null : <p className="sp">{say('confirm', it.code, it.params, it.prompt)}</p>; break;
    case 'secret': title = it.state === 'wait' ? t('ag.secret_wait', { name: it.name }) : t('ag.secret_ok', { name: it.name }); break;
    case 'resource': title = (it.state === 'run' ? t('ag.creating', { kind: t('ag.kind.' + it.kind), id: it.id }) : it.state === 'ok' ? t('ag.created', { kind: t('ag.kind.' + it.kind), id: it.id }) : t('ag.create_failed', { kind: t('ag.kind.' + it.kind), id: it.id })); break;
    case 'diagnosis': {
      const dg = it.diagnosis;
      title = say('diagnosis', dg.code, dg.params, dg.code, 'title') + (it.attempt ? ` · ${t('ag.attempt', { n: it.attempt })}` : '');
      body = <><p className="sp">{say('diagnosis', dg.code, dg.params, dg.summary)}</p>
        {dg.evidence.length > 0 && <p className="hint">{t('ag.evidence')}: <span className="code">{dg.evidence.join(' · ')}</span></p>}
        <p className="sect">{t('ag.fix_options')}</p><ul className="stack" style={{ gap: 6 }}>{dg.fixes.map((f, i) => <li key={i} className="drow"><p>{say('fix', f.code, f.params, f.text)}</p><p className="hint">{say('fix', f.code, f.params, '', 'detail')}</p><span style={{ alignSelf: 'flex-start' }} className={'badge ' + (f.cost_delta_hourly > 0 ? 's-warn' : f.kind === 'manual' ? 's-pend' : 's-ok')}>{f.cost_delta_hourly > 0 ? t('ag.fix_paid') : f.kind === 'manual' ? t('ag.fix_manual') : t('ag.fix_free')}</span></li>)}</ul></>; break;
    }
    case 'handoff': {
      const h = it.handoff;
      title = h.state === 'waiting' ? t('ag.handoff_wait') : h.state === 'expired' ? t('ag.handoff_expired') : t('ag.handoff_' + (h.how ?? 'fix'));
      body = <><p className="sp">{h.state === 'waiting' ? t('ag.handoff_wait_text') : h.state === 'expired' ? t('ag.handoff_expired_text') : h.files.length ? t('ag.handoff_files', { files: h.files.join(', ') }) : t('ag.handoff_' + (h.how ?? 'fix') + '_text')}</p>
        {h.calls.length > 0 && <ul className="stack" style={{ gap: 2 }}>{h.calls.map((c, i) => <li key={i} className="hint row" style={{ gap: 6 }}><Icon name={c.ok ? 'check' : 'x'} />{t('ho.call.' + c.tool) === 'ho.call.' + c.tool ? c.tool : t('ho.call.' + c.tool)}</li>)}</ul>}</>; break;
    }
    case 'fix': title = it.declined ? t('ag.fix_declined') : it.auto ? t('ag.fix_auto') : t('ag.fix_applied'); body = <p className="sp">{say('fix', it.fix.code, it.fix.params, it.fix.fix)}</p>; break;
    case 'patch': title = t('ag.patch'); body = <pre className="term diff">{it.diff}</pre>; break;
    case 'test': title = t('ag.test', { name: it.test.name }); body = <p className="sp">{it.test.ok ? t('ag.test_ok', { url: it.test.url ?? it.test.app }) : t('ag.test_fail', { url: it.test.url ?? it.test.app })} <span className="code">{it.test.detail}</span></p>; break;
    case 'warning': title = t('ag.warning'); body = <p className="sp">{say('warning', it.code, it.params, it.message)}</p>; break;
    case 'agent': title = t('ag.agent'); body = <p className="sp">{it.text}</p>; break;
    case 'expired': title = t('ag.expired'); body = <p className="sp">{t('ap.expired')}</p>; break;
    case 'finished': title = it.ok ? t('ag.finished_ok') : it.cancelled ? t('ag.finished_cancel') : t('ag.finished_fail'); body = it.reasonCode ? <p className="sp">{say('outcome', it.reasonCode, it.reasonParams, '') || say('diagnosis', it.reasonCode, it.reasonParams, it.reasonCode)}</p> : null; break;
  }
  const rt = 'ts' in it ? it.ts - t0 : 0;
  void view;
  return (
    <li className="step">
      <span className="rail"><span className={'dot ' + d.tone}><Icon name={d.icon} /></span></span>
      <div className="sbody"><div className="sh"><h3 className="st">{title}</h3><span className="tm mono">{rel(rt)}</span></div>{body}</div>
    </li>
  );
}
