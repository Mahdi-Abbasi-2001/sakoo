import { useEffect, useRef, useState } from 'react';
import { useI18n } from '../i18n';
import { compact, money, signed } from './format';
import { useSay } from './wording';
import type { RunViewState } from './types';

/** What a screen reader hears. Two live regions that exist from the first render: an assertive one for a request that needs the person
 *  (it says what is asked, the price and what the choices are), and a polite one for the end of the run. Nothing else is announced, so a
 *  running log never talks over the person. The text is built from the same codes and params as the visible card. */
/** A sentence ends with a full stop even when the catalog's title is a bare phrase. */
/** Direction marks help the eye on the page; a screen reader has no use for them and some read them out. */
const plain = (s: string) => s.replace(/[\u2066-\u2069]/g, '');
const end = (s: string) => (/[.?!؟…]\s*$/.test(s) ? s : s + '.');

export function Announcer({ view, projectName, limitSeconds }: { view: RunViewState; projectName: string; limitSeconds: number }) {
  const { t, lang } = useI18n();
  const say = useSay();
  const req = view.openRequests[0];
  const [urgent, setUrgent] = useState('');
  const [polite, setPolite] = useState('');
  const key = req ? String(req.eventId) : '';
  const handoffId = !view.result && view.handoff?.state === 'waiting' ? view.handoff.id : '';
  const r = view.result;

  useEffect(() => {
    if (!req) { setUrgent(''); return; }
    const minutes = Math.round(limitSeconds / 60);
    let body: string;
    if (req.kind === 'confirm' && req.confirmKind === 'plan') {
      const title = end(say('confirm', req.code, req.params, req.prompt)), hourly = money(lang, view.money.plannedHourly ?? 0);
      // the sentence already carries the price in the shipped wording; add it only when the wording does not
      body = t('ann.plan', { title, price: title.includes(hourly) ? '' : t('ann.plan_price', { hourly, monthly: compact(lang, view.money.plannedMonthly ?? 0) }) + ' ', minutes });
    } else if (req.kind === 'confirm') {
      const fix = view.diagnoses.at(-1)?.fixes.find((f) => f.code === req.code);
      const delta = req.priceDeltaHourly ?? 0;
      body = t('ann.fix', { title: end(fix ? say('fix', fix.code, fix.params, fix.text) : say('confirm', req.code, req.params, req.prompt)), price: delta > 0 ? t('ann.fix_price', { delta: signed(lang, delta) }) : t('ann.fix_free'), minutes });
    } else if (req.kind === 'question') {
      body = t('ann.question', { text: end(say('question', req.code, req.params, req.text)), options: (req.options ?? []).map((o) => say('option', req.code ? `${req.code}.${o}` : null, req.params, o)).join(', ') });
    } else {
      body = t('ann.secret', { name: req.name ?? '' });
    }
    setUrgent(plain(body));
  }, [key, lang]);   // eslint-disable-line react-hooks/exhaustive-deps

  const doneKey = view.removed ? '' : view.status === 'interrupted' ? 'interrupted' : r ? `${r.ok}:${r.stopped}:${r.dryRun}` : '';
  useEffect(() => {
    if (!doneKey) { setPolite(''); return; }
    if (view.status === 'interrupted') { setPolite(plain(t('ann.interrupted'))); return; }
    if (!r) return;
    if (r.ok && !r.dryRun) {
      const passed = view.tests.filter((x) => x.ok).length;
      setPolite(plain(t('ann.live', { project: projectName, url: Object.values(r.urls)[0] ?? '', passed, total: view.tests.length })));
    } else if (r.ok) setPolite(plain(t('ann.preview')));
    else if (r.stopped === 'cancelled') setPolite(plain(t('ann.cancelled')));
    else {
      const code = r.diagnosis?.code ?? r.reasonCode;
      const why = say('outcome', r.reasonCode, r.reasonParams, '') || say('diagnosis', code, r.diagnosis?.params ?? r.reasonParams, r.diagnosis?.summary ?? code ?? '');
      const created = r.created.apps.length + r.created.databases.length;
      setPolite(plain(t('ann.failed', { why, created: created ? t('ann.failed_created', { names: [...r.created.apps, ...r.created.databases].join(', ') }) : t('fail.nothing_created') })));
    }
  }, [doneKey, lang]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (handoffId) setUrgent(plain(t('ann.handoff'))); }, [handoffId, lang]);   // eslint-disable-line react-hooks/exhaustive-deps

  const removedKey = view.removed ? String(view.removed.at ?? 'x') : '';
  useEffect(() => { if (removedKey) setPolite(plain(t('ann.removed'))); }, [removedKey, lang]);   // eslint-disable-line react-hooks/exhaustive-deps

  // a request the person has just answered: say so once, so silence is not mistaken for nothing happening
  const prevKind = useRef<string>('');
  const flip = useRef(false);
  useEffect(() => {
    if (prevKind.current && !key && !r && view.status !== 'interrupted') { flip.current = !flip.current; setPolite(plain(t('ann.sent.' + prevKind.current)) + (flip.current ? '\u00a0' : '')); }
    prevKind.current = req ? (req.kind === 'confirm' ? 'decision' : req.kind) : '';
  }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className="sr-only" role="alert" aria-live="assertive" aria-atomic="true" data-live="urgent">{urgent}</div>
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-live="polite">{polite}</div>
    </>
  );
}
