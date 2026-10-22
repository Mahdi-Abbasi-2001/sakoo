import type { Lang } from '../api/types';
import { lookup, fill } from './index';

/** Count params that become a phrase with the right plural form: {apps} -> {apps_phrase} = "1 app", "2 apps", "no apps". */
const COUNTED: Record<string, 'app' | 'database' | 'manifest' | 'check' | 'envvar'> = { apps: 'app', databases: 'database', manifests: 'manifest', checks: 'check', count: 'envvar' };
export function countPhrase(lang: Lang, what: 'app' | 'database' | 'manifest' | 'check' | 'envvar', n: number): string {
  if (n === 0) return lookup(lang, `unit.${what}.zero`) ?? `0 ${what}s`;
  const w = lookup(lang, `unit.${what}.${n === 1 ? 'one' : 'other'}`) ?? what;
  return `${nfFor(lang).format(n)} ${w}`;
}

/** Params that name things (ports, versions, HTTP codes) are identifiers: never localized, always left to right. */
export const IDENT_PARAMS = new Set(['port', 'from_port', 'actual', 'expected', 'http', 'status', 'ports', 'chosen', 'service', 'app', 'name', 'names', 'key', 'file', 'source', 'target', 'image', 'hosts', 'module', 'append', 'plan', 'tier', 'tier_label', 'from_tier', 'from_tier_label', 'from_plan', 'kind', 'wanted', 'used', 'state', 'reason', 'question', 'question_code', 'answer', 'host', 'allowed', 'step', 'type', 'version', 'platform', 'signals', 'base_url', 'files', 'env', 'add_dependency', 'location', 'digest', 'steps']);

export type MsgType = 'note' | 'question' | 'option' | 'confirm' | 'warning' | 'diagnosis' | 'fix' | 'outcome' | 'readiness' | 'kindlabel' | 'how' | 'send';
export const msgKey = (type: MsgType, code: string, part?: 'title' | 'detail') => `msg.${type}.${code}${part ? '.' + part : ''}`;

type P = Record<string, unknown>;
const nfFor = (lang: Lang) => new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: 2 });
const iso = (s: string) => '\u2066' + s + '\u2069';     // left-to-right isolate: a name inside Persian text keeps its own direction
export function paramText(lang: Lang, key: string, v: unknown): string {
  const ident = IDENT_PARAMS.has(key);
  if (Array.isArray(v)) { const s = v.map((x) => String(x)).join(', '); return ident && lang === 'fa' ? iso(s) : s; }
  if (typeof v === 'number') return ident ? (lang === 'fa' ? iso(String(v)) : String(v)) : nfFor(lang).format(v);
  if (v === null || v === undefined) return '';
  return ident && lang === 'fa' ? iso(String(v)) : String(v);
}

/** The wording for a backend code in `lang`, or `undefined` when the catalog has none (the caller then shows the backend's English text). */
export function msgText(lang: Lang, type: MsgType, code: string | null | undefined, params: P = {}, part?: 'title' | 'detail'): string | undefined {
  if (!code) return undefined;
  const tpl = lookup(lang, msgKey(type, code, part)) ?? (lang === 'fa' ? undefined : undefined);
  if (tpl === undefined) return undefined;
  const flat: Record<string, string> = {};
  for (const [k, v] of Object.entries(params)) { flat[k] = paramText(lang, k, v); if (COUNTED[k] && typeof v === 'number') flat[k + '_phrase'] = countPhrase(lang, COUNTED[k], v); }
  return fill(tpl, flat);
}

/** Same, but English counts as translated and a missing Persian entry falls back to English before the backend's text. */
export function msgTextOrEnglish(lang: Lang, type: MsgType, code: string | null | undefined, params: P = {}, part?: 'title' | 'detail'): string | undefined {
  return msgText(lang, type, code, params, part) ?? (lang === 'en' ? undefined : msgText('en', type, code, params, part));
}

/** Plan source line and planned requests: worded from the code, the backend's English kept as the fallback. */
export const howText = (lang: Lang, code: string | null | undefined, params: P, fallback: string) => msgText(lang, 'how', code, params) ?? fallback;
export const sendText = (lang: Lang, w: { op?: string; params?: P; summary: string }) => msgText(lang, 'send', w.op, w.params ?? {}) ?? w.summary;
