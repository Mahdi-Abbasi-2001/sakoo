import { ApiError } from '../api/client';
import { lookup, fill } from './index';
import type { Lang } from '../api/types';

/** Localize an error by its code. Falls back to the backend's English message, then to a generic line. */
export function errorText(lang: Lang, e: unknown): string {
  if (e instanceof ApiError) {
    const p = Object.fromEntries(Object.entries(e.params).map(([k, v]) => [k, Array.isArray(v) ? v.join(', ') : (v as string | number)]));
    const hit = lookup(lang, 'err.' + e.code);
    if (hit) return fill(hit, p);
    if (e.message && e.message !== e.code) return e.message;
  }
  return lookup(lang, 'err.unknown') ?? 'Something went wrong.';
}
