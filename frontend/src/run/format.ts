import type { Lang } from '../api/types';

const cache = new Map<string, Intl.NumberFormat>();
const nf = (lang: Lang, opts: Intl.NumberFormatOptions = {}) => {
  const k = lang + JSON.stringify(opts); let f = cache.get(k);
  if (!f) { f = new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', opts); cache.set(k, f); }
  return f;
};
/** Whole toman (prices are shown rounded, VAT included). */
export const money = (lang: Lang, n: number) => nf(lang, { maximumFractionDigits: 0 }).format(Math.round(n));
export const money2 = (lang: Lang, n: number) => nf(lang, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
/** 3,300,000 -> 3.3M */
export const compact = (lang: Lang, n: number) => nf(lang, { notation: 'compact', maximumFractionDigits: 2 }).format(n);
export const signed = (lang: Lang, n: number) => (n >= 0 ? '+' : '−') + money(lang, Math.abs(n));
export const num = (lang: Lang, n: number) => nf(lang, {}).format(n);
export const clock = (lang: Lang, secs: number) => { const s = Math.max(0, Math.floor(secs)); const t = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; return lang === 'fa' ? t.replace(/\d/g, (d) => '۰۱۲۳۴۵۶۷۸۹'[+d]) : t; };
/** relative time of a log line, like +4.3 ms or +5.04 s, always left to right */
export const rel = (secs: number) => (secs < 1 ? `+${(secs * 1000).toFixed(1)} ms` : `+${secs.toFixed(2)} s`);
/** "2 apps", "1 app", "no apps": the number in the user's digits and the right form of the word (Persian has one form, and a word for none). */
export const unit = (t: (k: string) => string, lang: Lang, n: number, what: 'app' | 'database' | 'manifest' | 'check') =>
  n === 0 ? t(`unit.${what}.zero`) : `${num(lang, n)} ${t('unit.' + what + '.' + (n === 1 ? 'one' : 'other'))}`;
