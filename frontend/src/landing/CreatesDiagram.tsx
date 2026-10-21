import { useEffect, useMemo, useRef } from 'react';
import sample from '../demo/samples/events_dry_run_compose.json';
import { useI18n } from '../i18n';

/** "What it creates on Liara": the resources of the sample run, drawn as a small private network. Each box has the icon and colour of its kind, the lines are the
 *  real connections of the plan (with the setting each one uses), and every box carries its price. It builds up in the order a run creates things, once,
 *  when it scrolls into view, then stays. With reduced motion it is drawn finished. Real data only: names, versions, prices and links come from the sample. */
type Kind = 'web' | 'worker' | 'db' | 'cache' | 'disk';
const ICON: Record<Kind, string> = {   // 24x24 line icons, as path data
  web: 'M4 12a8 8 0 1 0 16 0a8 8 0 1 0-16 0M4 12h16M12 4c3 3 3 13 0 16M12 4c-3 3-3 13 0 16',
  worker: 'M9 12a3 3 0 1 0 6 0a3 3 0 1 0-6 0M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1',
  db: 'M5 6a7 3 0 1 0 14 0a7 3 0 1 0-14 0M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3',
  cache: 'M13 3 5 13h6l-1 8 8-10h-6z',
  disk: 'M6 6h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zM15.3 12a1.2 1.2 0 1 0 2.4 0a1.2 1.2 0 1 0-2.4 0M7 12h5',
};
const COLOR: Record<Kind, string> = { web: 'var(--run)', worker: 'var(--run)', db: 'var(--wait)', cache: 'var(--warn)', disk: 'var(--ok)' };
const POS: Record<Kind, { x: number; y: number; w: number; h: number; at: number }> = {   // seconds: databases first, as a run creates them
  db: { x: 590, y: 56, w: 230, h: 104, at: 0.6 }, cache: { x: 590, y: 232, w: 230, h: 104, at: 1.3 },
  web: { x: 120, y: 232, w: 230, h: 104, at: 2.2 }, worker: { x: 120, y: 56, w: 230, h: 104, at: 3.0 }, disk: { x: 160, y: 350, w: 190, h: 44, at: 3.8 },
};
const END = 8.5;
const clamp = (x: number, a: number, b: number) => (x < a ? a : x > b ? b : x);
const ease = (p: number) => 1 - Math.pow(1 - p, 3);

function readSample() {
  const events: any[] = Array.isArray(sample) ? (sample as any[]) : ((sample as any).events ?? []);
  const pr = events.find((e) => e.kind === 'plan_ready')!.data;
  const line = (name: string) => pr.cost.lines.find((l: any) => l.name === name);
  const web = pr.plan.apps.find((a: any) => a.role === 'web'), worker = pr.plan.apps.find((a: any) => a.role === 'worker');
  const db = pr.plan.databases.find((d: any) => d.kind === 'postgres'), cache = pr.plan.databases.find((d: any) => d.kind === 'redis');
  const link = (from: string, to: string) => pr.plan.links.find((l: any) => l.from === from && l.to === to)?.env?.[0] as string;
  return {
    names: { web: web.name, worker: worker.name, db: db.id, cache: cache.id, disk: pr.disks[0].name } as Record<Kind, string>,
    hourly: { web: line(web.name).hourly_incl_vat, worker: line(worker.name).hourly_incl_vat, db: line(db.id).hourly_incl_vat, cache: line(cache.id).hourly_incl_vat } as Record<string, number>,
    port: web.port as number, dbVer: String(db.version).split('.')[0], cacheVer: String(cache.version).split('.')[0], mount: pr.disks[0].mount_to as string,
    env: { wd: link(worker.name, db.id), wdb: link(web.name, db.id), wc: link(web.name, cache.id) },
    totalH: pr.cost.total_hourly_incl_vat as number, totalM: pr.cost.total_monthly_incl_vat as number,
  };
}

export function CreatesDiagram({ reduce }: { reduce: boolean }) {
  const { t, lang } = useI18n();
  const d = useMemo(readSample, []);
  const nf = (n: number, frac = 0) => new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: frac }).format(n);
  const box = useRef<HTMLDivElement>(null);
  const svg = useRef<SVGSVGElement>(null);
  const subs: Record<Kind, string> = {
    web: t('land.net.web', { port: new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', { useGrouping: false }).format(d.port) }), worker: t('land.net.worker'), db: `PostgreSQL ${nf(+d.dbVer)}`, cache: `Redis ${nf(+d.cacheVer)}`, disk: t('land.net.disk', { path: d.mount }),
  };
  const links = [
    { id: 'wd', path: 'M350 108 L590 108', env: d.env.wd, lx: 470, ly: 98, at: 3.7 },
    { id: 'wdb', path: 'M350 262 C470 262 470 140 590 140', env: d.env.wdb, lx: 482, ly: 206, at: 4.1 },
    { id: 'wc', path: 'M350 296 L590 296', env: d.env.wc, lx: 470, ly: 286, at: 4.5 },
  ];
  useEffect(() => {
    const root = svg.current, wrap = box.current; if (!root || !wrap) return;
    const paths = [...root.querySelectorAll<SVGPathElement>('path[data-len]')];
    paths.forEach((p) => { p.dataset.len = String(p.getTotalLength()); });
    const dots = [...root.querySelectorAll<SVGCircleElement>('circle[data-flow]')];
    const th = wrap.querySelector<HTMLElement>('[data-tot=h]')!, tm = wrap.querySelector<HTMLElement>('[data-tot=m]')!;
    const draw = (time: number) => {
      root.querySelectorAll<SVGElement>('[data-at]').forEach((e) => {
        const p = ease(clamp((time - +e.dataset.at!) / (+(e.dataset.d ?? 0.5)), 0, 1));
        if (e.dataset.len) { e.style.strokeDasharray = e.dataset.len; e.style.strokeDashoffset = String(+e.dataset.len * (1 - p)); }
        else if (e.dataset.kind) { const q = POS[e.dataset.kind as Kind]; e.style.opacity = String(p); e.setAttribute('transform', `translate(${q.x},${q.y + (1 - p) * 14})`); }
        else e.style.opacity = String(p);
      });
      dots.forEach((c) => {
        const path = root.querySelector<SVGPathElement>(`path[data-id=${c.dataset.flow}]`)!, at = +c.dataset.at!, len = +path.dataset.len!;
        const on = time > at + 0.6; c.style.opacity = on ? '1' : '0';
        if (on) { const pt = path.getPointAtLength(((time * 0.22 + (at % 1)) % 1) * len); c.setAttribute('cx', String(pt.x)); c.setAttribute('cy', String(pt.y)); }
      });
      const v = ease(clamp((time - 5.2) / 2.2, 0, 1));
      th.textContent = nf(d.totalH * v); tm.textContent = nf(d.totalM * v);
    };
    if (reduce) { draw(END); return; }
    draw(0);
    let raf = 0, time = 0, last = 0, vis = false;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick); const dt = Math.min(0.1, (now - last) / 1000); last = now;
      if (!vis || document.hidden) return;
      time += dt; draw(Math.min(time, END + 60));   // after END it keeps drawing only so the dots keep flowing
    };
    const io = 'IntersectionObserver' in window ? new IntersectionObserver((es) => { vis = es[0].isIntersecting && es[0].intersectionRatio > 0.25; if (!vis) { time = 0; draw(0); } }, { threshold: [0, 0.25] }) : null;
    if (io) io.observe(wrap); else { vis = true; time = END; }
    raf = requestAnimationFrame(tick);
    return () => { cancelAnimationFrame(raf); io?.disconnect(); };
  }, [lang, reduce]);   // eslint-disable-line react-hooks/exhaustive-deps
  const alt = [d.names.web, d.names.worker, d.names.db, d.names.cache].map((n) => `${n}, ${nf(d.hourly[n === d.names.web ? 'web' : n === d.names.worker ? 'worker' : n === d.names.db ? 'db' : 'cache'], 0)} ${t('land.net.hr')}`).join('; ');
  const order: Kind[] = ['db', 'cache', 'web', 'worker', 'disk'];
  return (
    <div ref={box} className="crd">
      <svg ref={svg} viewBox="0 0 860 424" role="img" aria-label={`${t('land.net.alt')} ${alt}`}>
        <defs><radialGradient id="crd-glow" cx="50%" cy="50%" r="50%"><stop offset="0" style={{ stopColor: 'var(--accent)', stopOpacity: 0.3 }} /><stop offset="1" style={{ stopColor: 'var(--accent)', stopOpacity: 0 }} /></radialGradient></defs>
        <rect className="crd-net" x="92" y="18" width="760" height="392" rx="20" />
        <text className="crd-t crd-dim" x="112" y="44" fontSize="13">{t('land.w.netlabel')}</text>
        <g data-at="5" data-d="0.5"><rect x="2" y="276" width="78" height="40" rx="20" className="crd-pill" /><text className="crd-t" x="41" y="301" textAnchor="middle" fontSize="12.5">{t('land.net.internet')}</text></g>
        <path className="crd-link" d="M80 296 L120 296" data-at="5.2" data-d="0.4" data-len="40" />
        <path className="crd-link" d="M255 336 L255 350" data-at={POS.disk.at} data-d="0.3" data-len="14" />
        {links.map((l) => <g key={l.id}>
          <path className="crd-link" d={l.path} data-id={l.id} data-at={l.at} data-d="0.6" data-len="1" />
          <text className="crd-env" x={l.lx} y={l.ly} textAnchor="middle" data-at={l.at + 0.4} data-d="0.4">{l.env}</text>
          <circle r="4.5" className="crd-flow" data-flow={l.id} data-at={l.at} />
        </g>)}
        {order.map((k) => { const q = POS[k], big = k !== 'disk', cy = big ? q.h / 2 - 8 : q.h / 2, c = COLOR[k]; return (
          <g key={k} className="crd-node" data-kind={k} data-at={q.at} data-d="0.5" transform={`translate(${q.x},${q.y})`}>
            <rect className="crd-box" width={q.w} height={q.h} rx="14" />
            <circle cx="34" cy={cy} r={big ? 20 : 15} style={{ fill: c, fillOpacity: 0.14, stroke: c }} />
            <g transform={`translate(${34 - (big ? 12 : 9)},${cy - (big ? 12 : 9)}) scale(${big ? 1 : 0.75})`} fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ stroke: c }}><path d={ICON[k]} /></g>
            <text className="crd-t crd-mono" x="66" y={big ? cy - 6 : cy - 2} fontSize={big ? 15 : 13.5} fontWeight="600">{d.names[k]}</text>
            <text className="crd-t crd-dim" x="66" y={big ? cy + 16 : cy + 14} fontSize="12.5">{subs[k]}</text>
            {big && <circle cx={q.w - 18} cy="18" r="5" fill="var(--accent-2)" />}
            {big && <text className="crd-t crd-mono crd-price" x="66" y={cy + 40} fontSize="13" data-at={q.at + 0.6} data-d="0.4">{nf(d.hourly[k], 0)} {t('land.net.hr')}</text>}
          </g>); })}
      </svg>
      <ul className="crd-list" aria-label={t('land.net.alt')}>
        {order.map((k) => { const uses = links.filter((l) => (k === 'web' && l.id !== 'wd') || (k === 'worker' && l.id === 'wd')).map((l) => ({ env: l.env, to: l.id === 'wc' ? d.names.cache : d.names.db })); return (
          <li key={k} className="crd-li" style={{ ['--c' as string]: COLOR[k] }}>
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICON[k]} /></svg>
            <div className="crd-li-b"><b className="crd-mono">{d.names[k]}</b><span>{subs[k]}</span>
              {uses.map((u) => <span key={u.env + u.to} className="crd-use">{t('land.net.uses', { to: u.to, env: u.env })}</span>)}
              {k !== 'disk' && <span className="crd-mono crd-li-p">{nf(d.hourly[k], 0)} {t('land.net.hr')}</span>}</div>
          </li>); })}
      </ul>
      <div className="crd-tot"><div><b data-tot="h">0</b> <span>{t('land.unit_h')}</span></div><div><b data-tot="m">0</b> <span>{t('land.net.per_month')}</span></div></div>
      <p className="crd-note">{t('land.net.note')}</p>
    </div>
  );
}
