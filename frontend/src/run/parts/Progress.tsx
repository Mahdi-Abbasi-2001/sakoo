import { useI18n } from '../../i18n';
import { num } from '../format';
import type { RunViewState } from '../types';

/** How far the run has got: one segment per step, filled as steps finish, with a plain line saying where it is. Derived from the pipeline, never stored. */
export function Progress({ view }: { view: RunViewState }) {
  const { t, lang } = useI18n();
  const nodes = view.pipeline.filter((n) => !(n.id === 'provision' && n.state === 'skipped' && view.plan?.databases.length === 0) && !(view.dryRun && (n.id === 'provision' || n.id === 'deploy' || n.id === 'verify' || n.id === 'live')) || n.state !== 'pending');
  const total = nodes.length || 1;
  const finishedOk = !!view.result?.ok;
  const done = finishedOk ? total : nodes.filter((n) => n.state === 'ok').length;
  const cur = nodes.find((n) => n.state !== 'ok' && n.state !== 'skipped') ?? null;
  const pct = Math.round((done / total) * 100);
  const name = cur ? t('pl.' + cur.id) : '';
  let line: string;
  if (finishedOk) line = t('prog.done');
  else if (view.removed) line = t('prog.removed');
  else if (cur?.state === 'fail' || (view.result && !view.result.ok && view.result.stopped !== 'cancelled')) line = t('prog.failed', { step: name });
  else if (view.result?.stopped === 'cancelled' || cur?.state === 'declined') line = t('prog.stopped', { step: name });
  else if (cur?.state === 'waiting') line = t('prog.waiting', { step: name });
  else line = t('prog.now', { step: name });
  const tone = (st: string) => (st === 'ok' || finishedOk ? 'ok' : st === 'fail' ? 'fail' : st === 'running' ? 'run' : st === 'waiting' ? 'wait' : 'off');
  return (
    <div className="prog2">
      <div className="prog2-top"><b>{line}</b><span className="mono">{num(lang, pct)}{lang === 'fa' ? '٪' : '%'}</span></div>
      <div className="prog2-bar" role="progressbar" aria-label={t('prog.label')} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done} aria-valuetext={t('prog.steps', { done: num(lang, done), total: num(lang, total) })}>
        {nodes.map((n) => <i key={n.id} className={'seg-' + tone(n.state)} />)}
      </div>
      <div className="hint">{t('prog.steps', { done: num(lang, done), total: num(lang, total) })}</div>
    </div>
  );
}
