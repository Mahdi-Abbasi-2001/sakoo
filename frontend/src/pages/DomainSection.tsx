import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../api/client';
import type * as G from '../api/schema.gen';
import { Icon } from '../components/Icon';
import { useI18n } from '../i18n';
import { errorText } from '../i18n/errors';

/** Connect a domain the developer owns. Every real change is a click on a button that says what it does, after a plan the person has read. */
export function DomainSection({ pid }: { pid: number }) {
  const { t, lang } = useI18n();
  const here = useRef<HTMLElement>(null);
  const { hash } = useLocation();
  const [list, setList] = useState<G.DomainsOut | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [domain, setDomain] = useState(''); const [liaraDns, setLiaraDns] = useState(false); const [zone, setZone] = useState(''); const [www, setWww] = useState(false);
  const [plan, setPlan] = useState<G.DomainPlanOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null); const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<Record<string, string>>({});
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setList(await api.domains(pid)); setLoadErr(null); } catch (e) { setLoadErr(errorText(lang, e)); setList(null); }
  }, [pid, lang]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (hash === '#domain') here.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }, [hash, list === null]);   // arriving from the run page's "Add a custom domain"

  const body = (): G.DomainPlanIn => ({ domain, use_liara_dns: liaraDns, zone: liaraDns && zone.trim() ? zone.trim() : null, www });
  const run = async (what: string, fn: () => Promise<void>) => { setBusy(what); setErr(null); try { await fn(); } catch (e) { setErr(errorText(lang, e)); } finally { setBusy(null); } };

  const review = () => run('plan', async () => { setPlan(await api.planDomain(pid, body())); });
  const connect = () => run('connect', async () => {
    await api.connectDomain(pid, { ...body(), confirm: true });
    setPlan(null); setDomain(''); setZone('');
    await load();
  });
  const check = (d: G.DomainOut) => run('check:' + d.id, async () => {
    const r = await api.checkDomain(pid, d.id);
    setNote((n) => ({ ...n, [d.id]: r.zone_check === 'busy' ? t('dom.check.busy') : r.zone_check === 'checked' ? t('dom.check.sent') : '' }));
    await load();
  });
  const ssl = (d: G.DomainOut) => run('ssl:' + d.id, async () => { await api.domainSsl(pid, d.id); setNote((n) => ({ ...n, [d.id]: t('dom.ssl.sent') })); await load(); });
  const detach = (d: G.DomainOut, removeRecords: boolean) => run('rm:' + d.id, async () => {
    const r = await api.detachDomain(pid, d.id, removeRecords);
    setInfo(r.records_removed ? t('dom.detach.removed', { n: String(r.records_removed) }) : t('dom.detach.done', { domain: d.name }));
    await load();
  });

  const noApp = loadErr !== null && !list;
  const loading = list === null && loadErr === null;
  const waiting = (list?.domains ?? []).some((d) => d.managed !== false && (!d.ready || !d.ssl_active));   // something is still settling: tell the person it takes time
  return (
    <section id="domain" ref={here} className="card pad stack" style={{ gap: 12 }} aria-label={t('dom.t')} aria-busy={loading}>
      <h2 className="card-t">{t('dom.t')}</h2>
      <p className="hint">{t('dom.d')}</p>
      {loading && <div className="stack" style={{ gap: 10 }} role="status" aria-label={t('dom.loading')}><div className="sk" style={{ height: 22, width: '45%' }} /><div className="sk" style={{ height: 110 }} /><div className="sk" style={{ height: 44 }} /></div>}
      {noApp && <p className="hint" role="status">{loadErr}</p>}
      {list && <>
        {waiting && <WaitNotice />}
        {list.domains.length === 0 && <p className="hint">{t('dom.empty')}</p>}
        {list.domains.map((d) => <DomainCard key={d.id} pid={pid} d={d} note={note[d.id]} busy={busy} onCheck={() => void check(d)} onSsl={() => void ssl(d)} onDetach={(rm) => void detach(d, rm)} />)}
        {info && <p role="status" className="hint">{info}</p>}

        {!plan ? (
          <form className="stack" style={{ gap: 10 }} onSubmit={(e) => { e.preventDefault(); void review(); }}>
            <div className="field"><label className="lbl" htmlFor="dom-name">{t('dom.input.lbl')}</label>
              <input id="dom-name" className="inp mono ltr" dir="ltr" placeholder="mydomain.com" value={domain} onChange={(e) => setDomain(e.target.value)} autoComplete="off" spellCheck={false} /></div>
            <fieldset className="stack" style={{ gap: 6, border: 0, padding: 0, margin: 0 }}>
              <legend className="lbl">{t('dom.mode.lbl')}</legend>
              <label className="row" style={{ gap: 8, flexWrap: 'nowrap' }}><input type="radio" name="dom-mode" checked={!liaraDns} onChange={() => setLiaraDns(false)} /><span>{t('dom.mode.own')}</span></label>
              <label className="row" style={{ gap: 8, flexWrap: 'nowrap' }}><input type="radio" name="dom-mode" checked={liaraDns} onChange={() => setLiaraDns(true)} /><span>{t('dom.mode.liara')}</span></label>
            </fieldset>
            {liaraDns && <div className="field"><label className="lbl" htmlFor="dom-zone">{t('dom.zone.lbl')}</label>
              <input id="dom-zone" className="inp mono ltr" dir="ltr" placeholder="mydomain.com" value={zone} onChange={(e) => setZone(e.target.value)} autoComplete="off" spellCheck={false} /><p className="hint">{t('dom.zone.d')}</p></div>}
            <label className="row" style={{ gap: 8, flexWrap: 'nowrap' }}><input type="checkbox" checked={www} onChange={(e) => setWww(e.target.checked)} /><span>{t('dom.www')}</span></label>
            <div><button type="submit" className="btn btn-p" disabled={!domain.trim() || busy !== null}>{busy === 'plan' ? t('dom.reviewing') : t('dom.review')}</button></div>
          </form>
        ) : (
          <div className="stack" style={{ gap: 10 }}>
            <b>{t('dom.plan.t', { domain: plan.domain })}</b>
            <ol className="stack" style={{ gap: 6, paddingInlineStart: 20, margin: 0 }}>
              {plan.steps.map((s) => <li key={s.code}>{stepText(t, s)}</li>)}
            </ol>
            {plan.already_attached && <p className="hint">{t('dom.plan.attached')}</p>}
            <WaitNotice />
            <div className="row">
              <button type="button" className="btn btn-p" disabled={busy !== null} onClick={() => void connect()}>{busy === 'connect' ? t('dom.connecting') : t('dom.plan.approve')}</button>
              <button type="button" className="btn" disabled={busy !== null} onClick={() => setPlan(null)}>{t('dom.plan.back')}</button>
            </div>
          </div>
        )}
        <div><a className="link" href={list.console_url} target="_blank" rel="noreferrer noopener">{t('dom.console')}</a></div>
      </>}
      {err && <div className="err" role="alert"><Icon name="alert" />{err}</div>}
    </section>
  );
}

function stepText(t: (k: string, p?: Record<string, string | number>) => string, s: G.DomainStepOut) {
  const p = Object.fromEntries(Object.entries(s.params ?? {}).map(([k, v]) => [k, typeof v === 'number' ? v : String(v)]));
  return t('msg.domain.step.' + s.code, p);
}

function RegistrarGuide({ pid, domain, open }: { pid: number; domain: string; open: boolean }) {
  const { t } = useI18n();
  const [g, setG] = useState<G.RegistrarGuideOut | null>(null);
  useEffect(() => { let dead = false; api.domainGuide(pid, domain).then((r) => { if (!dead) setG(r.guides[0] ?? null); }).catch(() => undefined); return () => { dead = true; }; }, [pid, domain]);
  if (!g) return null;
  return (
    <details open={open} className="stack" style={{ gap: 6 }}>
      <summary><b>{t('reg.t')}</b></summary>
      <p className="hint">{t(g.kind === 'irnic' ? 'reg.intro.irnic' : 'reg.intro.generic')}</p>
      <ol className="stack" style={{ gap: 6, paddingInlineStart: 20, margin: 0 }}>
        {g.steps.map((s, i) => <li key={i}>{t(`reg.step.${s.code}`, Object.fromEntries(Object.entries(s.params ?? {}).map(([k, v]) => [k, typeof v === 'number' ? v : String(v)])))}</li>)}
      </ol>
      {g.panel_url && <div><a className="btn btn-s" href={g.panel_url} target="_blank" rel="noreferrer noopener">nic.ir</a></div>}
      <p className="hint">{t('reg.note')}</p>
    </details>
  );
}

function DomainCard({ pid, d, note, busy, onCheck, onSsl, onDetach }: { pid: number; d: G.DomainOut; note?: string; busy: string | null; onCheck: () => void; onSsl: () => void; onDetach: (removeRecords: boolean) => void }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState('');
  const [confirmRm, setConfirmRm] = useState(false);
  const [dnsRes, setDnsRes] = useState<G.DnsCheckOut | 'loading' | 'error' | null>(null);
  const runDns = async () => { setDnsRes('loading'); try { setDnsRes(await api.dnsCheck(pid, d.id)); } catch { setDnsRes('error'); } };
  const [removal, setRemoval] = useState<G.RemovalPlanOut | null | 'loading'>(null); const [alsoRecords, setAlsoRecords] = useState(true);
  const startRemove = async () => {      // look at what could be cleaned up in the Liara DNS zone before asking
    setConfirmRm(true); setRemoval('loading');
    try { setRemoval(await api.domainRemoval(pid, d.id)); } catch { setRemoval(null); }
  };
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); setCopied(text); setTimeout(() => setCopied(''), 1500); } catch { /* blocked */ } };
  const records = d.records ?? [];
  const z = d.zone;
  return (
    <div className="card pad stack" style={{ gap: 10 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <b className="mono ltr" dir="ltr">{d.name}</b>
        <span className="row" style={{ gap: 6 }}>
          <span className={'badge ' + (d.ready ? 's-ok' : 's-wait')} title={t('dom.badge.domain.help')}><Icon name={d.ready ? 'check' : 'dashed'} />{t('dom.badge.domain', { status: d.status ?? t('dom.status.unknown') })}</span>
          <span className={'badge ' + (d.ssl_active ? 's-ok' : 's-pend')} title={t('dom.badge.cert.help')}><Icon name={d.ssl_active ? 'shield' : 'dashed'} />{t('dom.badge.cert', { status: d.certificates_status ?? t('dom.ssl.none') })}</span>
        </span>
      </div>
      {d.managed === false && <p className="hint">{t('dom.unmanaged')}</p>}
      <details><summary className="hint">{t('dom.details')}</summary><pre className="code ltr" dir="ltr" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: 0 }}>{JSON.stringify(d.details ?? {}, null, 1)}</pre></details>

      {z && !z.active && (
        <div className="stack" style={{ gap: 6 }}>
          <b>{t('dom.ns.t')}</b>
          <p className="hint">{t('dom.ns.d', { zone: z.name ?? '' })}</p>
          <div className="row">{(z.name_servers ?? []).map((n) => <span className="chip" key={n}><span className="ltr mono" dir="ltr">{n}</span></span>)}</div>
          <p className="hint">{t('dom.ns.state', { status: z.status ?? '?' })}</p>
        </div>
      )}

      {d.managed !== false && <RegistrarGuide pid={pid} domain={d.name} open={!d.ready} />}

      <div className="stack" style={{ gap: 6 }}>
        <b>{t('dom.records.t')}</b>
        {records.length === 0 ? <p className="hint">{t('dom.records.none')}</p> : <>
          <p className="hint">{t('dom.records.d')}</p>
          {records.map((r) => (
            <div className="rw" key={r.type + r.name} style={{ flexWrap: 'wrap' }}>
              <span className="badge">{r.type}</span>
              <span className="code ltr" dir="ltr">{r.name}</span>
              {r.value
                ? <><span className="code ltr" dir="ltr" style={{ overflowWrap: 'anywhere' }}>{r.value}</span>
                  <button type="button" className="btn btn-s" onClick={() => void copy(r.value)}>{copied === r.value ? t('log.copied') : t('live.copy')}</button></>
                : <span className="hint">{t('dom.value.console')}</span>}
              {r.derived && r.value && <span className="badge s-pend" title={t('dom.derived.help')}>{t('dom.derived')}</span>}
              {r.status && <span className={'badge ' + (r.status.toUpperCase() === 'VALID' ? 's-ok' : 's-fail')} title={t('dom.rec.status.help')}><Icon name={r.status.toUpperCase() === 'VALID' ? 'check' : 'x'} />{r.status}</span>}
            </div>
          ))}
        </>}
      </div>

      {d.console_url && <div><a className="link" href={d.console_url} target="_blank" rel="noreferrer noopener">{t('dom.record_status')}</a></div>}
      {note && <p role="status" className="hint">{note}</p>}
      {d.managed !== false && (
        <div className="row">
          <button type="button" className="btn" disabled={busy !== null} onClick={onCheck}>{busy === 'check:' + d.id ? t('dom.checking') : t('dom.check')}</button>
          <button type="button" className="btn btn-p" disabled={busy !== null || d.ssl_active} onClick={onSsl}>{busy === 'ssl:' + d.id ? t('dom.checking') : t('dom.ssl.btn')}</button>
          <button type="button" className="btn" disabled={busy !== null || dnsRes === 'loading'} onClick={() => void runDns()}>{dnsRes === 'loading' ? t('dom.checking') : t('dom.dns.btn')}</button>
          {d.ready && <a className="btn btn-s" href={'https://' + d.name} target="_blank" rel="noreferrer noopener">{t('live.open')}</a>}
          <span style={{ flex: 1 }} />
          {!confirmRm && <button type="button" className="link" onClick={() => void startRemove()}>{t('dom.detach')}</button>}
        </div>
      )}
      {dnsRes && dnsRes !== 'loading' && (dnsRes === 'error' ? <p className="hint" role="status">{t('dom.dns.error')}</p> : <DnsResult r={dnsRes} />)}
      {confirmRm && d.managed !== false && (
        <div className="stack" style={{ gap: 8 }} role="alertdialog" aria-label={t('dom.detach')}>
          <p>{t('dom.detach.confirm', { domain: d.name })}</p>
          {removal === 'loading' && <div className="sk" style={{ height: 40 }} role="status" aria-label={t('dom.loading')} />}
          {removal && removal !== 'loading' && removal.records.length > 0 && <>
            <label className="row" style={{ gap: 8, flexWrap: 'nowrap', alignItems: 'flex-start' }}><input type="checkbox" checked={alsoRecords} onChange={(e) => setAlsoRecords(e.target.checked)} /><span>{t('dom.detach.records', { n: String(removal.records.length), zone: removal.zone?.name ?? '' })}</span></label>
            <div className="stack" style={{ gap: 4 }}>{removal.records.map((r) => <div className="rw" key={r.name + r.type}><span className="badge">{r.type}</span><span className="code ltr" dir="ltr">{r.name}</span><span className="code ltr" dir="ltr" style={{ overflowWrap: 'anywhere' }}>{r.value}</span></div>)}</div>
          </>}
          {(removal === null || (removal !== 'loading' && removal.records.length === 0)) && <p className="hint">{t('dom.detach.elsewhere')}</p>}
          <div className="row">
            <button type="button" className="btn btn-d" disabled={busy !== null || removal === 'loading'} onClick={() => onDetach(alsoRecords && removal !== null && removal !== 'loading' && removal.records.length > 0)}>{t('dom.detach.yes')}</button>
            <button type="button" className="btn" onClick={() => setConfirmRm(false)}>{t('proj.rm.no')}</button>
          </div>
        </div>
      )}
    </div>
  );
}

/** DNS changes spread slowly and resolvers remember old answers: say so up front, so a domain that does not open yet is not mistaken for a failure. */
function WaitNotice() {
  const { t } = useI18n();
  return (
    <div className="card pad stack" role="note" style={{ gap: 6, borderColor: 'var(--warn, currentColor)' }}>
      <div className="row" style={{ gap: 8, flexWrap: 'nowrap', alignItems: 'flex-start' }}><Icon name="warn" className="ic warn" /><div className="stack" style={{ gap: 4 }}><b>{t('dom.wait.t')}</b><p className="hint">{t('dom.wait.d')}</p></div></div>
    </div>
  );
}

/** The verdict of a DNS check in words, with what each nameserver and public resolver said about each record. */
function DnsResult({ r }: { r: G.DnsCheckOut }) {
  const { t } = useI18n();
  const tone = r.verdict === 'all_good' ? 's-ok' : r.verdict === 'propagating' ? 's-wait' : 's-fail';
  const word = (s: string) => t('dom.dns.s.' + s);
  return (
    <div className="card pad stack" role="status" style={{ gap: 8 }}>
      <div className="row" style={{ gap: 8, flexWrap: 'nowrap', alignItems: 'flex-start' }}>
        <span className={'badge ' + tone}><Icon name={r.verdict === 'all_good' ? 'check' : r.verdict === 'propagating' ? 'dashed' : 'warn'} />{t('dom.dns.v.' + r.verdict + '.t')}</span>
        <p className="hint">{t('dom.dns.v.' + r.verdict, { ns: r.nameservers.join(', ') })}</p>
      </div>
      {r.records.map((x) => (
        <div className="stack" style={{ gap: 2 }} key={x.type + x.fqdn}>
          <div className="row" style={{ gap: 8 }}><span className="badge">{x.type}</span><span className="code ltr" dir="ltr">{x.fqdn}</span></div>
          <p className="hint">{t('dom.dns.at_source')}: {[...new Set(Object.values(x.authoritative as Record<string, string>))].map(word).join(', ') || '—'} · {Object.entries(x.public as Record<string, string>).map(([n, st]) => `${n}: ${word(st)}`).join(' · ')} · {t('dom.dns.local')}: {word(String(x.local))}</p>
        </div>
      ))}
    </div>
  );
}
