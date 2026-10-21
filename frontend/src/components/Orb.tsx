import type { AgentState } from '../run/types';

const R = 13;           // core radius in a -34..34 box
const hex = Array.from({ length: 6 }, (_, i) => `${(R * Math.cos(((60 * i - 90) * Math.PI) / 180)).toFixed(1)},${(R * Math.sin(((60 * i - 90) * Math.PI) / 180)).toFixed(1)}`).join(' ');
const ARC = 2 * Math.PI * R * 1.3;
const parts = Array.from({ length: 8 }, (_, i) => { const a = ((45 * i + 10) * Math.PI) / 180, d = R * 3.1; return { dx: d * Math.cos(a), dy: d * Math.sin(a), delay: i * 0.21 }; });
const g = R * 0.34;

/** The agent orb: a hexagon core, an orbit ring, turning arcs, and one distinct glyph and motion per state. State changes restart the animation. */
export function Orb({ state, size = 64, label }: { state: AgentState; size?: number; label?: string }) {
  return (
    <svg className="orb" data-state={state} width={size} height={size} viewBox="-34 -34 68 68" role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} focusable="false">
      <circle className="o-halo" r={R * 1.9} />
      <circle className="o-ring" r={R * 1.62} />
      {parts.map((p, i) => <circle key={i} className="o-part" r={Math.max(1.6, R * 0.09)} style={{ ['--dx' as string]: `${p.dx.toFixed(1)}px`, ['--dy' as string]: `${p.dy.toFixed(1)}px`, animationDelay: `${p.delay}s` }} />)}
      <circle className="o-arcs" r={R * 1.3} strokeDasharray={`${(ARC * 0.2).toFixed(1)} ${(ARC * 0.133).toFixed(1)}`} />
      <polygon className="o-core" points={hex} />
      <g className="g-reading"><path className="gl" d={`M${-g} ${-g * 0.8}h${2 * g}M${-g} 0h${2 * g}M${-g} ${g * 0.8}h${1.3 * g}`} /></g>
      <g className="g-thinking">{[-g, 0, g].map((x) => <circle key={x} className="gl gl-fill" cx={x} r={R * 0.1} />)}</g>
      <g className="g-waiting"><path className="gl" d={`M0 ${-g * 1.1}L${g * 0.9} 0L0 ${g * 1.1}L${-g * 0.9} 0Z`} /></g>
      <g className="g-working"><path className="gl" d={`M${-g} ${-g * 0.8}l${g * 0.8} ${g * 0.8}l${-g * 0.8} ${g * 0.8}M${g * 0.1} ${-g * 0.8}l${g * 0.8} ${g * 0.8}l${-g * 0.8} ${g * 0.8}`} /></g>
      <g className="g-done"><path className="gl" d={`M${-g} ${g * 0.1}l${g * 0.7} ${g * 0.8}l${g * 1.4} ${-g * 1.6}`} /></g>
      <g className="g-failed"><path className="gl" d={`M${-g * 0.8} ${-g * 0.8}l${g * 1.6} ${g * 1.6}M${g * 0.8} ${-g * 0.8}l${-g * 1.6} ${g * 1.6}`} /></g>
      <circle className="o-burst" r={R * 1.1} />
    </svg>
  );
}
