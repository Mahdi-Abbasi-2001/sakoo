import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import en from './en.json';
import fa from './fa.json';
import type { Lang } from '../api/types';

export type Theme = 'dark' | 'light';
type Params = Record<string, string | number | null | undefined>;
const CATALOGS: Record<Lang, Record<string, string>> = { en, fa };

/** Look a key up in one language. No fallback here: callers decide (the run page falls back to the backend's English text). */
export function lookup(lang: Lang, key: string): string | undefined { return CATALOGS[lang][key]; }

/** Fill {name} placeholders. Missing params stay visible as {name} so a gap is noticed in review, not hidden. */
export function fill(template: string, params?: Params): string {
  return template.replace(/\{(\w+)\}/g, (m, k) => (params && params[k] != null ? String(params[k]) : m));
}

/** key -> text in `lang`, then in English, then the key itself. */
export function translate(lang: Lang, key: string, params?: Params): string {
  return fill(lookup(lang, key) ?? lookup('en', key) ?? key, lang === 'fa' ? faParams(params) : params);
}

/** In Persian text a number is written with Persian digits, and a name made of Latin letters (a project, a file, an address) is wrapped in a
 *  left-to-right isolate so it keeps its own direction and does not scramble the sentence around it. */
const FA_NUM = new Intl.NumberFormat('fa-IR', { maximumFractionDigits: 2 });
const LRI = '\u2066', PDI = '\u2069';
export function faParams(params?: Params): Params | undefined {
  if (!params) return params;
  const out: Params = {};
  for (const [k, v] of Object.entries(params)) {
    if (typeof v === 'number') out[k] = FA_NUM.format(v);
    else if (typeof v === 'string' && /[A-Za-z]/.test(v) && !/[\u0600-\u06FF]/.test(v)) out[k] = LRI + v + PDI;
    else out[k] = v;
  }
  return out;
}

interface Ctx { lang: Lang; theme: Theme; t: (key: string, params?: Params) => string; setLang: (l: Lang) => void; setTheme: (t: Theme) => void; dir: 'ltr' | 'rtl' }
const I18nCtx = createContext<Ctx | null>(null);

function stored<T extends string>(key: string, ok: readonly T[], def: T): T {
  try { const v = localStorage.getItem(key); if (v && (ok as readonly string[]).includes(v)) return v as T; } catch { /* storage can be blocked */ }
  return def;
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => stored('lang', ['en', 'fa'] as const, 'en'));
  const [theme, setThemeState] = useState<Theme>(() => stored('theme', ['dark', 'light'] as const, 'dark'));
  const dir = lang === 'fa' ? 'rtl' : 'ltr';
  useEffect(() => { const h = document.documentElement; h.lang = lang; h.dir = dir; }, [lang, dir]);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const setLang = useCallback((l: Lang) => { setLangState(l); try { localStorage.setItem('lang', l); } catch { /* ignore */ } }, []);
  const setTheme = useCallback((t: Theme) => { setThemeState(t); try { localStorage.setItem('theme', t); } catch { /* ignore */ } }, []);
  const value = useMemo<Ctx>(() => ({ lang, theme, dir, setLang, setTheme, t: (k, p) => translate(lang, k, p) }), [lang, theme, dir, setLang, setTheme]);
  return <I18nCtx.Provider value={value}>{children}</I18nCtx.Provider>;
}

/** Sets the document title (the language follows the toggle). */
export function usePageTitle(key?: string, params?: Params) {
  const { lang } = useI18n();
  useEffect(() => { document.title = (key ? `${translate(lang, key, params)} · ${translate(lang, 'app.name')}` : translate(lang, 'app.name')).replace(/[\u2066-\u2069]/g, ''); }, [lang, key, JSON.stringify(params)]);   // eslint-disable-line react-hooks/exhaustive-deps
}

export function useI18n(): Ctx { const c = useContext(I18nCtx); if (!c) throw new Error('useI18n outside I18nProvider'); return c; }
