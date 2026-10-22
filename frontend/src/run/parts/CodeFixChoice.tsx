import { useState, type ReactNode } from 'react';
import { useI18n } from '../../i18n';
import { Icon } from '../../components/Icon';
import { useSay } from '../wording';
import type { OpenReq } from '../types';

type Answer = 'sakoo' | 'own_agent';

/** The deploy stopped because of an error in the person's code: who fixes it? Two ways, one click each, and the answer can be kept for the project.
 *  Sakoo: patches go to Sakoo's copy (never the person's files), the app is released again and checked, and the fix can go back to the repository.
 *  Own agent: the person's coding agent (Claude Code, Cursor) connects to Sakoo's MCP server, fixes the real code and tells Sakoo to continue. */
export function CodeFixChoice({ req, head, busy, err, onPick }: { req: OpenReq; head: ReactNode; busy: string | null; err: ReactNode; onPick: (answer: string) => void }) {
  const { t } = useI18n();
  const say = useSay();
  const [remember, setRemember] = useState(false);
  const p = (req.params ?? {}) as { diagnosis?: string; summary?: string; evidence?: string[]; location?: string; sakoo_can_fix?: boolean };
  const canSakoo = p.sakoo_can_fix !== false;
  const pick = (a: Answer) => onPick(remember ? `${a}_remember` : a);
  const what = say('diagnosis', p.diagnosis, { location: p.location ?? '' }, p.summary ?? req.text);
  return (
    <section className="ap appr cf" role="region" aria-labelledby="ap-title">
      {head}
      <h2 className="ap-title" id="ap-title">{say('question', req.code, req.params, req.text)}</h2>
      <div className="cf-what">
        <p className="ap-text">{what}</p>
        {p.location && <p className="hint"><span className="chip"><Icon name="file" /><b className="mono ltr" dir="ltr">{p.location}</b></span></p>}
        {(p.evidence ?? []).length > 0 && <pre className="term cf-ev ltr" dir="ltr">{(p.evidence ?? []).join('\n')}</pre>}
      </div>
      <div className="cf-grid" role="group" aria-label={t('ap.answer')}>
        <button type="button" className="cf-card cf-rec no-lift" disabled={!!busy || !canSakoo} aria-describedby="cf-a-d" onClick={() => pick('sakoo')}>
          <span className="cf-ic"><Icon name="shield" /></span>
          <span className="cf-t">{say('option', 'code_fix_choice.sakoo', req.params, t('cf.a.t'))}{canSakoo && <span className="badge s-ok cf-tag">{t('cf.recommended')}</span>}</span>
          <span className="cf-d" id="cf-a-d">{canSakoo ? t('cf.a.d') : t('cf.a.none')}</span>
          <ul className="cf-pts">{canSakoo && (['cf.a.p1', 'cf.a.p2', 'cf.a.p3'] as const).map((k) => <li key={k}><Icon name="check" />{t(k)}</li>)}</ul>
        </button>
        <button type="button" className="cf-card no-lift" disabled={!!busy} aria-describedby="cf-b-d" onClick={() => pick('own_agent')}>
          <span className="cf-ic"><Icon name="plug" /></span>
          <span className="cf-t">{say('option', 'code_fix_choice.own_agent', req.params, t('cf.b.t'))}</span>
          <span className="cf-d" id="cf-b-d">{t('cf.b.d')}</span>
          <ul className="cf-pts">{(['cf.b.p1', 'cf.b.p2', 'cf.b.p3'] as const).map((k) => <li key={k}><Icon name="check" />{t(k)}</li>)}</ul>
        </button>
      </div>
      <label className="cf-remember"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} disabled={!!busy} /> {t('cf.remember')}</label>
      {err}
      <p className="hint">{t('cf.note')}</p>
    </section>
  );
}
