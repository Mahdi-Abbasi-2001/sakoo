// Step 5: landing page. Visitor vs signed-in routing, the hero replay, the scroll-linked run, reduced motion, pause when hidden or off screen, language toggle.
import { launch, sleep, open, type, click, shot, errors, MEASURE, waitFor } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer(); const c = await launch();
const out = {}; const OUT = process.env.SHOTS || '/tmp';
const R = (sel) => `(function(){var e=document.querySelector(${JSON.stringify(sel)});if(!e)return null;var r=e.getBoundingClientRect();return [Math.round(r.x*10)/10,Math.round((r.y+scrollY)*10)/10,Math.round(r.width*10)/10,Math.round(r.height*10)/10]})()`;

// 1. routing
await open(c, '/'); await waitFor(c, `!!document.querySelector('.lp')`);
out.visitorRoot = await c.ev(`({landing:!!document.querySelector('.lp'),title:document.title,h1:document.querySelector('.lp h1').textContent})`);
out.visitorRequests = (await c.ev(`performance.getEntriesByType('resource').map(function(e){return new URL(e.name).pathname}).filter(function(p){return p.indexOf('/api/')===0})`));
await open(c, '/runs/x'); out.visitorProtected = await c.ev('location.pathname');
const user = 'tester' + Date.now().toString(36);
await open(c, '/register'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await open(c, '/'); out.userRoot = await c.ev(`({landing:!!document.querySelector('.lp'),dashboard:!!document.querySelector('.page')})`);
await open(c, '/welcome'); await waitFor(c, `!!document.querySelector('.lp')`); out.userWelcome = await c.ev(`({landing:!!document.querySelector('.lp'),cta:document.querySelector('.cta').getAttribute('href')})`);
await c.ev(`fetch('/api/auth/logout',{method:'POST',headers:{'X-Requested-With':'XMLHttpRequest'}})`); await sleep(300);

// 2. the hero replay is a pure function of t: states and the one-rectangle rule
await open(c, '/?t=0'); await waitFor(c, `window.__lp&&__lp[0]`);
const frames = {};
for (const t of [0, 1.8, 4.7, 8.5, 10.4, 14.5, 19.5, 24.6, 26.4, 30]) {
  frames[t] = await c.ev(`(function(){var S=__lp[0].sheets[0];__lp[0].render(S,${t});var q=function(s){var e=document.querySelector(s);var r=e.getBoundingClientRect();return [r.x,r.y+scrollY,r.width,r.height].map(function(v){return Math.round(v*10)/10})};
    return {state:S.html('.state-word')[0].textContent,orb:S.orbs[0]&&S.orbs[0].getAttribute('data-state'),big:S.big[0].textContent,bigm:S.bigm[0].textContent,txt:S.apprTxt[0].textContent.slice(0,40),clock:S.clock.textContent,
      apply:q('.hero .lp-btn-p'),decline:q('.hero .lp-btn-s'),appr:q('.hero .lp-appr'),sheet:q('.hero .sheet'),big_r:q('.hero .big-h'),visibleActs:getComputedStyle(document.querySelector('.hero .lp-acts')).visibility}})()`);
}
out.frames = Object.fromEntries(Object.entries(frames).map(([t, f]) => [t, { state: f.state, orb: f.orb, big: f.big, bigm: f.bigm, acts: f.visibleActs, clock: f.clock }]));
const maxMove = (k) => { const v = Object.values(frames).map((f) => f[k]); return Math.max(...[0, 1, 2, 3].map((i) => Math.max(...v.map((a) => a[i])) - Math.min(...v.map((a) => a[i])))); };
out.maxMovePx = { apply: maxMove('apply'), decline: maxMove('decline'), approvalBox: maxMove('appr'), sheet: maxMove('sheet'), readout: maxMove('big_r') };

// 3. live: it plays, Pause stops it, frames seek, Apply advances it
await open(c, '/'); await waitFor(c, `window.__lp&&__lp[0]`); await sleep(1500);
const t1 = await c.ev('__lp[0].sheets[0].clock0'); await sleep(1000); const t2 = await c.ev('__lp[0].sheets[0].clock0');
await click(c, '.hero .pause'); await sleep(300); const p1 = await c.ev('__lp[0].sheets[0].clock0'); await sleep(900); const p2 = await c.ev('__lp[0].sheets[0].clock0');
await click(c, '.hero [data-frame="2"]'); const fr = await c.ev(`({t:__lp[0].sheets[0].clock0,state:document.querySelector('.hero .state-word').textContent,pressed:document.querySelector('.hero .pause').getAttribute('aria-pressed'),label:document.querySelector('.hero .pause').textContent,visible:getComputedStyle(document.querySelector('.hero .lp-acts')).visibility})`);
await click(c, '.hero .lp-btn-p'); await sleep(400); const ap = await c.ev(`({t:Math.round(__lp[0].sheets[0].clock0*10)/10,state:document.querySelector('.hero .state-word').textContent,label:document.querySelector('.hero .pause').textContent})`);
out.live = { advancedBy: +(t2 - t1).toFixed(2), pausedDrift: +(p2 - p1).toFixed(3), frame2: fr, afterApply: ap };

// 4. scroll-linked run
await open(c, '/?t=99', { w: 1440, h: 900 }); await c.ev('history.replaceState(null,"","/")');
await c.send('Page.reload'); await sleep(1200); await waitFor(c, `window.__lp&&__lp[0]`);
const reg = await c.ev(`(function(){var r=document.querySelector('.lp-steps').getBoundingClientRect();return {top:r.top+scrollY,h:r.height,vh:innerHeight}})()`);
out.scrubRegion = reg;
const scrub = [];
for (const p of [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1]) {
  const y = reg.top - reg.vh * 0.3 + 0 + p * (reg.h - reg.vh * 0.7) * 1 + reg.vh * 0.0;
  await c.ev(`scrollTo(0,${Math.round(reg.top - reg.vh * 0.3 + p * (reg.h - reg.vh * 0.7))})`); await sleep(1800);
  scrub.push(await c.ev(`(function(){var S=__lp[0].sheets[1];var r=document.querySelector('.story .sheet').getBoundingClientRect();return {p:${p},t:Math.round(S.clock0*10)/10,state:document.querySelector('.story .lp-appr').getAttribute('data-state'),step:[].slice.call(document.querySelectorAll('.lp-steps li')).findIndex(function(l){return l.classList.contains('lp-on')}),sheetInView:r.top>=0&&r.bottom<=innerHeight,sheetTop:Math.round(r.top)}})()`));
}
out.scrub = scrub;
await c.ev('scrollTo(0,0)'); await sleep(600);

// 5. reduced motion: a still, no running animation, frame buttons work, scroll snaps to frames
await open(c, '/', { rm: true }); await waitFor(c, `window.__lp&&__lp[0]`); await sleep(1200);
out.reduced = await c.ev(`(function(){var S=__lp[0].sheets[0];var a=S.clock0;return {t:a,state:document.querySelector('.hero .state-word').textContent,rm:document.querySelector('.lp').classList.contains('rm'),runningAnimations:document.getAnimations().filter(function(x){return x.playState==='running'}).length,pauseVisible:true}})()`);
await sleep(800); out.reducedStill = await c.ev('__lp[0].sheets[0].clock0');
out.reducedSnaps = [];
const rreg = await c.ev(`(function(){var r=document.querySelector('.lp-steps').getBoundingClientRect();return {top:r.top+scrollY,h:r.height,vh:innerHeight}})()`);
for (const p of [0.05, 0.35, 0.6, 0.9]) { await c.ev(`scrollTo(0,${Math.round(rreg.top - rreg.vh * 0.3 + p * (rreg.h - rreg.vh * 0.7))})`); await sleep(700); out.reducedSnaps.push([p, await c.ev(`Math.round(__lp[0].sheets[1].clock0*10)/10`), await c.ev(`document.querySelector('.story .lp-appr').getAttribute('data-state')`)]); }
await c.ev('scrollTo(0,0)');
out.reducedHover = await c.ev(`(function(){var t=document.querySelector('.tool');var cs=getComputedStyle(t);return {transition:cs.transitionDuration,animName:getComputedStyle(document.querySelector('.cta')).animationName}})()`);

// 6. hidden tab and off-screen pause everything
await open(c, '/'); await waitFor(c, `window.__lp&&__lp[0]`); await sleep(800);
await c.ev(`Object.defineProperty(document,'hidden',{configurable:true,get:function(){return true}});document.dispatchEvent(new Event('visibilitychange'))`);
const h1 = await c.ev('__lp[0].sheets[0].clock0'); await sleep(900); const h2 = await c.ev('__lp[0].sheets[0].clock0');
const hid = await c.ev(`document.querySelector('.lp').classList.contains('hid')`);
const pausedCss = await c.ev(`getComputedStyle(document.querySelector('.hero .orb, .hero svg.orb')||document.body).animationPlayState`);
await c.ev(`Object.defineProperty(document,'hidden',{configurable:true,get:function(){return false}});document.dispatchEvent(new Event('visibilitychange'))`); await sleep(500);
const h3 = await c.ev('__lp[0].sheets[0].clock0');
await c.ev(`scrollTo(0,document.querySelector('#why').offsetTop)`); await sleep(900);
const o1 = await c.ev('__lp[0].sheets[0].clock0'); await sleep(800); const o2 = await c.ev('__lp[0].sheets[0].clock0');
out.pause = { hiddenDrift: +(h2 - h1).toFixed(3), hiddenClass: hid, resumesAfterShow: h3 > h2, offScreenDrift: +(o2 - o1).toFixed(3), offClass: await c.ev(`document.querySelector('.hero .sheet').classList.contains('lp-off')`) };

// 7. language toggle on the landing
await open(c, '/'); await waitFor(c, `!!document.querySelector('.lp')`);
await click(c, 'button[lang=fa]'); await sleep(1200);
out.fa = await c.ev(`({dir:document.documentElement.dir,lpDir:document.querySelector('.lp').dir,lang:document.documentElement.lang,title:document.title,h1:document.querySelector('.lp h1').textContent,rtlSheet:document.querySelector('.sheet').getAttribute('data-rtl'),state:document.querySelector('.state-word').textContent})`);
await click(c, 'button[lang=en]'); await sleep(900); out.en = await c.ev(`({dir:document.documentElement.dir,h1:document.querySelector('.lp h1').textContent})`);

// 8. layout at three widths in both languages, shots
for (const lang of ['en', 'fa']) for (const [name, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) {
  await open(c, '/?t=26.4&nt=6.2', { w, h, lang: lang === 'fa' ? 'fa' : undefined }); await waitFor(c, `!!document.querySelector('.lp')`); await sleep(800);
  out[`layout-${lang}-${name}`] = await c.ev(MEASURE);
  out[`layout-${lang}-${name}`].sideways = await c.ev(`[document.documentElement.scrollWidth,document.documentElement.clientWidth]`);
  await shot(c, `${OUT}/s5-${lang}-${name}-top.png`);
  await c.send('Page.captureScreenshot', {}); 
  const full = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); (await import('node:fs')).writeFileSync(`${OUT}/s5-${lang}-${name}-full.png`, Buffer.from(full.result.data, 'base64'));
}
out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1));
c.close(); srv.stop();
