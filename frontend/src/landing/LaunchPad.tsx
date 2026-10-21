import { useEffect, useRef } from 'react';
import { useI18n } from '../i18n';

/** The hero picture: a project block drops onto a glowing platform (سکو), the agent's findings orbit it, a beam rises and a live address appears.
 *  Pure SVG and CSS on one 10 s loop. With reduced motion it shows the last frame; off screen it pauses. Colours come from the theme tokens. */
export function LaunchPad({ reduce }: { reduce: boolean }) {
  const { t } = useI18n();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current; if (!el || !('IntersectionObserver' in window)) return;
    const io = new IntersectionObserver((es) => el.classList.toggle('lpd-off', !es[0].isIntersecting), { threshold: 0.05 });
    io.observe(el); return () => io.disconnect();
  }, []);
  return (
    <div ref={box} className={'lpd' + (reduce ? ' lpd-still' : '')}>
      <svg viewBox="0 0 560 440" role="img" aria-label={t('land.lp.alt')}>
        <defs>
          <radialGradient id="lpd-glow" cx="50%" cy="50%" r="50%"><stop offset="0" style={{ stopColor: 'var(--accent)', stopOpacity: 0.35 }} /><stop offset="1" style={{ stopColor: 'var(--accent)', stopOpacity: 0 }} /></radialGradient>
          <linearGradient id="lpd-beam" x1="0" y1="1" x2="0" y2="0"><stop offset="0" style={{ stopColor: 'var(--accent)', stopOpacity: 0.75 }} /><stop offset="1" style={{ stopColor: 'var(--accent)', stopOpacity: 0 }} /></linearGradient>
        </defs>
        <g className="lpd-grid"><line x1="0" y1="110" x2="560" y2="110" /><line x1="0" y1="220" x2="560" y2="220" /><line x1="0" y1="330" x2="560" y2="330" /><line x1="140" y1="0" x2="140" y2="440" /><line x1="280" y1="0" x2="280" y2="440" /><line x1="420" y1="0" x2="420" y2="440" /></g>
        <ellipse cx="280" cy="350" rx="230" ry="60" fill="url(#lpd-glow)" />
        <g fill="none" className="lpd-pad">
          <ellipse cx="280" cy="350" rx="150" ry="34" opacity=".45" /><ellipse cx="280" cy="350" rx="106" ry="24" opacity=".7" /><ellipse cx="280" cy="350" rx="60" ry="14" strokeWidth="2" />
          <ellipse className="lpd-a lpd-ring" cx="280" cy="350" rx="150" ry="34" strokeWidth="2" />
        </g>
        <rect className="lpd-a lpd-beam" x="238" y="70" width="84" height="280" fill="url(#lpd-beam)" />
        <g transform="translate(280,226)"><g className="lpd-a lpd-orb">
          <circle r="62" fill="url(#lpd-glow)" />
          <polygon points="0,-46 40,-23 40,23 0,46 -40,23 -40,-23" className="lpd-hex" strokeWidth="2.4" />
          <polygon points="0,-24 21,-12 21,12 0,24 -21,12 -21,-12" fill="none" className="lpd-hex2" strokeWidth="2" />
          <circle r="5" className="lpd-dot" />
        </g></g>
        <g transform="translate(280,200)"><g className="lpd-a lpd-drop">
          <rect x="-70" y="-48" width="140" height="96" rx="12" className="lpd-card" strokeWidth="1.5" />
          <circle cx="-54" cy="-34" r="3.5" fill="var(--fail)" /><circle cx="-42" cy="-34" r="3.5" fill="var(--warn)" /><circle cx="-30" cy="-34" r="3.5" fill="var(--accent-2)" />
          <rect x="-54" y="-18" width="70" height="6" rx="3" className="lpd-line1" /><rect x="-54" y="-4" width="100" height="6" rx="3" className="lpd-line" /><rect x="-54" y="10" width="56" height="6" rx="3" className="lpd-line" /><rect x="-54" y="24" width="84" height="6" rx="3" className="lpd-line" />
        </g></g>
        <text className="lpd-t lpd-dim" x="280" y="288" textAnchor="middle" fontSize="13">{t('land.lp.project')}</text>
        {[
          { x: 120, y: 150, w: 104, c: 'var(--run)', k: 1, dx: 160, dy: 50, label: 'Node.js' },
          { x: 450, y: 150, w: 124, c: 'var(--wait)', k: 2, dx: -170, dy: 50, label: 'PostgreSQL' },
          { x: 116, y: 262, w: 112, c: 'var(--ok)', k: 3, dx: 164, dy: -60, label: 'port 3000' },
          { x: 452, y: 262, w: 112, c: 'var(--warn)', k: 4, dx: -172, dy: -60, label: t('land.lp.env') },
        ].map((g) => (
          <g key={g.k} transform={`translate(${g.x},${g.y})`}><g className={`lpd-a lpd-t${g.k}`} style={{ ['--dx' as string]: `${g.dx}px`, ['--dy' as string]: `${g.dy}px` }}>
            <rect x={-g.w / 2} y="-15" width={g.w} height="30" rx="15" className="lpd-tag" style={{ stroke: g.c }} />
            <circle cx={-g.w / 2 + 18} cy="0" r="4" style={{ fill: g.c }} />
            <text className="lpd-t lpd-mono" x="8" y="4.5" textAnchor="middle" fontSize="12.5">{g.label}</text>
          </g></g>
        ))}
        <g transform="translate(280,64)"><g className="lpd-a lpd-pill">
          <rect x="-132" y="-22" width="264" height="44" rx="22" className="lpd-live" strokeWidth="1.5" />
          <circle cx="-106" cy="0" r="6" fill="var(--accent-2)" /><circle cx="-106" cy="0" r="11" fill="none" stroke="var(--accent-2)" opacity=".4" />
          <text className="lpd-t lpd-mono" x="10" y="5" textAnchor="middle" fontSize="15">your-app.liara.run</text>
        </g></g>
      </svg>
    </div>
  );
}
