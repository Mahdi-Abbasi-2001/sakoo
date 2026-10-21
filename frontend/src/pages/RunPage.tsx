import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { api } from '../api/client';
import type { Limits, LiveInfo } from '../api/types';
import { useI18n } from '../i18n';
import { RunView } from '../run/RunView';
import { RunSkeleton } from '../run/Skeleton';
import { useRunStream } from '../run/useRunStream';
import { Icon } from '../components/Icon';

export function RunPage() {
  const { id = '' } = useParams();
  const { t } = useI18n();
  const { view, detail, conn, respond, refresh, markCleaned } = useRunStream(id);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [live, setLive] = useState<LiveInfo | null>(null);
  useEffect(() => { api.limits().then(setLimits).catch(() => setLimits(null)); }, []);
  useEffect(() => {
    if (!detail || view.status !== 'succeeded' || view.result?.dryRun) return;
    api.project(detail.project_id).then((p) => setLive(p.live ?? null)).catch(() => setLive(null));
  }, [detail?.project_id, view.status, view.result?.dryRun]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (conn === 'missing') return <div className="page-narrow" style={{ margin: '0 auto' }}><div className="card pad stack"><Icon name="alert" /><h1 className="ap-title" tabIndex={-1}>{t('run.not_found')}</h1></div></div>;
  if (conn === 'loading' || !detail) return <RunSkeleton />;
  const freeDays = !view.removed && live?.free_address_expires_at ? Math.max(0, Math.ceil((live.free_address_expires_at * 1000 - Date.now()) / 86400000)) : null;
  return (
    <RunView view={view} conn={conn} runId={id} projectId={detail.project_id} projectName={detail.project ?? view.project ?? '—'}
      onRespond={respond} limitSeconds={limits?.approval_timeout_seconds ?? 900} freeDays={freeDays} maxAttempts={limits?.max_heal_attempts ?? null} onRefresh={() => void refresh()} onCleaned={markCleaned} />
  );
}
