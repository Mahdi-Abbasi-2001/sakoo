import type { Lang, RunRow, LastRun } from '../api/types';
import { lookup, fill, translate } from '../i18n';
import { msgText } from '../i18n/messages';

export interface RunLine { tone: string; icon: string; label: string; sub?: string; text: string; code?: string }
type RunLike = (Pick<RunRow, 'status' | 'mode' | 'result'> | Pick<LastRun, 'status' | 'mode' | 'result'>) & { cleaned_at?: number | null };

const names = (c?: { apps?: string[]; databases?: string[] } | null) => [...(c?.apps ?? []), ...(c?.databases ?? [])];

/** How one run is told in a list: a status badge (colour, shape and word), and one sentence worded from the run's own result codes. */
export function describeRun(lang: Lang, run: RunLike, opts: { current?: boolean } = {}): RunLine {
  const t = (k: string, p?: Record<string, string | number | null | undefined>) => translate(lang, k, p);
  const r = run.result ?? null;
  // a cleanup deletes what the run created: the run stays in the list, told as removed, never as live
  if (run.cleaned_at && run.mode !== 'preview' && (run.status === 'succeeded' || run.status === 'failed')) return { tone: 's-pend', icon: 'dashed', label: t('status.removed'), text: t('proj.res.removed', { names: names(r?.created).join(', ') }) };
  switch (run.status) {
    case 'waiting': return { tone: 's-wait', icon: 'diamond', label: t('status.waiting'), text: t('proj.res.waiting') };
    case 'running': return { tone: 's-run', icon: 'loader', label: t('status.running'), text: t('proj.res.running') };
    case 'cancelled': return { tone: 's-pend', icon: 'diamond', label: t('status.cancelled'), text: msgText(lang, 'outcome', 'not_approved') ?? t('status.cancelled'), code: 'not_approved' };
    case 'interrupted': return { tone: 's-warn', icon: 'warn', label: t('status.interrupted'), text: t('proj.res.interrupted') };
    case 'succeeded': {
      if (r?.dry_run || run.mode === 'preview') return { tone: 's-ok', icon: 'check', label: t('proj.status.preview'), sub: t('proj.preview.sub'), text: t('proj.res.preview') };
      const fixes = (r?.fixes ?? []).map((f) => msgText(lang, 'fix', f.code, (f.params ?? {}) as Record<string, unknown>) ?? f.code).filter(Boolean);
      return { tone: 's-ok', icon: 'check', label: t('status.succeeded'), sub: opts.current ? t('proj.current') : undefined,
        text: [t('proj.res.created', { names: names(r?.created).join(', ') }), fixes.length ? t('proj.res.fixed', { fixes: fixes.join('; ') }) : ''].filter(Boolean).join(' ') };
    }
    case 'failed': default: {
      const code = r?.diagnosis_code ?? r?.reason_code ?? null;
      const params = (r?.reason_params ?? {}) as Record<string, unknown>;
      const worded = code ? (msgText(lang, 'outcome', code, params) ?? msgText(lang, 'diagnosis', code, params) ?? ((l) => (l ? fill(l, Object.fromEntries(Object.entries(params).map(([k, v]) => [k, Array.isArray(v) ? v.join(', ') : String(v)]))) : undefined))(lookup(lang, 'err.' + code))) : undefined;
      const made = names(r?.created);
      return { tone: 's-fail', icon: 'x', label: t('status.failed'), code: code ?? undefined,
        text: [worded ?? t('err.unknown'), made.length ? t('proj.res.created_before', { names: made.join(', ') }) : ''].filter(Boolean).join(' ') };
    }
  }
}
