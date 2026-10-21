import { useEffect, useRef, useState } from 'react';

/** A number that counts up when it first appears or changes. The final text always occupies the width (a hidden copy), so nothing shifts while
 *  it counts. Reduced motion, a hidden tab and a first paint off screen show the final number at once. */
export function Ticker({ value, format, duration = 600 }: { value: number; format: (n: number) => string; duration?: number }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  const first = useRef(true);
  useEffect(() => {
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const start = first.current ? 0 : from.current;
    first.current = false;
    if (reduce || document.hidden || start === value) { setShown(value); from.current = value; return; }
    let raf = 0, t0: number | null = null;
    const step = (ts: number) => {
      if (document.hidden) { setShown(value); from.current = value; return; }
      if (t0 === null) t0 = ts;
      const p = Math.min(1, (ts - t0) / duration), e = 1 - Math.pow(1 - p, 3);
      setShown(start + (value - start) * e);
      if (p < 1) raf = requestAnimationFrame(step); else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  const final = format(value);
  return (
    <span className="tick">
      <span className="sr-only">{final}</span>
      <span className="tick-final" aria-hidden="true">{final}</span>
      <span className="tick-anim" aria-hidden="true">{format(shown)}</span>
    </span>
  );
}
