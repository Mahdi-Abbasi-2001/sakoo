import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { RunEvent } from '../api/events';
import { reduceRun } from './reduceRun';
import type { OpenReq } from './types';

export type DemoName = 'waiting' | 'failed' | 'dryrun';
const loaders: Record<DemoName, () => Promise<{ default: RunEvent[] }>> = {
  waiting: () => import('../demo/samples/events_waiting_for_approval.json') as never,
  failed: () => import('../demo/samples/events_build_timeout_then_fixed.json') as never,
  dryrun: () => import('../demo/samples/events_dry_run_compose.json') as never,
};
export const DEMOS: DemoName[] = ['waiting', 'failed', 'dryrun'];

/** Replays a recorded run through the same reducer a live stream feeds. No backend. Real gaps between events are kept, with a floor and a cap. */
export function useReplay(name: DemoName) {
  const [script, setScript] = useState<RunEvent[] | null>(null);
  const [n, setN] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const opened = useRef<number>(Date.now());

  useEffect(() => {
    let dead = false; setScript(null); setN(0); setNote(null);
    loaders[name]().then((m) => { if (dead) return; const evs = (m.default ?? m) as RunEvent[]; setScript(evs); setPlaying(!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches); const at = Number(new URLSearchParams(location.search).get('at'));   // /demo/waiting?at=10 jumps straight to event 10
      if (at >= 1 && at <= evs.length) setN(at); else if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) { setN(firstOpen(evs) ?? evs.length); } else setN(1); });
    return () => { dead = true; };
  }, [name]);

  const shown = useMemo(() => (script ? script.slice(0, n) : []), [script, n]);
  const view = useMemo(() => reduceRun(shown), [shown]);
  const waitingOn = view.openRequests.length > 0;

  useEffect(() => {
    if (!script || !playing || waitingOn || n >= script.length) return;
    const gap = (script[n].ts - script[n - 1]?.ts || 0) * 1000;
    const t = setTimeout(() => setN((x) => x + 1), Math.max(450, Math.min(gap, 4000)));
    return () => clearTimeout(t);
  }, [script, n, playing, waitingOn]);
  useEffect(() => { if (waitingOn) opened.current = Date.now(); }, [waitingOn, view.openRequests[0]?.eventId]);

  const synth = useCallback((kind: string, data: unknown): RunEvent => {
    const base = script?.[Math.min(n, (script?.length ?? 1)) - 1];
    return { id: (base?.id ?? 0) + 1, ts: (base?.ts ?? 0) + 1, kind, data } as RunEvent;
  }, [script, n]);

  /** Answer a request in the demo. Nothing is sent anywhere, and a typed secret is dropped on the spot. */
  const respond = useCallback(async (req: OpenReq, value: unknown) => {
    if (!script) return;
    const next = script[n];
    const accept = req.kind === 'confirm' ? value === true : true;
    const sampleAnswer = next && ((req.kind === 'confirm' && next.kind === 'confirm_response') || (req.kind === 'question' && next.kind === 'answered') || (req.kind === 'secret' && next.kind === 'secret_provided'));
    if (sampleAnswer && (req.kind !== 'confirm' || (next.data as { accepted: boolean }).accepted === accept)) { setPlaying(true); setN((x) => x + 1); return; }
    if (req.kind === 'confirm' && !accept) {
      const shown = script.slice(0, n);
      const tail: RunEvent[] = req.confirmKind === 'plan'
        ? [synth('phase_finished', { phase: 'confirm', approved: false }), synth('run_finished', { ok: false, dry_run: false, created: { apps: [], databases: [], database_ids: [] }, stopped: 'cancelled', reason_code: 'not_approved' })]
        : failedAfterDecline(shown, synth);        // a declined fix: the run ends as failed with its diagnosis and NO `stopped` field (docs/api.md)
      const evs = [...shown, synth('confirm_response', { id: req.id, accepted: false }), ...tail].map((e, i) => (i < shown.length ? e : { ...e, id: (shown[n - 1]?.id ?? 0) + (i - shown.length) + 1, ts: (shown[n - 1]?.ts ?? 0) + (i - shown.length) + 1 })) as RunEvent[];
      setScript(evs); setN(evs.length); return;
    }
    if (!next && req.kind === 'confirm' && accept && name === 'waiting') {
      // the sample ends at the approval: continue with the deploy events of the dry-run sample (same project, same resources)
      const dry = ((await loaders.dryrun()).default) as RunEvent[];
      const from = dry.findIndex((e) => e.kind === 'phase_started' && (e.data as { phase: string }).phase === 'deploy');
      const t0 = script[n - 1].ts;
      const tail = dry.slice(from).map((e, i) => ({ ...e, id: script[n - 1].id + 3 + i, ts: t0 + 2 + (e.ts - dry[from].ts) }));
      const head: RunEvent[] = [{ id: script[n - 1].id + 1, ts: t0 + 1, kind: 'confirm_response', data: { id: req.id, accepted: true } } as RunEvent, { id: script[n - 1].id + 2, ts: t0 + 1, kind: 'phase_finished', data: { phase: 'confirm', approved: true } } as RunEvent];
      setScript([...script, ...head, ...tail]); setNote('apply'); setPlaying(true); setN((x) => x + 1); return;
    }
    setPlaying(true); setN((x) => x + 1);
  }, [script, n, synth, name]);

  return {
    view, events: shown, total: script?.length ?? 0, n, loaded: !!script, playing, note, openedAt: opened.current,
    play: () => setPlaying(true), pause: () => setPlaying(false), restart: () => { setN(1); setPlaying(true); setNote(null); },
    step: () => { setPlaying(false); setN((x) => (script ? Math.min(script.length, x + 1) : x)); },
    respond,
  };
}
function firstOpen(evs: RunEvent[]): number | null { for (let i = 1; i <= evs.length; i++) if (reduceRun(evs.slice(0, i)).openRequests.length) return i; return null; }

/** What the backend sends when the user declines the only fix: fix_declined, then run_finished ok:false with the diagnosis and no `stopped`. */
function failedAfterDecline(shown: RunEvent[], synth: (kind: string, data: unknown) => RunEvent): RunEvent[] {
  const v = reduceRun(shown); const dg = v.diagnoses.at(-1); const f = dg?.fixes[0];
  const created = { apps: v.resources.filter((r) => r.kind === 'app' && r.state !== 'proposed').map((r) => r.id), databases: v.resources.filter((r) => r.kind === 'database' && r.state !== 'proposed').map((r) => r.id), database_ids: [] as string[] };
  return [
    synth('fix_declined', { fix: f?.text ?? '', note: 'declined', kind: f?.kind ?? 'resize', params: f?.params ?? {}, cost_delta_hourly: f?.cost_delta_hourly ?? 0, app: dg?.app ?? '', code: f?.code ?? '' }),
    synth('run_finished', { ok: false, dry_run: false, failed_step: `app:${dg?.app ?? ''}`, reason_code: dg?.code ?? null, reason_params: dg?.params ?? {}, created, diagnosis: dg ?? null, fixes: [], heal_attempts: v.healAttempts }),
  ];
}
