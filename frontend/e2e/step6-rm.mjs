// Reduced motion on every page and state: nothing running, no transition or animation declared anywhere, hover and focus change colour only.
// usage: node e2e/step6-rm.mjs <lang>
import fs from 'node:fs';
import { launch, sleep, errors } from './helpers.mjs';
import { startServer } from './server.mjs';
import { walk } from './scenarios.mjs';
const lang = process.argv[2] || 'en';
const srv = await startServer(); const c = await launch();
const SCAN = `(function(){
  const sel=(e)=>e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+(typeof e.className==='string'&&e.className?'.'+e.className.trim().split(/\\s+/).slice(0,2).join('.'):'');
  const running=document.getAnimations().filter(a=>a.playState==='running').map(a=>(a.animationName||a.transitionProperty||'anim')+'@'+(a.effect&&a.effect.target?sel(a.effect.target):'?'));
  const trans=new Set(),anim=new Set();
  for(const e of document.querySelectorAll('*')){
    for(const ps of [null,'::before','::after']){const cs=getComputedStyle(e,ps);
      if(cs.transitionDuration.split(',').some(d=>parseFloat(d)>0))trans.add(sel(e)+(ps||'')+' '+cs.transitionDuration.split(',')[0]);
      if(cs.animationName!=='none'&&parseFloat(cs.animationDuration)>0)anim.add(sel(e)+(ps||'')+' '+cs.animationName)}}
  return {running,trans:[...trans].slice(0,8),anim:[...anim].slice(0,8),scrollBehavior:getComputedStyle(document.documentElement).scrollBehavior,tick:[...document.querySelectorAll('.tick')].filter(t=>{const a=t.querySelector('.tick-anim'),f=t.querySelector('.tick-final');return a&&f&&a.textContent!==f.textContent}).length,lpRm:document.querySelector('.lp')?document.querySelector('.lp').classList.contains('rm'):null}
})()`;
const HOVER = `(function(){const els=[...document.querySelectorAll('a[href],button,[role=tab],[role=radio],.card.lift,.tool,.prow')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.top>=0&&r.bottom<=innerHeight&&getComputedStyle(e).visibility!=='hidden'}).slice(0,14);
  window.__els=els;return els.map(e=>{const r=e.getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})})()`;
const SNAP = `window.__els.map(e=>{const r=e.getBoundingClientRect(),cs=getComputedStyle(e);return [cs.transform,Math.round(r.x*10)/10,Math.round(r.y*10)/10,Math.round(r.width*10)/10,Math.round(r.height*10)/10]})`;
const states = []; const bad = { running: new Map(), trans: new Map(), anim: new Map(), tick: [], hoverMoves: [], scroll: new Set() };
await walk(c, { lang, rm: true, async onState(label, w) {
  const r = await c.ev(SCAN); states.push(label + '@' + w);
  for (const k of r.running) { if (!bad.running.has(k)) bad.running.set(k, []); bad.running.get(k).push(label); }
  for (const k of r.trans) { if (!bad.trans.has(k)) bad.trans.set(k, []); bad.trans.get(k).push(label); }
  for (const k of r.anim) { if (!bad.anim.has(k)) bad.anim.set(k, []); bad.anim.get(k).push(label); }
  if (r.tick) bad.tick.push(label); bad.scroll.add(r.scrollBehavior);
  if (/^(landing|dashboard|project|run: plan approval$|run: live$|new deployment$|login)/.test(label) && w >= 768) {
    const pts = await c.ev(HOVER); const before = await c.ev(SNAP);
    for (let i = 0; i < pts.length; i++) {
      await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: pts[i][0], y: pts[i][1] }); await sleep(40);
      const mid = await c.ev(`(function(i){const e=window.__els[i];const r=e.getBoundingClientRect(),cs=getComputedStyle(e);return [cs.transform,Math.round(r.x*10)/10,Math.round(r.y*10)/10,Math.round(r.width*10)/10,Math.round(r.height*10)/10]})(${i})`);
      if (JSON.stringify(mid) !== JSON.stringify(before[i])) bad.hoverMoves.push(`${label}: element ${i} ${JSON.stringify(before[i])} -> ${JSON.stringify(mid)}`);
    }
    await c.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1, y: 1 });
  }
} });
const out = { lang, states: states.length, runningAnimations: [...bad.running].map(([k, v]) => [k, [...new Set(v)].slice(0, 3)]), transitionsDeclared: [...bad.trans].map(([k, v]) => [k, [...new Set(v)].slice(0, 2)]), animationsDeclared: [...bad.anim].map(([k, v]) => [k, [...new Set(v)].slice(0, 2)]),
  tickersNotFinal: bad.tick, scrollBehavior: [...bad.scroll], hoverMoves: bad.hoverMoves.slice(0, 10), consoleErrors: errors(c).length };
fs.writeFileSync(`/tmp/rm-${lang}.json`, JSON.stringify(out, null, 1)); console.log(JSON.stringify(out, null, 1));
c.close(); srv.stop();
process.exit(0);
