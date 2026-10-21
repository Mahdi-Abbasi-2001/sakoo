import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { RunDetail } from '../api/types';
import type { RunEvent } from '../api/events';
import { applyRemoval, reduceRun } from './reduceRun';
import type { OpenReq, Removal } from './types';

/** Every event kind in docs/events.d.ts. SSE delivers each kind as a named event, so each is listened to; an unknown kind is simply never heard. */
export const KINDS = ['run_started', 'phase_started', 'phase_finished', 'plan_ready', 'run_finished', 'agent_message', 'tool_call', 'plan_rejected', 'plan_updated', 'step_started', 'step_finished', 'resource_created', 'resource_updated', 'build', 'build_error', 'warning', 'test_result', 'heal_attempt', 'diagnosis', 'fix_applied', 'fix_declined', 'patch_ready', 'question', 'answered', 'confirm_request', 'confirm_response', 'secret_request', 'secret_provided', 'request_expired'] as const;
export type Conn = 'loading' | 'live' | 'reconnecting' | 'ended' | 'error' | 'missing';

export function useRunStream(runId: string) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [conn, setConn] = useState<Conn>('loading');
  const [removal, setRemoval] = useState<Removal | null>(null);   // the cleanup's own answer, until the run's cleaned_at is read again
  const last = useRef(0);
  const lastKindRef = useRef<string>('');
  const lastKind = () => lastKindRef.current;
  const es = useRef<EventSource | null>(null);

  const append = useCallback((incoming: RunEvent[]) => {
    const fresh = incoming.filter((e) => e.id > last.current).sort((a, b) => a.id - b.id);
    if (!fresh.length) return;
    last.current = fresh[fresh.length - 1].id; lastKindRef.current = fresh[fresh.length - 1].kind;
    setEvents((cur) => cur.concat(fresh));
  }, []);

  const loadDetail = useCallback(async () => { const d = await api.run(runId); setDetail(d); return d; }, [runId]);

  const catchUp = useCallback(async () => { const r = await api.runEvents(runId, last.current); append(r.events); }, [runId, append]);

  useEffect(() => {
    let dead = false, finished = false;
    let retry: ReturnType<typeof setTimeout> | undefined, poll: ReturnType<typeof setInterval> | undefined;
    const cleanup: (() => void)[] = [];
    last.current = 0; lastKindRef.current = ''; setEvents([]); setDetail(null); setRemoval(null); setConn('loading');

    const open = () => {
      if (dead || finished) return;
      const src = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream?after=${last.current}`, { withCredentials: true });
      es.current = src;
      src.onopen = () => setConn('live');
      src.onerror = () => {
        if (finished) return;
        if (src.readyState === EventSource.CLOSED) { setConn('error'); src.close(); retry = setTimeout(open, 1500); }   // the browser gave up: start a new stream from the last id
        else setConn('reconnecting');                                                                                // the browser retries and resends Last-Event-ID itself
        void catchUp().catch(() => { /* the next tick tries again */ });
      };
      for (const k of KINDS) src.addEventListener(k, (m) => {
        try {
          const ev = JSON.parse((m as MessageEvent).data) as RunEvent; append([ev]);
          if (ev.kind === 'run_finished') { finished = true; src.close(); setConn('ended'); void loadDetail(); }
        } catch { /* a broken frame is skipped; the safety poll below fills the gap */ }
      });
    };

    (async () => {
      try {
        const d = await loadDetail(); if (dead) return;
        await catchUp(); if (dead) return;
        if (d.status === 'succeeded' || d.status === 'failed' || d.status === 'cancelled' || d.status === 'interrupted') { finished = true; setConn('ended'); return; }
        setConn('live');          // the data is here: show the page now. A stream that is slow to open must not keep the skeleton up.
        open();
        // The stream is the fast path. This is the safety net for a stream that stalls without an error (a proxy that buffers, a laptop that slept):
        // ask for events after the last one we hold, every few seconds, and when the tab comes back.
        const tick = () => { if (dead || finished) return; catchUp().then(() => { if (!finished && lastKind() === 'run_finished') { finished = true; es.current?.close(); setConn('ended'); void loadDetail(); } }).catch(() => { /* offline: try later */ }); };
        poll = setInterval(tick, 3000);
        document.addEventListener('visibilitychange', tick); window.addEventListener('online', tick);
        cleanup.push(() => { document.removeEventListener('visibilitychange', tick); window.removeEventListener('online', tick); });
      } catch (e) { if (!dead) setConn(e instanceof ApiError && e.status === 404 ? 'missing' : 'error'); }
    })();
    return () => { dead = true; clearTimeout(retry); clearInterval(poll); cleanup.forEach((f) => f()); es.current?.close(); es.current = null; };
  }, [runId, loadDetail, catchUp, append]);

  const view = useMemo(() => {
    const v = reduceRun(events);
    if (detail?.status === 'interrupted' && v.status !== 'succeeded' && v.status !== 'failed' && v.status !== 'cancelled') v.status = 'interrupted';
    // a cleanup writes no event: the run row says when it happened (cleaned_at), and what went is what the run created
    const at = detail?.cleaned_at ?? removal?.at ?? null;
    if (at !== null || removal) {
      const c = v.result?.created;
      return applyRemoval(v, { at: at ?? removal?.at ?? null, apps: removal?.apps ?? c?.apps ?? [], databases: removal?.databases ?? c?.databases ?? [], buckets: removal?.buckets ?? c?.buckets ?? [], mail_servers: removal?.mail_servers ?? c?.mail_servers ?? [] });
    }
    return v;
  }, [events, detail?.status, detail?.cleaned_at, removal]);

  /** Called with the answer of a successful cleanup: the page changes at once, then the run row is read again for the real time. */
  const markCleaned = useCallback((deleted: { apps: string[]; databases: string[]; buckets?: string[]; mail_servers?: string[] }) => {
    setRemoval({ at: Math.floor(Date.now() / 1000), apps: deleted.apps, databases: deleted.databases, buckets: deleted.buckets, mail_servers: deleted.mail_servers });
    void loadDetail().catch(() => { /* the local answer stays */ });
  }, [loadDetail]);

  /** Answer an open request. The value goes to the server once; it is not kept anywhere here. */
  const respond = useCallback(async (req: OpenReq, value: unknown) => {
    try { await api.respond(runId, { request_id: req.id, value }); }
    catch (e) { if (e instanceof ApiError && (e.code === 'no_such_request' || e.status === 404)) { await catchUp(); throw e; } throw e; }
    // The answer is recorded. Do not wait for the stream to say so: fetch what the answer caused right away, and again a moment later
    // (the run needs a beat to write its next events), so a stream that is slow or silently dropped cannot leave the page on the old question.
    const fetchOnce = () => catchUp().then(() => { if (lastKindRef.current === 'run_finished') { es.current?.close(); setConn('ended'); void loadDetail(); } }).catch(() => { /* the safety poll continues */ });
    await fetchOnce(); setTimeout(fetchOnce, 700); setTimeout(fetchOnce, 2000);
  }, [runId, catchUp, loadDetail]);

  return { view, events, detail, conn, respond, markCleaned, refresh: async () => { await catchUp(); await loadDetail(); } };
}
