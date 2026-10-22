import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';
import { rovingKeys } from '../lib/roving';

type Choice = 'ask' | 'sakoo' | 'own_agent';

/** Who fixes an error in this project's code: asked each time, or remembered. The run page asks the first time and can keep the answer. */
export function CodeFixSection({ pid, current }: { pid: number; current: string | null | undefined }) {
  const { t, lang } = useI18n();
  const [value, setValue] = useState<Choice>((current as Choice) || 'ask');
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setValue((current as Choice) || 'ask'); }, [current]);
  const pick = async (c: Choice) => {
    const before = value; setValue(c); setErr(null);
    try { await api.setCodeFixChoice(pid, c === 'ask' ? null : c); } catch (e) { setValue(before); setErr(errorText(lang, e)); }
  };
  const options: { id: Choice; text: string }[] = [{ id: 'ask', text: t('cfs.ask') }, { id: 'sakoo', text: t('cfs.sakoo') }, { id: 'own_agent', text: t('cfs.own') }];
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-labelledby="cfs-h">
      <h2 className="sect" id="cfs-h">{t('cfs.t')}</h2>
      <p className="hint">{t('cfs.d')}</p>
      <div className="opts" role="radiogroup" aria-label={t('cfs.t')} style={{ maxWidth: 420 }} onKeyDown={rovingKeys('radio')}>
        {options.map((o) => <button type="button" role="radio" aria-checked={value === o.id} tabIndex={value === o.id ? 0 : -1} className="opt no-lift" key={o.id} onClick={() => void pick(o.id)}><span className="rd" />{o.text}</button>)}
      </div>
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}

/** What the agent changed in the code, to take back to the repository. Shown only when it changed something. */
export function FixPatchSection({ pid }: { pid: number }) {
  const { t } = useI18n();
  const [has, setHas] = useState(false);
  useEffect(() => { let live = true; fetch(`/api/projects/${pid}/fixes.patch`, { credentials: 'include' }).then((r) => { if (live) setHas(r.ok); }).catch(() => undefined); return () => { live = false; }; }, [pid]);
  if (!has) return null;
  return (
    <section className="card pad stack" style={{ gap: 10 }} aria-labelledby="fp-h">
      <h2 className="card-t" id="fp-h">{t('fp.t')}</h2>
      <p className="hint">{t('fp.d')}</p>
      <div><a className="btn" href={`/api/projects/${pid}/fixes.patch`} download>{t('fb.download')}</a></div>
    </section>
  );
}
