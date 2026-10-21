// Contrast in the middle of motion: the landing hero at every half second of its 30 s loop, CSS animations of the run page seeked to early/middle/late
// positions, and every control with hover + focus + active forced at the same time. usage: node e2e/step6-mid.mjs <lang> <theme>
import fs from 'node:fs';
import { launch, sleep, open, waitFor, mockApi, errors } from './helpers.mjs';
import { startServer } from './server.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';
import { measureContrast } from './lib/a11y.mjs';
const [lang = 'en', theme = 'dark'] = process.argv.slice(2);
const srv = await startServer(); const c = await launch();
const out = { lang, theme, hero: [], heroScrub: [], runAnimations: [], forcedStates: [] };
const pre = async (path, w = 1440, h = 900) => { await open(c, path, { w, h }); await c.ev(`localStorage.setItem('lang','${lang}');localStorage.setItem('theme','${theme}')`); await c.send('Page.reload'); await sleep(1500); };
const short = (r) => ({ checked: r.checked, worst: r.worst, approx: r.approx, fails: r.fails.filter((f) => !f.disabled).map((f) => `${f.text.slice(0, 26)} ${f.ratio} [${f.cls}] ${f.fg} on ${f.bg}`) });

// 1. landing hero, every 0.5 s of the loop (the engine is a pure function of t; CSS animations keep running)
await pre('/?t=0'); await waitFor(c, `window.__lp&&__lp[0]`);
const heroH = await c.ev(`Math.ceil(document.querySelector('.hero').getBoundingClientRect().bottom+scrollY)`);
for (let t = 0; t <= 29.5; t += 0.5) {
  await c.ev(`__lp[0].render(__lp[0].sheets[0],${t})`);
  const r = await measureContrast(c, { clip: { x: 0, y: 0, width: 1440, height: heroH } });
  out.hero.push({ t, ...short(r) });
}
// the scroll-linked sheet at the same times
await c.ev(`scrollTo(0,document.querySelector('.story').offsetTop)`); await sleep(300);
const sr = await c.ev(`(function(){var r=document.querySelector('.story .sheet').getBoundingClientRect();return {x:0,y:Math.round(r.top+scrollY-10),w:1440,h:Math.round(r.height+20)}})()`);
for (let t = 0; t <= 27; t += 1.5) {
  await c.ev(`__lp[0].render(__lp[0].sheets[1],${t})`);
  const r = await measureContrast(c, { clip: { x: sr.x, y: sr.y, width: sr.w, height: sr.h } });
  out.heroScrub.push({ t, ...short(r) });
}
// the network drawing mid-build
await c.ev(`document.querySelector('#creates').scrollIntoView()`); await sleep(200);
const nr = await c.ev(`(function(){var r=document.querySelector('.netwrap').getBoundingClientRect();return {y:Math.round(r.top+scrollY-10),h:Math.round(r.height+20)}})()`);
for (const nt of [0.5, 1.5, 2.8, 4.0, 5.0, 6.2]) {
  const r = await measureContrast(c, { clip: { x: 0, y: nr.y, width: 1440, height: nr.h } });
  out.heroScrub.push({ net: nt, ...short(r) });
}

// 2. run page: every CSS animation seeked to three positions, then measured (page-enter, orb, flow, ring, shimmer, success burst)
const SEEK = (frac) => `(function(){var n=0;document.getAnimations().forEach(function(a){try{var t=a.effect.getComputedTiming();var d=t.duration;if(!isFinite(d)||!d)return;a.pause();a.currentTime=(t.delay||0)+d*${frac};n++}catch(e){}});return n})()`;
for (const [label, path, ready] of [['plan approval', '/demo/waiting?at=10', 'waiting'], ['secret', '/demo/failed?at=12', 'secret'], ['fix approval', '/demo/failed?at=23', 'fix'], ['live', '/demo/failed?at=30', 'live'], ['working', '/demo/waiting?at=16', 'working']]) {
  await pre(path); await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='${ready}'`, 20000).catch(() => {});
  for (const frac of [0.1, 0.5, 0.9]) {
    await c.ev(`(function(){var m=document.querySelector('main');if(m){m.classList.remove('enter');void m.offsetWidth;m.classList.add('enter')}})()`);
    const n = await c.ev(SEEK(frac));
    const r = await measureContrast(c);
    out.runAnimations.push({ label, frac, animations: n, ...short(r) });
  }
}

// 3. every control with hover, focus and active forced at once
const FORCE = async (selector) => {
  await c.send('DOM.enable'); await c.send('CSS.enable');
  const doc = await c.send('DOM.getDocument', { depth: 0 });
  const q = await c.send('DOM.querySelectorAll', { nodeId: doc.result.root.nodeId, selector });
  for (const id of q.result.nodeIds) await c.send('CSS.forcePseudoState', { nodeId: id, forcedPseudoClasses: ['hover', 'focus', 'active', 'focus-visible'] }).catch(() => {});
  return q.result.nodeIds;
};
const UNFORCE = async (ids) => { for (const id of ids) await c.send('CSS.forcePseudoState', { nodeId: id, forcedPseudoClasses: [] }).catch(() => {}); };
const SEL = 'a[href],button:not(:disabled),[role=tab],[role=radio],label.lp-drop,.prow,.opt,.fchip,input,textarea';
{ await pre('/'); await waitFor(c, `!!document.querySelector('.lp')`, 20000).catch(() => {}); await sleep(600);
  const ids = await FORCE(SEL); const r = await measureContrast(c); await UNFORCE(ids); out.forcedStates.push({ label: 'landing', controls: ids.length, ...short(r) }); }
const stop = await mockApi(c, buildMock());
for (const [label, path, ready] of [['dashboard', '/', `document.querySelectorAll('.prow .nm').length===4`], ['project', '/projects/2', `!!document.querySelector('.rrow:not(.rhead)')`]]) {
  await pre(path); await waitFor(c, ready, 20000).catch(() => {}); await sleep(600);
  const ids = await FORCE(SEL); const r = await measureContrast(c); await UNFORCE(ids);
  out.forcedStates.push({ label, controls: ids.length, ...short(r) });
}
for (const [label, path, ready] of [['plan approval', '/demo/waiting?at=10', 'waiting'], ['question', '/demo/waiting?at=5', 'question'], ['secret', '/demo/failed?at=12', 'secret'], ['fix approval', '/demo/failed?at=23', 'fix'], ['live', '/demo/failed?at=30', 'live']]) {
  await pre(path); await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='${ready}'`, 20000).catch(() => {}); await sleep(500);
  const ids = await FORCE(SEL); const r = await measureContrast(c); await UNFORCE(ids);
  out.forcedStates.push({ label: 'run: ' + label, controls: ids.length, ...short(r) });
}
out.consoleErrors = errors(c).length;
const sum = (arr) => ({ frames: arr.length, textsChecked: arr.reduce((a, x) => a + x.checked, 0), worst: Math.min(...arr.map((x) => x.worst)), framesWithFails: arr.filter((x) => x.fails.length).length });
const summary = { lang, theme, hero: sum(out.hero), heroScrub: sum(out.heroScrub), runAnimations: sum(out.runAnimations), forcedStates: sum(out.forcedStates), consoleErrors: out.consoleErrors,
  fails: [...out.hero.map((x) => ['hero t=' + x.t, x]), ...out.heroScrub.map((x) => ['scrub ' + (x.t ?? 'net ' + x.net), x]), ...out.runAnimations.map((x) => ['run ' + x.label + ' @' + x.frac, x]), ...out.forcedStates.map((x) => ['forced ' + x.label, x])].filter(([, x]) => x.fails.length).map(([k, x]) => ({ where: k, fails: x.fails })) };
fs.writeFileSync(`/tmp/mid-${lang}-${theme}.json`, JSON.stringify({ summary, ...out }, null, 1)); console.log(JSON.stringify(summary, null, 1));
c.close(); srv.stop();
process.exit(0);
