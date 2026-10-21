/* The blueprint engine, ported from the final landing board. The whole sheet is a pure function of the replay clock t (seconds):
   render(S, t). Autoplay, scroll scrubbing, the frame buttons and the reduced-motion still all call the same render.
   Real data only: the numbers and event names come from docs/samples (the build-timeout and dry-run samples); steps that ran in
   under 100 ms in a sample get the visible floors listed in the motion spec. */
/* eslint-disable @typescript-eslint/no-explicit-any */
export const LP_T = {
  read: 3.2, plan: 6.2, wait: 8.6, appr: 14.0, done: 24.4, url: 24.8, end: 30, scrubMax: 27,
  plot: { web: [3.6, 4.8], cache: [4.0, 5.2], db: [4.4, 5.6], worker: [4.8, 6.0], disk: [5.2, 6.2] } as Record<string, number[]>,
  price: { web: 6.2, cache: 6.45, db: 6.7, worker: 6.95 } as Record<string, number>,
  make: { db: [14.0, 19.0], cache: [19.0, 20.4], web: [20.4, 22.6], worker: [22.6, 24.4] } as Record<string, number[]>,
  q: 4.6, qa: 5.4,
};
const LP_PRICE: Record<string, number> = { web: 1145.83, cache: 1145.83, db: 1145.83, worker: 1145.83 };
const LP_TOTAL_H = 4583, LP_TOTAL_M = 3300000;
export const LP_FRAMES = [1.8, 8.5, 10.4, 19.5, 26.4];
const LP_EVENTS: [number, string][] = [
  [0, 'run_started'], [3.2, 'phase_finished'], [4.6, 'question'], [5.4, 'answered'], [6.2, 'plan_ready'],
  [8.6, 'confirm_request'], [14.0, 'confirm_response'], [14.3, 'resource_created'], [19.3, 'resource_created'],
  [20.7, 'resource_created'], [22.9, 'resource_created'], [24.4, 'run_finished'], [24.8, 'test_result'],
];

const clamp = (x: number, a: number, b: number) => (x < a ? a : x > b ? b : x);
const ease = (p: number) => 1 - Math.pow(1 - clamp(p, 0, 1), 3);
const win = (t: number, a: number, d: number) => clamp((t - a) / d, 0, 1);

export type Sheet = any;
export type Lang = 'en' | 'fa';

function makeSheet(el: HTMLElement, lang: Lang): Sheet {
  const S: any = { el, lang, rtl: el.getAttribute('data-rtl') === '1', t: -1, root: el.closest('.lp'), last: {} };
  const nf = new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', { maximumFractionDigits: 0 });
  const nf2 = new Intl.NumberFormat(lang === 'fa' ? 'fa-IR' : 'en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  S.fmt = (n: number) => nf.format(Math.round(n));
  S.fmt2 = (n: number) => nf2.format(n);
  const svgs = [...el.querySelectorAll('svg.scene')];
  S.all = (sel: string) => svgs.flatMap((s) => [...s.querySelectorAll(sel)]) as any[];
  S.html = (sel: string) => [...el.querySelectorAll(sel)] as any[];
  S.orbs = S.all('.hero-orb');
  S.files = S.all('.file');
  S.nodes = {};
  for (const k of ['web', 'cache', 'db', 'worker', 'disk']) {
    S.nodes[k] = S.all(`.node[data-n="${k}"]`).map((g: Element) => ({
      g, pl: g.querySelector('.lp-pl'), pr: g.querySelector('.pr'), fl: g.querySelector('.fl'), sl: g.querySelector('.sl'),
      sp: g.querySelector('.st-p'), sc: g.querySelector('.st-c'), sk: g.querySelector('.st-l'), dim: g.querySelector('.dim'),
      dl: g.querySelector('.dl'), pv: g.querySelector('.lp-pv'), ev: g.querySelectorAll('.ev,.kd,.lp-nm'), fw: +(g.getAttribute('data-w') ?? 0),
    }));
  }
  S.edges = S.all('.lp-ed'); S.cells = S.all('.gc');
  S.qs = S.all('.q-ask'); S.qa = S.all('.q-ans'); S.url = S.all('.url-tag');
  S.tv = S.all('.tv-h'); S.tm = S.all('.tv-m');
  S.big = S.html('.big-h'); S.bigm = S.html('.big-m');
  S.ev = S.all('.tb-ev'); S.tt = S.all('.tb-t');
  S.appr = S.html('.lp-appr')[0]; S.apprTxt = S.html('.appr-txt');
  S.bar = S.html('.prog-bar')[0]; S.clock = S.html('.clock')[0];
  S.leg = S.html('.states [data-s]'); S.frames = S.html('[data-frame]');
  S.captions = el.getAttribute('data-mode') === 'scrub' ? [...(el.closest('.story') ?? el).querySelectorAll('[data-step]')] : [];
  return S;
}

const isLoop = (S: Sheet) => S.el.getAttribute('data-mode') === 'loop';
function stateAt(t: number) {
  if (t < LP_T.read) return 'reading';
  if (t < LP_T.wait) return 'thinking';
  if (t < LP_T.appr) return 'waiting';
  if (t < LP_T.done) return 'working';
  return 'done';
}

export function render(S: Sheet, t0: number) {
  const t = clamp(t0, 0, LP_T.end);
  if (S.t === t) return;
  S.t = t;
  const st = stateAt(t), T = LP_T, rtl = S.rtl;
  if (S.last.st !== st) {
    S.last.st = st;
    S.orbs.forEach((o: Element) => o.setAttribute('data-state', st));
    S.html('.state-word').forEach((e: HTMLElement) => { e.textContent = e.getAttribute('data-' + st); });
    S.html('.state-ico').forEach((e: Element) => e.setAttribute('data-state', st));
    S.leg.forEach((e: HTMLElement) => e.classList.toggle('lp-on', e.getAttribute('data-s') === st));
    if (S.appr) {
      S.appr.setAttribute('data-state', st);
      S.apprTxt.forEach((e: HTMLElement) => { e.textContent = e.getAttribute('data-' + st); });
    }
  }
  if (S.root && isLoop(S)) {
    const w = t < 6.2 ? 0 : t < 8.6 ? (t - 6.2) / 2.4 : t < 14 ? 1 : t < 16.5 ? 1 - (t - 14) / 2.5 : 0;
    const wv = Math.round(ease(w) * 50) / 50;
    if (S.warmEl === undefined) S.warmEl = S.root.querySelector('.lpbg .g2');
    if (S.warmEl && S.warmV !== wv) { S.warmV = wv; S.warmEl.style.opacity = String(wv); }
  }
  /* files stream into the orb */
  S.files.forEach((f: SVGElement, i: number) => {
    const a = i * 0.3, p = win(t, a, 1.7), e = ease(p);
    const x0 = +f.getAttribute('data-x0')!, y0 = +f.getAttribute('data-y0')!, x1 = +f.getAttribute('data-x1')!, y1 = +f.getAttribute('data-y1')!;
    const x = x0 + (x1 - x0) * e, y = y0 + (y1 - y0) * e, sc = 1 - 0.55 * e;
    f.style.opacity = t >= a && p < 1 ? '1' : '0';
    // a label that has shrunk below legible size is dropped rather than faded: text is either at full contrast or not shown
    const lab = f.querySelector('text') as SVGElement | null;
    if (lab) lab.style.display = sc >= 0.8 ? '' : 'none';
    f.setAttribute('transform', `translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${sc.toFixed(3)})`);
  });
  /* nodes */
  const live: Record<string, boolean> = {};
  for (const k of ['db', 'cache', 'web', 'worker']) live[k] = t >= T.make[k][1];
  live.disk = live.web;
  for (const k of Object.keys(S.nodes)) {
    const pw = T.plot[k], pp = win(t, pw[0], pw[1] - pw[0]);
    const mk = T.make[k] || T.make.web;
    const mp = k === 'disk' ? (live.web ? 1 : win(t, T.make.web[0], T.make.web[1] - T.make.web[0])) : win(t, mk[0], mk[1] - mk[0]);
    const creating = mp > 0 && mp < 1, isLive = mp >= 1;
    for (const n of S.nodes[k]) {
      n.pl.style.strokeDashoffset = (1 - ease(pp)).toFixed(3);
      n.pl.style.opacity = pp > 0 && pp < 1 ? '1' : '0';
      n.pr.style.opacity = pp >= 1 ? '1' : '0';
      n.sl.style.opacity = isLive || creating ? '1' : '0';
      n.sl.classList.toggle('cre', creating);
      const fw = n.fw * (isLive ? 1 : mp);
      n.fl.setAttribute('width', fw.toFixed(1));
      if (rtl) n.fl.setAttribute('x', (+n.fl.getAttribute('data-x') + n.fw - fw).toFixed(1));
      n.pr.style.display = isLive ? 'none' : '';
      for (let i = 0; i < n.ev.length; i++) n.ev[i].style.opacity = pp >= 0.55 ? '1' : '0';
      n.sp.style.display = pp >= 1 && !creating && !isLive ? 'inline' : 'none';
      n.sc.style.display = creating ? 'inline' : 'none';
      n.sk.style.display = isLive ? 'inline' : 'none';
      if (n.dim) {
        const ps = T.price[k], dp = win(t, ps - 0.2, 0.5);
        n.dl.style.strokeDashoffset = (1 - ease(dp)).toFixed(3);
        n.dim.style.opacity = dp > 0 ? '1' : '0';
        if (n.pv) n.pv.textContent = S.fmt2(LP_PRICE[k] * ease(win(t, ps, 1.4)));
      }
    }
  }
  /* edges */
  S.edges.forEach((e: SVGElement) => {
    const a = e.getAttribute('data-a')!, b = e.getAttribute('data-b')!;
    e.style.opacity = String(win(t, 5.8, 0.5));
    e.classList.toggle('lp-on', !!(live[a] && live[b]));
  });
  /* private network grid */
  S.cells.forEach((c: SVGElement) => {
    const g = c.getAttribute('data-g')!;
    let s: number;
    if (g === 'link') { const ab = c.getAttribute('data-ab')!.split(','); s = Math.max((T.make[ab[0]] ?? T.make.web)[1], (T.make[ab[1]] ?? T.make.web)[1]); }
    else s = (T.make[g] ?? T.make.web)[0];
    const dt = t - s; let o = 0;
    if (dt >= 0) o = dt < 0.5 ? dt / 0.5 : 0.5 + 0.5 * Math.max(0, 1 - (dt - 0.5) / 1.6);
    c.style.opacity = o.toFixed(3);
  });
  S.qs.forEach((e: SVGElement) => { e.style.opacity = t >= T.q && t < T.qa ? '1' : '0'; });
  S.qa.forEach((e: SVGElement) => { e.style.opacity = t >= T.qa ? '1' : '0'; });
  /* totals */
  const tp = ease(win(t, 6.2, 2.2)), th = S.fmt(LP_TOTAL_H * tp), tm = S.fmt(LP_TOTAL_M * tp);
  S.tv.forEach((e: Element) => { e.textContent = th; }); S.tm.forEach((e: Element) => { e.textContent = tm; });
  S.big.forEach((e: Element) => { e.textContent = th; }); S.bigm.forEach((e: Element) => { e.textContent = tm; });
  S.url.forEach((e: SVGElement) => { e.style.opacity = t >= T.url ? '1' : '0'; });
  /* title block */
  let evn = LP_EVENTS[0][1];
  for (const [at, name] of LP_EVENTS) if (t >= at) evn = name;
  if (S.last.ev !== evn) { S.last.ev = evn; S.ev.forEach((e: Element) => { e.textContent = evn; }); }
  const tt = (t < 10 ? '0' : '') + t.toFixed(1) + ' s';
  S.tt.forEach((e: Element) => { e.textContent = tt; });
  if (S.bar) S.bar.style.transform = `scaleX(${(t / T.end).toFixed(4)})`;
  if (S.clock) S.clock.textContent = tt;
  /* frame buttons */
  let fi = 0;
  for (let j = 0; j < LP_FRAMES.length; j++) if (t >= LP_FRAMES[j] - 0.01 && (j === LP_FRAMES.length - 1 || t < LP_FRAMES[j + 1] - 0.01)) fi = j;
  if (S.last.fi !== fi) {
    S.last.fi = fi;
    S.frames.forEach((b: HTMLElement) => { const on = +b.getAttribute('data-frame')! === fi; b.classList.toggle('lp-on', on); if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current'); });
  }
  const ts = S.stepT !== undefined ? S.stepT : t;   // while scrolling, the caption follows the scroll position, not the eased clock
  const step = ts < T.plan - 0.2 ? 0 : ts < T.wait + 0.2 ? 1 : ts < T.appr ? 2 : 3;
  if (S.last.step !== step) { S.last.step = step; S.captions.forEach((c: HTMLElement) => c.classList.toggle('lp-on', +c.getAttribute('data-step')! === step)); }
}

export interface LandingApi { sheets: Sheet[]; stop: () => void; render: typeof render }

/** Starts every sheet under `root`. `reduce` is the real prefers-reduced-motion value. */
export function mountLanding(root: HTMLElement, lang: Lang, reduce: boolean): LandingApi {
  const q = location.search || '';
  const frozen = (q.match(/[?&]t=([0-9.]+)/) || [])[1];     // test hook: freeze every sheet at this time
  root.classList.toggle('rm', reduce);
  const cleanups: (() => void)[] = [];
  const on = (t: EventTarget, ev: string, fn: (e: any) => void) => { t.addEventListener(ev, fn); cleanups.push(() => t.removeEventListener(ev, fn)); };
  const sheets: Sheet[] = [...root.querySelectorAll<HTMLElement>('.sheet')].map((el) => {
    const S = makeSheet(el, lang);
    S.mode = el.getAttribute('data-mode'); S.paused = false; S.vis = true; S.clock0 = 0; S.target = 0;
    return S;
  });
  const setPause = (S: Sheet, paused: boolean) => {
    S.paused = paused;
    const pb = S.html('.pause')[0];
    if (pb) { pb.setAttribute('aria-pressed', paused ? 'true' : 'false'); pb.textContent = pb.getAttribute(paused ? 'data-play' : 'data-pause'); }
  };
  for (const S of sheets) {
    const pb = S.html('.pause')[0];
    if (pb) on(pb, 'click', () => setPause(S, !S.paused));
    S.frames.forEach((b: HTMLElement) => on(b, 'click', () => {
      setPause(S, true);
      S.target = S.clock0 = LP_FRAMES[+b.getAttribute('data-frame')!];
      render(S, S.clock0);
    }));
    const ap = S.html('.lp-appr .lp-btn-p')[0], dc = S.html('.lp-appr .lp-btn-s')[0];
    if (ap) on(ap, 'click', () => { S.clock0 = LP_T.appr; setPause(S, false); render(S, S.clock0); });
    if (dc) on(dc, 'click', () => { S.clock0 = 0; setPause(S, false); render(S, 0); });
    if ('IntersectionObserver' in window) {
      const io = new IntersectionObserver((es) => { S.vis = es[0].isIntersecting; S.el.classList.toggle('lp-off', !S.vis); }, { threshold: 0.05 });
      io.observe(S.el); cleanups.push(() => io.disconnect());
    }
  }
  const api: any = { sheets, root, lang, render };
  if (frozen !== undefined) { sheets.forEach((S) => { S.paused = true; S.clock0 = parseFloat(frozen); render(S, S.clock0); }); api.frozen = true; }
  else if (reduce) sheets.forEach((S) => { S.paused = true; S.clock0 = LP_FRAMES[4]; render(S, S.clock0); });
  else sheets.forEach((S) => { S.clock0 = 0; render(S, 0); });
  /* scroll-linked sheet: progress through its .steps region. If the document cannot scroll that far, it plays by time instead. */
  const region = (S: Sheet) => S.el.closest('.story')?.querySelector('.lp-steps') as HTMLElement | null;
  /* The step list is the ruler: f is how many steps the viewport middle has passed (0..4), so the glowing step is always the one on screen.
     The sheet's time is interpolated between the moment each step begins. */
  const KNOTS = [0, LP_T.plan - 0.2, LP_T.wait + 0.2, LP_T.appr, LP_T.scrubMax];
  const scrubTarget = (S: Sheet) => {
    const r = region(S); if (!r) return null;
    const b = r.getBoundingClientRect(), vh = window.innerHeight || 800;
    if (b.height < vh * 1.3) return null;
    const f = clamp((vh * 0.5 - b.top) / b.height * 4, 0, 4), i = Math.min(3, Math.floor(f));
    return { f, t: KNOTS[i] + (KNOTS[i + 1] - KNOTS[i]) * (f - i) };
  };
  let raf = 0, last = 0;
  const tick = (now: number) => {
    raf = requestAnimationFrame(tick);
    const dt = Math.min(0.1, (now - last) / 1000); last = now;
    if (document.hidden || api.frozen) return;
    for (const S of sheets) {
      if (!S.vis) continue;
      if (S.mode === 'scrub') {
        const sc = scrubTarget(S);
        if (sc !== null) {
          if (reduce) {
            const idx = Math.min(3, Math.floor(sc.f));
            const snap = [LP_FRAMES[0], LP_FRAMES[1], LP_FRAMES[2], LP_FRAMES[4]][idx];
            S.clock0 = snap; render(S, snap); continue;
          }
          S.target = sc.t; S.stepT = S.target; S.clock0 += (S.target - S.clock0) * 0.25; render(S, S.clock0); continue;
        }
        if (reduce) continue;
      }
      if (reduce || S.paused) continue;
      S.stepT = undefined;
      S.clock0 += dt; if (S.clock0 >= LP_T.end) S.clock0 = 0;
      render(S, S.clock0);
    }
  };
  if (!reduce || sheets.some((S) => S.mode === 'scrub')) { last = performance.now(); raf = requestAnimationFrame(tick); }
  on(document, 'visibilitychange', () => { last = performance.now(); root.classList.toggle('hid', document.hidden); });
  /* pointer spotlight (fine pointers only, no reduced motion) */
  const hero = root.querySelector<HTMLElement>('.hero');
  if (hero && window.matchMedia?.('(pointer:fine)').matches && !reduce) {
    let pend = false, px = 0, py = 0;
    on(hero, 'pointermove', (e: PointerEvent) => {
      const b = hero.getBoundingClientRect(); px = e.clientX - b.left; py = e.clientY - b.top;
      if (!pend) { pend = true; requestAnimationFrame(() => { pend = false; hero.style.setProperty('--mx', px + 'px'); hero.style.setProperty('--my', py + 'px'); hero.classList.add('spot'); }); }
    });
    on(hero, 'pointerleave', () => hero.classList.remove('spot'));
  }
  api.stop = () => { cancelAnimationFrame(raf); cleanups.forEach((f) => f()); };
  const w = window as any; (w.__lp = w.__lp || []).push(api);
  return api as LandingApi;
}
