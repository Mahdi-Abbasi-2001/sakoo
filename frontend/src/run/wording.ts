import { useMemo } from 'react';
import type { Lang } from '../api/types';
import { useI18n } from '../i18n';
import { msgText, type MsgType } from '../i18n/messages';

type P = Record<string, unknown>;
/** The wording of a backend code: the catalog in the user's language, else the backend's own English text, else English from the catalog. */
export function makeSay(lang: Lang) {
  const say = (type: MsgType, code: string | null | undefined, params: P | undefined, fallback?: string, part?: 'title' | 'detail'): string =>
    msgText(lang, type, code, params ?? {}, part) ?? fallback ?? msgText('en', type, code, params ?? {}, part) ?? code ?? '';
  return say;
}
export function useSay() { const { lang } = useI18n(); return useMemo(() => makeSay(lang), [lang]); }
