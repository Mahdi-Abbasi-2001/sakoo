import { Icon } from '../../components/Icon';
import { useI18n } from '../../i18n';
import { unit } from '../format';
import { Progress } from './Progress';
import type { NodeState, PipeNode, RunViewState } from '../types';

const ICON: Record<NodeState, string> = { pending: 'dashed', running: 'loader', waiting: 'diamond', ok: 'check', fail: 'x', declined: 'diamond', skipped: 'dots' };
const TONE: Record<NodeState, string> = { pending: 's-pend', running: 's-run', waiting: 's-wait', ok: 's-ok', fail: 's-fail', declined: 's-warn', skipped: 's-pend' };

export function nodeSub(t: ReturnType<typeof useI18n>['t'], lang: 'en' | 'fa', n: PipeNode, view: RunViewState): string {
  switch (n.id) {
    case 'analyze': return n.state === 'pending' || n.state === 'running' ? '' : unit(t, lang, n.count ?? 0, 'manifest');
    case 'plan': return n.apps !== undefined ? t('pl.plan.sub', { apps: unit(t, lang, n.apps, 'app'), databases: unit(t, lang, n.databases ?? 0, 'database') }) : '';
    case 'confirm': return n.state === 'waiting' ? t('pl.confirm.waiting') : n.state === 'ok' ? (view.dryRun ? t('pl.confirm.auto') : t('pl.confirm.ok')) : n.state === 'declined' ? t('pl.confirm.declined') : '';
    case 'provision': { if (n.state === 'skipped' && !view.resources.some((r) => r.kind === 'database')) return t('pl.provision.none'); const c = view.resources.filter((r) => r.kind === 'database').length || n.subs.length; return c ? unit(t, lang, c, 'database') : ''; }
    case 'deploy': { const c = view.resources.filter((r) => r.kind === 'app').length || n.subs.length; return n.state === 'fail' && view.diagnoses.length ? view.diagnoses.at(-1)!.code : c ? unit(t, lang, c, 'app') : ''; }
    case 'verify': return view.tests.length ? unit(t, lang, view.tests.length, 'check') : t('pl.verify.after');
    case 'live': return n.url ? t('pl.live.url') : t('pl.live.none');
  }
}

export function Pipeline({ view, filter, onFilter }: { view: RunViewState; filter: string | null; onFilter: (id: string | null) => void }) {
  const { t, lang } = useI18n();
  const subsOf = (n: PipeNode) => {
    if (n.id !== 'provision' && n.id !== 'deploy') return [];
    const kind = n.id === 'provision' ? 'database' : 'app';
    const planned = view.resources.filter((r) => r.kind === kind);
    return planned.map((r) => ({ id: r.id, state: (n.subs.find((s) => s.id === r.id)?.state ?? (r.state === 'live' ? 'ok' : r.state === 'failed' ? 'fail' : r.state === 'creating' ? 'running' : 'pending')) as NodeState }));
  };
  return (
    <section className="card pipe-card" aria-label={t('pl.title')}>
      <h2 className="sr-only">{t('pl.title')}</h2>
      <div className="pipe-prog"><Progress view={view} />{filter && <button type="button" className="fchip" onClick={() => onFilter(null)}>{t('pl.all')}</button>}</div>
      <ol className="pipe">
        {view.pipeline.map((n, i) => {
          const sub = view.removed && n.id === 'live' ? t('res.removed') : nodeSub(t, lang, n, view); const subs = subsOf(n);
          const prev = view.pipeline[i - 1];
          const flow = prev && (prev.state === 'ok' && (n.state === 'running' || n.state === 'waiting' || n.state === 'ok'));
          const bad = prev && prev.state === 'ok' && n.state === 'fail';
          return (
            <li key={n.id} className={'pn ' + TONE[n.state]} data-state={n.state}>
              {i > 0 && <span className={'pl-link' + (flow ? ' flow ok' : bad ? ' flow fail' : '')} aria-hidden="true" />}
              <button type="button" className={'pn-b' + (filter === n.id ? ' on' : '')} aria-pressed={filter === n.id} onClick={() => onFilter(filter === n.id ? null : n.id)}>
                <span className={'pc ' + TONE[n.state]}><Icon name={ICON[n.state]} dashed={n.state === 'pending' && !!view.dryRun && (n.id === 'provision' || n.id === 'deploy')} /></span>
                <span className="pl">{t('pl.' + n.id)}</span>{' '}
                <span className="ps">{sub || ' '}</span>{' '}
                <span className="sr-only">{t('node.' + n.state)}</span>
              </button>
              {subs.length > 0 && <span className="subs">{subs.map((s) => <span key={s.id} className={'sub ' + TONE[s.state]}><Icon name={ICON[s.state]} />{s.id}</span>)}</span>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
