import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ProjectRow } from '../api/types';

/** Loads the project list. It checks again every 8 seconds while any run is going or waiting, and when the tab comes back. */
export function useProjects() {
  const [projects, setProjects] = useState<ProjectRow[] | null>(null);
  const [error, setError] = useState(false);
  const alive = useRef(true);
  const load = useCallback(async () => {
    try { const r = await api.projects(); if (alive.current) { setProjects(r.projects); setError(false); } } catch { if (alive.current) setError(true); }
  }, []);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load]);
  const busy = !!projects?.some((p) => p.last_run && (p.last_run.status === 'running' || p.last_run.status === 'waiting'));
  useEffect(() => {
    const tick = () => { if (!document.hidden) void load(); };
    const t = busy ? setInterval(tick, 8000) : undefined;
    document.addEventListener('visibilitychange', tick);
    return () => { if (t) clearInterval(t); document.removeEventListener('visibilitychange', tick); };
  }, [busy, load]);
  return { projects, error, reload: load };
}
