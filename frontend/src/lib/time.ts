import type { Lang } from '../api/types';

const loc = (lang: Lang) => (lang === 'fa' ? 'fa-IR' : 'en-GB');
/** "2 minutes ago", "yesterday", then a date and time once it is older than a week. Persian uses the Persian calendar, as its readers expect. */
export function when(lang: Lang, unixSeconds: number, nowMs = Date.now()): string {
  const diff = unixSeconds * 1000 - nowMs;                 // negative: in the past
  const abs = Math.abs(diff);
  const rtf = new Intl.RelativeTimeFormat(loc(lang), { numeric: 'auto' });
  if (abs < 45_000) return rtf.format(0, 'second');
  if (abs < 3_600_000) return rtf.format(Math.round(diff / 60_000), 'minute');
  if (abs < 86_400_000) return rtf.format(Math.round(diff / 3_600_000), 'hour');
  if (abs < 7 * 86_400_000) return rtf.format(Math.round(diff / 86_400_000), 'day');
  return new Intl.DateTimeFormat(loc(lang), { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(unixSeconds * 1000));
}
export const daysLeft = (unixSeconds: number | null | undefined, nowMs = Date.now()): number | null =>
  unixSeconds == null ? null : Math.max(0, Math.ceil((unixSeconds * 1000 - nowMs) / 86_400_000));
export const fileSize = (lang: Lang, bytes: number): string => {
  const mb = bytes / (1024 * 1024);
  const nf = new Intl.NumberFormat(loc(lang), { maximumFractionDigits: mb < 10 ? 1 : 0 });
  return mb >= 1 ? `${nf.format(mb)} MB` : `${nf.format(bytes / 1024)} KB`;
};
