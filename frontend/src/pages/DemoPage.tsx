import { Link, Navigate, useParams } from 'react-router-dom';
import { useI18n } from '../i18n';
import { RunView } from '../run/RunView';
import { RunSkeleton } from '../run/Skeleton';
import { DEMOS, useReplay, type DemoName } from '../run/useReplay';

/** /demo/waiting, /demo/failed, /demo/dryrun: a recorded run replayed through the same reducer, with no backend. */
export function DemoPage() {
  const { name = '' } = useParams();
  if (!(DEMOS as string[]).includes(name)) return <Navigate to="/demo/waiting" replace />;
  return <Demo name={name as DemoName} key={name} />;
}

function Demo({ name }: { name: DemoName }) {
  const { t } = useI18n();
  const r = useReplay(name);
  if (!r.loaded) return <RunSkeleton />;
  return (
    <>
      <div className="demo-bar" role="region" aria-label={t('demo.list')}>
        <p className="hint">{t('demo.banner')}{r.note ? ' ' + t('demo.apply_note') : ''}</p>
        <div className="row">
          <nav className="row" aria-label={t('demo.list')}>{DEMOS.map((d) => <Link key={d} className={'fchip' + (d === name ? ' on' : '')} aria-current={d === name ? 'page' : undefined} to={`/demo/${d}`}>{t('demo.' + d)}</Link>)}</nav>
          <span style={{ flex: 1 }} />
          <span className="hint mono">{t('demo.at', { n: r.n, total: r.total })}</span>
          <button type="button" className="btn" onClick={r.playing ? r.pause : r.play}>{r.playing ? t('demo.pause') : t('demo.play')}</button>
          <button type="button" className="btn" onClick={r.step}>{t('demo.step')}</button>
          <button type="button" className="btn" onClick={r.restart}>{t('demo.reset')}</button>
        </div>
      </div>
      <RunView view={r.view} conn="live" runId={`demo-${name}`} projectName={r.view.project ?? '—'} onRespond={r.respond} limitSeconds={900} startedAt={r.openedAt} freeDays={7} demo />
    </>
  );
}
