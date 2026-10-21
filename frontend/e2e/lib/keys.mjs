// Keyboard driving and focus inspection over the DevTools protocol.
import { sleep } from '../helpers.mjs';
import { decodePng, ratio } from './a11y.mjs';
const K = { Tab: { key: 'Tab', code: 'Tab', vk: 9 }, Enter: { key: 'Enter', code: 'Enter', vk: 13, text: '\r' }, Space: { key: ' ', code: 'Space', vk: 32, text: ' ' }, Escape: { key: 'Escape', code: 'Escape', vk: 27 },
  ArrowRight: { key: 'ArrowRight', code: 'ArrowRight', vk: 39 }, ArrowLeft: { key: 'ArrowLeft', code: 'ArrowLeft', vk: 37 }, ArrowDown: { key: 'ArrowDown', code: 'ArrowDown', vk: 40 }, Home: { key: 'Home', code: 'Home', vk: 36 } };
export async function press(c, name, { shift = false } = {}) {
  const k = K[name]; const mod = shift ? 8 : 0;
  await c.send('Input.dispatchKeyEvent', { type: k.text ? 'keyDown' : 'rawKeyDown', modifiers: mod, key: k.key, code: k.code, windowsVirtualKeyCode: k.vk, text: k.text, unmodifiedText: k.text });
  await c.send('Input.dispatchKeyEvent', { type: 'keyUp', modifiers: mod, key: k.key, code: k.code, windowsVirtualKeyCode: k.vk });
  await sleep(60);
}
/** What has focus: name, role, rectangle, whether it is on screen and not covered, and the focus indicator's computed style. */
export const FOCUS = `(function(){
  const e=document.activeElement;if(!e||e===document.body)return null;
  // a visually hidden file input: the visible focus ring belongs to the drop zone that wraps it
  const host=(e.type==='file'&&e.closest('label,.drop'))||e;
  const r=host.getBoundingClientRect(),cs=getComputedStyle(host);
  const lab=e.getAttribute('aria-labelledby');
  const name=(e.getAttribute('aria-label')||(lab&&lab.split(' ').map(function(i){var x=document.getElementById(i);return x?x.textContent:''}).join(' '))||(e.labels&&e.labels[0]&&e.labels[0].textContent)||e.textContent||e.value||e.getAttribute('placeholder')||e.id||'').replace(/\\s+/g,' ').trim().slice(0,40);
  const top=document.elementFromPoint(Math.min(innerWidth-1,Math.max(0,r.left+r.width/2)),Math.min(innerHeight-1,Math.max(0,r.top+r.height/2)));
  const inView=r.bottom>0&&r.top<innerHeight&&r.right>0&&r.left<innerWidth;
  return {sx:scrollX,sy:scrollY,tag:e.tagName.toLowerCase(),role:e.getAttribute('role')||'',id:e.id,cls:(typeof e.className==='string'?e.className:'').slice(0,28),name,rect:[r.left,r.top,r.width,r.height].map(Math.round),inView,
    covered:!!top&&!(host.contains(top)||top.contains(host)||e.contains(top)||top.contains(e)),outline:{w:parseFloat(cs.outlineWidth),style:cs.outlineStyle,color:cs.outlineColor,offset:parseFloat(cs.outlineOffset)},shadow:cs.boxShadow==='none'?'':cs.boxShadow.slice(0,60),
    visibleSkip:/skip/.test(e.className)?r.width>10:null}
})()`;
export const INTERACTIVE = `(function(){
  return [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[tabindex],[role=button],[role=tab],[role=radio]')].filter(function(e){
    const r=e.getBoundingClientRect(),cs=getComputedStyle(e);
    return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&!e.disabled&&e.tabIndex>=0&&!e.closest('[inert],[hidden],.sr-only')&&!(e.classList.contains('skip')&&false)
  }).map(function(e){return (e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+':'+((e.getAttribute('aria-label')||e.textContent||e.value||'').replace(/\\s+/g,' ').trim().slice(0,24)))})
})()`;
const parseRgb = (s) => { const m = /rgba?\(([^)]+)\)/.exec(s); if (!m) return null; const p = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; };

/** Is the focus indicator really on screen? Takes the pixels around the focused element: the ring's pixels (offset + half the width outside the edge) must differ
 *  from the surrounding pixels by 3:1 on every side. This catches a ring clipped by an overflow:hidden parent, or one drawn in the background colour. */
export async function ringCheck(c, f) {
  const [vx, vy, w, h] = f.rect; const x = vx + f.sx, y = vy + f.sy; const pad = 14, cx = Math.max(0, x - pad), cy = Math.max(0, y - pad);
  const cw = Math.min(w + 2 * pad, 4000), ch = Math.min(h + 2 * pad, 2000);
  if (cw < 4 || ch < 4) return { sides: 0 };
  const shot = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true, clip: { x: cx, y: cy, width: cw, height: ch, scale: 1 } });
  const img = decodePng(Buffer.from(shot.result.data, 'base64'));
  const o = f.outline, hasOutline = o.style !== 'none' && o.w > 0;
  const dRing = hasOutline ? o.offset + o.w / 2 : 1.5, dBg = hasOutline ? o.offset + o.w + 5 : 7;
  const at = (px, py) => { const ix = Math.round(px - cx), iy = Math.round(py - cy); return ix < 0 || iy < 0 || ix >= img.w || iy >= img.h ? null : img.px(ix, iy); };
  // each side is checked at five places along it; at each the pixels from just inside the ring's band to just outside are compared with the pixel beyond the band
  let sides = 0; const per = [];
  const lo = Math.floor(dRing - o.w / 2 - 1.5), hi = Math.ceil(dRing + o.w / 2 + 1.5);
  for (const side of ['top', 'bottom', 'left', 'right']) {
    let best = 1;
    for (const f2 of [0.1, 0.3, 0.5, 0.7, 0.9]) {
      const along = side === 'top' || side === 'bottom' ? x + w * f2 : y + h * f2;
      const pt = (d) => side === 'top' ? [along, y - d] : side === 'bottom' ? [along, y + h + d] : side === 'left' ? [x - d, along] : [x + w + d, along];
      const ref = at(...pt(dBg)); if (!ref) continue;
      for (let d = lo; d <= hi; d++) { const px = at(...pt(d)); if (px) best = Math.max(best, ratio(px, ref)); }
    }
    per.push(+best.toFixed(2)); if (best >= 3) sides++;
  }
  return { sides, per };
}

/** Presses Tab through the page and records each stop. Stops when focus leaves the document, returns to the first element, or after `max` presses. */
export async function tabThrough(c, { max = 70, rings = true } = {}) {
  const stops = []; const seen = new Set();
  for (let i = 0; i < max; i++) {
    await press(c, 'Tab');
    // smooth scrolling is on (unless reduced motion): wait for the page to stop moving before looking at where focus is
    let prev = -1; for (let k = 0; k < 30; k++) { const y = await c.ev('scrollY'); if (y === prev) break; prev = y; await sleep(60); }
    await sleep(260);   // hover/focus transitions (0.2 s) have finished
    const f = await c.ev(FOCUS);
    if (!f) break;
    const k = f.tag + f.id + f.name + f.rect.join(',');
    if (seen.has(k)) break; seen.add(k);
    if (rings) f.ring = await ringCheck(c, f);
    stops.push(f);
  }
  return stops;
}
export const brief = (s) => `${s.tag}${s.role ? '[' + s.role + ']' : ''}${s.id ? '#' + s.id : ''} “${s.name}” @${s.rect[0]},${s.rect[1]}`;
