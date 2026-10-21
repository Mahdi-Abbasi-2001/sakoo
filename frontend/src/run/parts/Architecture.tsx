import { Icon } from '../../components/Icon';
import { Ticker } from '../../components/Ticker';
import { useI18n } from '../../i18n';
import { money } from '../format';
import type { Resource, RunViewState } from '../types';

const STATE_ICON: Record<Resource['state'], string> = { proposed: 'dashed', creating: 'loader', live: 'check', failed: 'x', removed: 'dashed' };
const STATE_TONE: Record<Resource['state'], string> = { proposed: 's-pend', creating: 's-run', live: 's-ok', failed: 's-fail', removed: 's-pend' };

function line2(t: (k: string, p?: Record<string, string | number>) => string, r: Resource) {
  if (r.kind === 'disk') return `${t('res.disk')} · ${r.mountTo ?? ''}`;
  if (r.kind === 'database') return `${r.type ?? ''}${r.version ? ' ' + r.version : ''}`;
  if (r.kind === 'bucket' || r.kind === 'mail') return `${t('proj.kind.' + r.kind)} · ${r.type ?? ''}`;
  return [r.type, r.role ? t('res.role_' + r.role) : null, r.port ? `:${r.port}` : t('res.no_port')].filter(Boolean).join(' · ');
}

/** Architecture: proposed resources are dashed, created ones solid, edges are labelled with the variable that links them. */
export function Architecture({ view }: { view: RunViewState }) {
  const { t, lang } = useI18n();
  const apps = view.resources.filter((r) => r.kind === 'app');
  const dbs = view.resources.filter((r) => r.kind === 'database' || r.kind === 'bucket' || r.kind === 'mail');   // services the apps talk to share one column
  const disks = view.resources.filter((r) => r.kind === 'disk');
  const total = view.money.plannedHourly ?? view.money.currentHourly;
  // SVG layout: internet | apps (+ their disks) | databases
  const rtl = lang === 'fa';
  const NW = 210, NH = 80, DH = 64, GAP = 16, X0 = 28, X1 = 262, X2 = 560, W = 780;   // 186 px of text per node fits the longest line (Persian worker: 180); 88 px between columns fits a variable name
  const L = (x: number, w = NW) => (rtl ? W - x - w : x);      // mirrored in Persian: the internet is on the right, flow runs leftward
  const appY = new Map<string, number>(); let y = 44;
  for (const a of apps) { appY.set(a.id, y); y += NH + GAP; const d = disks.filter((x) => x.ownerApp === a.id); y += d.length * (DH + 10); }
  const dbY = new Map<string, number>(); let yd = 44; for (const d of dbs) { dbY.set(d.id, yd); yd += NH + GAP; }
  const H = Math.max(y, yd, 160) + 6;
  const pos = (id: string): { x: number; y: number; h: number } | null => {
    if (id === 'internet') return { x: L(X0, NW - 40), y: (appY.values().next().value ?? 44) as number, h: NH };
    if (appY.has(id)) return { x: L(X1), y: appY.get(id)!, h: NH };
    if (dbY.has(id)) return { x: L(X2), y: dbY.get(id)!, h: NH };
    return null;
  };
  const node = (r: Resource, x: number, yy: number, h: number) => {
    const dashed = r.state === 'proposed' || (r.kind === 'disk' && r.state !== 'live');
    return (
      <g key={r.id} className={'an ' + STATE_TONE[r.state] + (dashed ? ' dashed' : ' solid')} transform={`translate(${L(x)} ${yy})`}>
        <rect width={NW} height={h} rx="10" />
        <text className="r1" x={rtl ? NW - 12 : 12} textAnchor={rtl ? 'end' : 'start'} y="21" style={r.id.length > 22 ? { fontSize: 10.5 } : r.id.length > 17 ? { fontSize: 12 } : undefined}>{r.id}</text>
        <text className="r2" x={rtl ? NW - 12 : 12} textAnchor={rtl ? 'end' : 'start'} y="39">{line2(t, r)}</text>
        {r.kind === 'disk' ? <text className="r3 unk" x={rtl ? NW - 12 : 12} textAnchor={rtl ? 'end' : 'start'} y="55">{t('ar.unverified')}</text> : <text className="r3" x={rtl ? NW - 12 : 12} textAnchor={rtl ? 'end' : 'start'} y="56">{r.hourly !== null ? <><tspan className="pv">{money(lang, r.hourly)}</tspan><tspan className="pu"> {t('ar.hr')}</tspan></> : t('res.price_unknown')}{r.tierLabel ? <tspan className="pu"> · {r.tierLabel}</tspan> : null}</text>}
        <text className="stw" x={r.kind === 'disk' ? (rtl ? 12 : NW - 12) : (rtl ? NW - 12 : 12)} textAnchor={r.kind === 'disk' ? (rtl ? 'start' : 'end') : (rtl ? 'end' : 'start')} y={r.kind === 'disk' ? 21 : 72}>{t('res.' + r.state)}</text>
      </g>
    );
  };
  return (
    <section className="card arch-card" aria-label={t('ar.title')}>
      <div className="card-h"><div><h2 className="card-t">{t('ar.title')}</h2><p className="hint">{t('ar.hint')}</p></div>
        <span className="cost"><i>{t('ar.total')}</i> <b>≈ <Ticker value={total} format={(n) => money(lang, n)} /> {t('ar.hr')}</b> <i>{t('ap.incl_vat')}</i></span></div>
      <div className="arch-body">
        <svg className="arch-svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t('ar.title')}>
          <rect className="netbox" x={rtl ? 6 : 236} y="6" width="538" height={H - 12} rx="14" />
          <text className="netlbl" x={rtl ? 510 : 250} textAnchor={rtl ? 'end' : 'start'} y="24">{view.network ? t(view.networkNew ? 'ar.network_new' : 'ar.network', { name: view.network }) : ''}</text>
          {(() => {
            // every line first, then every label, so no line is ever drawn over a label
            const geo = view.edges.map((e, i) => {
              const a = pos(e.from), b = pos(e.to); if (!a || !b) return null;
              const aw = e.from === 'internet' ? NW - 40 : NW;
              const ax = rtl ? a.x : a.x + aw, ay = a.y + a.h / 2, bx = rtl ? b.x + NW : b.x, by = b.y + b.h / 2;
              const mx = (ax + bx) / 2; const live = (view.resources.find((r) => r.id === e.to)?.state === 'live') && (e.from === 'internet' || view.resources.find((r) => r.id === e.from)?.state === 'live');
              return { e, i, ax, ay, bx, by, mx, live };
            }).filter((g): g is NonNullable<typeof g> => g !== null);
            return <>
              {geo.map((g) => <path key={'p' + g.i} className={'ed' + (g.live ? ' on flow' : '') + (g.e.inferred ? ' inf' : '')} d={`M${g.ax} ${g.ay} C${g.mx} ${g.ay} ${g.mx} ${g.by} ${g.bx} ${g.by}`} />)}
              {geo.map((g) => <text key={'l' + g.i} className="el" x={g.mx} y={(g.ay + g.by) / 2 - 6 + (g.i % 2) * 14} textAnchor="middle">{g.e.label}</text>)}
            </>;
          })()}
          <g className="an solid inet" transform={`translate(${L(X0, NW - 40)} ${pos('internet')?.y ?? 44})`}><rect width={NW - 40} height={NH} rx="10" /><text className="r1" x={rtl ? NW - 52 : 12} textAnchor={rtl ? 'end' : 'start'} y="30">{t('ar.internet')}</text><text className="r2" x={rtl ? NW - 52 : 12} textAnchor={rtl ? 'end' : 'start'} y="50">HTTPS</text></g>
          {apps.map((a) => node(a, X1, appY.get(a.id)!, NH))}
          {apps.flatMap((a) => { let yy = appY.get(a.id)! + NH + 10; return disks.filter((d) => d.ownerApp === a.id).map((d) => { const el = node(d, X1, yy, DH); yy += DH + 10; return el; }); })}
          {dbs.map((d) => node(d, X2, dbY.get(d.id)!, NH))}
        </svg>
        <ul className="arch-list">
          {[...apps, ...dbs, ...disks].map((r) => (
            <li key={r.id} className={STATE_TONE[r.state]}>
              <span className={'pc ' + STATE_TONE[r.state]}><Icon name={STATE_ICON[r.state]} /></span>
              <span className="mono"><b>{r.id}</b><span className="hint"> {line2(t, r)}</span></span>
              <span className="cost">{r.hourly !== null ? <>{money(lang, r.hourly)} <i>{t('ar.hr')}</i></> : <i>{t('ap.included')}</i>}</span>
              <span className="sr-only">{t('res.' + r.state)}</span>
            </li>
          ))}
          {view.edges.filter((e) => e.from !== 'internet').map((e, i) => <li key={'e' + i} className="edge-li hint"><span className="mono">{e.from} → {e.to}</span> <span className="code">{e.label}</span></li>)}
        </ul>
      </div>
    </section>
  );
}
