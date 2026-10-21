const P: Record<string, string> = {
  check: 'M3.5 8.4l3 3 6-7',
  warn: 'M8 2l6.4 11.4H1.6zM8 6.6v3M8 11.6v.1',
  alert: 'M8 1.8a6.2 6.2 0 1 0 0 12.4A6.2 6.2 0 0 0 8 1.8zM8 4.8v4M8 10.8v.1',
  shield: 'M8 1.5l5.5 2v4c0 3.2-2.3 5.3-5.5 6.5C4.8 12.8 2.5 10.7 2.5 7.5v-4z',
  plug: 'M5.5 2v3M10.5 2v3M4 5h8v2.5a4 4 0 0 1-8 0zM8 11.5V14',
  diamond: 'M8 1.8l5 6.2-5 6.2L3 8zM8 6.4v3.2',
  x: 'M4.5 4.5l7 7M11.5 4.5l-7 7',
  dots: 'M4 8h.1M8 8h.1M12 8h.1',
  sun: 'M8 4.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zM8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3 3l1 1M12 12l1 1M3 13l1-1M12 4l1-1',
  moon: 'M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z',
  arrow: 'M3 8h10M9 4l4 4-4 4',
  db: 'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2zM3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
  server: 'M2.5 3h11v4h-11zM2.5 9h11v4h-11zM5 5h.1M5 11h.1',
  disk: 'M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2zM8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z',
  link: 'M6.5 9.5a3 3 0 0 0 4.2 0l2-2a3 3 0 0 0-4.2-4.2l-.8.8M9.5 6.5a3 3 0 0 0-4.2 0l-2 2a3 3 0 0 0 4.2 4.2l.8-.8',
  file: 'M4 1.5h5l3 3v10H4zM9 1.5v3h3',
  loader: 'M8 1.8a6.2 6.2 0 1 1-6.2 6.2',
  sliders: 'M2.5 4.5h11M2.5 11.5h11M10.5 2.8v3.4M5.5 9.8v3.4',
  chat: 'M2.5 3h11v7.5H8.2L5 13.5v-3H2.5z',
  dashed: 'M8 1.8a6.2 6.2 0 1 0 0 12.4A6.2 6.2 0 0 0 8 1.8z',
};
export function Icon({ name, className = 'ic', dashed }: { name: keyof typeof P | string; className?: string; dashed?: boolean }) {
  return (
    <svg className={className + (name === 'arrow' ? ' ic-flip' : '')} viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d={P[name] ?? P.dots} strokeDasharray={dashed ? '2 2.6' : undefined} />
    </svg>
  );
}
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect width="28" height="28" rx="7" fill="var(--accent)" />
      <path d="M8 17l6-9 6 9M8 21h12" fill="none" stroke="var(--accent-ink)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
