// Accessibility measurements that run in headless Chrome over the DevTools protocol, no extra packages.
import zlib from 'node:zlib';
import { sleep } from '../helpers.mjs';

/** Minimal PNG decoder: 8-bit, non-interlaced, colour type 2 or 6 (what Chrome writes). Returns {w,h,px(x,y)->[r,g,b]}. */
export function decodePng(buf) {
  let o = 8, w = 0, h = 0, ct = 0; const idat = [];
  while (o < buf.length) {
    const len = buf.readUInt32BE(o), type = buf.toString('ascii', o + 4, o + 8), d = buf.subarray(o + 8, o + 8 + len); o += 12 + len;
    if (type === 'IHDR') { w = d.readUInt32BE(0); h = d.readUInt32BE(4); if (d[8] !== 8 || d[12] !== 0) throw new Error('unsupported PNG'); ct = d[9]; } else if (type === 'IDAT') idat.push(d); else if (type === 'IEND') break;
  }
  const bpp = ct === 6 ? 4 : 3, stride = w * bpp, raw = zlib.inflateSync(Buffer.concat(idat)), out = Buffer.alloc(h * stride);
  for (let y = 0; y < h; y++) {
    const f = raw[y * (stride + 1)], line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1)), cur = out.subarray(y * stride, (y + 1) * stride), prev = y ? out.subarray((y - 1) * stride, y * stride) : null;
    for (let i = 0; i < stride; i++) {
      const a = i >= bpp ? cur[i - bpp] : 0, b = prev ? prev[i] : 0, c = prev && i >= bpp ? prev[i - bpp] : 0; let v = line[i];
      if (f === 1) v += a; else if (f === 2) v += b; else if (f === 3) v += (a + b) >> 1; else if (f === 4) { const p = a + b - c, pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c); v += pa <= pb && pa <= pc ? a : pb <= pc ? b : c; }
      cur[i] = v & 255;
    }
  }
  return { w, h, px: (x, y) => { const i = y * stride + x * bpp; return [out[i], out[i + 1], out[i + 2]]; } };
}
const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
export const lum = ([r, g, b]) => 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
export const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

/** In the page: every visible piece of text with its rectangle (page coordinates) and the colour(s) it is painted with. */
const COLLECT = `(function(){
  const out=[];
  const parse=(s)=>{const m=/rgba?\\(([^)]+)\\)/.exec(s);if(!m)return null;const p=m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);return [p[0],p[1],p[2],p.length>3?p[3]:1]};
  const opacityOf=(el)=>{let o=1;for(let e=el;e&&e.nodeType===1;e=e.parentElement){const cs=getComputedStyle(e);o*=parseFloat(cs.opacity);if(cs.display==='none'||cs.visibility==='hidden')return 0}return o};
  const rectOf=(r)=>({x:r.left+scrollX,y:r.top+scrollY,w:r.width,h:r.height});
  const seen=new Set();
  const push=(el,node,kind,label)=>{
    const op=opacityOf(el);if(op<0.02)return;
    if(el.closest('.sr-only,[hidden],script,style,noscript'))return;
    if(el.closest('.skip')&&!el.closest('.skip').matches(':focus'))return;
    const range=document.createRange();range.selectNodeContents(node);const rects=[...range.getClientRects()].filter(r=>r.width>1&&r.height>1);if(!rects.length)return;
    // text scrolled out of a box (overflow auto/hidden) is not on screen: skip it, and measure only the part inside
    const clipped=(()=>{let L=-1e9,T=-1e9,R=1e9,B=1e9;for(let p=el.parentElement;p&&p!==document.documentElement;p=p.parentElement){const k=getComputedStyle(p);if(/(auto|scroll|hidden|clip)/.test(k.overflowX+k.overflowY)&&p!==document.body){const b=p.getBoundingClientRect();L=Math.max(L,b.left);T=Math.max(T,b.top);R=Math.min(R,b.right);B=Math.min(B,b.bottom)}}
      const r=rects[0];return r.right<=L||r.left>=R||r.bottom<=T||r.top>=B})();
    if(clipped)return;
    const cs=getComputedStyle(el);
    let cols=[],grad=false;
    if(kind==='svg'){const f=parse(cs.fill);if(!f)return;cols=[f]}
    else if(kind==='ph'){const f=parse(getComputedStyle(el,'::placeholder').color);if(!f)return;cols=[f]}
    else{
      const fill=parse(cs.webkitTextFillColor||cs.color);
      if(fill&&fill[3]<0.02){
        let g=null;for(let e=el;e&&e.nodeType===1;e=e.parentElement){const k=getComputedStyle(e);if(/text/.test(k.webkitBackgroundClip||k.backgroundClip)&&/gradient/.test(k.backgroundImage)){g=e;break}}
        if(!g)return;grad=true;cols=(getComputedStyle(g).backgroundImage.match(/rgba?\\([^)]+\\)/g)||[]).map(parse).filter(Boolean);g.setAttribute('data-a11y-gt','1')}
      else if(fill) cols=[fill]; else return;
    }
    const size=parseFloat(cs.fontSize);
    for(const c of cols){c[3]=c[3]*op}
    const key=label+'|'+rects.map(r=>Math.round(r.left)+','+Math.round(r.top)).join(';');if(seen.has(key))return;seen.add(key);
    const r0=rects[0];
    out.push({fixed:!!el.closest('.sticky-bar'),text:(node.nodeValue||el.value||el.getAttribute('placeholder')||'').trim().slice(0,50),kind,grad,cols,size,bold:parseInt(cs.fontWeight)>=700,rects:rects.slice(0,3).map(rectOf),disabled:!!el.closest(':disabled,[aria-disabled=true]'),cls:(typeof el.className==='string'?el.className:(el.className.baseVal||''))||el.tagName});
  };
  const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let n;
  while(n=w.nextNode()){if(!n.nodeValue.trim())continue;const el=n.parentElement;if(!el)continue;push(el,n,el.closest('svg')?'svg':'html',n.nodeValue.trim())}
  for(const i of document.querySelectorAll('input,textarea')){const cs=getComputedStyle(i);if(i.type==='file'||i.type==='hidden'||i.type==='checkbox'||i.type==='radio'||cs.visibility==='hidden')continue;const r=i.getBoundingClientRect();if(r.width<2)continue;
    if(i.getAttribute('placeholder')&&!i.value){const fake=document.createTextNode(i.getAttribute('placeholder'));push(i,{nodeValue:i.getAttribute('placeholder'),nodeType:3,_r:r,__proto__:null},'ph','ph'+i.id)}}
  return {items:out,w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,vw:innerWidth,vh:innerHeight,sx:scrollX,sy:scrollY};
})()`;
// range.selectNodeContents needs a real node, so placeholder text is handled by selecting the input's own box instead
const COLLECT_FIX = COLLECT.replace("const range=document.createRange();range.selectNodeContents(node);const rects=[...range.getClientRects()]", "const range=document.createRange();const rects=(kind==='ph'?[el.getBoundingClientRect()]:(range.selectNodeContents(node),[...range.getClientRects()]))")
  .replace(/const fake=[^;]+;push\(i,\{[^}]*\},'ph','ph'\+i\.id\)/, "push(i,{nodeValue:i.getAttribute('placeholder')},'ph','ph'+i.id)");

const HIDE = `(function(){const s=document.createElement('style');s.id='a11y-hide';s.textContent='*{-webkit-text-fill-color:transparent!important;color:transparent!important;text-shadow:none!important;caret-color:transparent!important}svg text,svg tspan{fill:transparent!important}input::placeholder,textarea::placeholder{color:transparent!important}[data-a11y-gt]{background:none!important}';document.head.appendChild(s)})()`;
const SHOW = `(function(){var s=document.getElementById('a11y-hide');if(s)s.remove();document.querySelectorAll('[data-a11y-gt]').forEach(function(e){e.removeAttribute('data-a11y-gt')})})()`;

/** Contrast of every text on the current screen, measured on the pixels. Two captures of the same frame (animations and the landing clock frozen for the
 *  moment): one with the text, one with the text hidden. A pixel counts as ink when it differs between the two and has the text's colour; its contrast is that
 *  colour against what the hidden capture shows at the same pixel, so glows, grain, glass, halos and lines behind the text are all included. Text too small to
 *  have ink pixels falls back to the worst background pixel in its box. Fixed bars are measured in a viewport capture of their own. */
export async function measureContrast(c, { min = 4.5, full = true, clip = null, skip = null } = {}) {
  const data = await c.ev(COLLECT_FIX);
  await c.send('Animation.enable').catch(() => {}); await c.send('Animation.setPlaybackRate', { playbackRate: 0 }).catch(() => {});
  await c.ev(`(window.__lp||[]).forEach(function(a){a.__f=a.frozen;a.frozen=true})`);
  const cap = async (hide) => {
    if (hide) await c.ev(HIDE);
    await sleep(40);
    const shot = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full || !!clip, fromSurface: true, ...(clip ? { clip: { ...clip, scale: 1 } } : {}) });
    return decodePng(Buffer.from(shot.result.data, 'base64'));
  };
  const hideFixed = `(function(){var s=document.createElement('style');s.id='a11y-fixed';s.textContent='.sticky-bar{visibility:hidden!important}';document.head.appendChild(s)})()`;
  const showFixed = `(function(){var s=document.getElementById('a11y-fixed');if(s)s.remove()})()`;
  const hasFixed = data.items.some((i) => i.fixed);
  let vis, hid, visV, hidV;
  try {
    if (hasFixed) await c.ev(hideFixed);
    vis = await cap(false); hid = await cap(true); await c.ev(SHOW);
    if (hasFixed) { await c.ev(showFixed); visV = await (async () => { const sh = await c.send('Page.captureScreenshot', { format: 'png', fromSurface: true }); return decodePng(Buffer.from(sh.result.data, 'base64')); })(); await c.ev(HIDE); const sh2 = await c.send('Page.captureScreenshot', { format: 'png', fromSurface: true }); hidV = decodePng(Buffer.from(sh2.result.data, 'base64')); await c.ev(SHOW); }
  } finally {
    await c.ev(SHOW).catch(() => {}); await c.ev(showFixed).catch(() => {});
    await c.ev(`(window.__lp||[]).forEach(function(a){a.frozen=a.__f})`); await c.send('Animation.setPlaybackRate', { playbackRate: 1 }).catch(() => {});
  }
  const ox = clip ? clip.x : 0, oy = clip ? clip.y : 0;
  const fails = []; let checked = 0, worst = 99, approx = 0;
  const dist = (p, q) => Math.abs(p[0] - q[0]) + Math.abs(p[1] - q[1]) + Math.abs(p[2] - q[2]);
  for (const it of data.items) {
    if (skip && skip.test(it.cls)) continue;
    for (const r0 of it.rects) {
      const useV = it.fixed && visV;
      const A = useV ? visV : vis, B = useV ? hidV : hid;
      const r = { x: r0.x - (useV ? data.sx : ox), y: r0.y - (useV ? data.sy : oy), w: r0.w, h: r0.h };
      const x0 = Math.max(0, Math.floor(r.x)), y0 = Math.max(0, Math.floor(r.y)), x1 = Math.min(A.w - 1, Math.ceil(r.x + r.w)), y1 = Math.min(A.h - 1, Math.ceil(r.y + r.h));
      if (x1 <= x0 || y1 <= y0) continue;
      let low = 99, bad = null, ink = 0;
      const sx = Math.max(1, Math.floor((x1 - x0) / 120)), sy = Math.max(1, Math.floor((y1 - y0) / 40));
      for (let y = y0; y < y1; y += sy) for (let x = x0; x < x1; x += sx) {
        const v = A.px(x, y), b = B.px(x, y);
        if (dist(v, b) < 60) continue;
        for (const col of it.cols) {
          const a = col[3], fg = [0, 1, 2].map((k) => Math.round(col[k] * a + b[k] * (1 - a)));
          if (dist(v, fg) <= 24) { ink++; const q = ratio(fg, b); if (q < low) { low = q; bad = { fg, bg: b }; } break; }
        }
      }
      let how = 'ink';
      if (ink < 2) {                         // too small or too faint to have ink pixels: the worst background pixel in the box
        how = 'box'; approx++; low = 99; bad = null;
        const nx = Math.min(10, x1 - x0), ny = Math.min(3, y1 - y0);
        for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) {
          const bg = B.px(x0 + Math.floor(((i + 0.5) * (x1 - x0)) / nx), y0 + Math.floor(((j + 0.5) * (y1 - y0)) / ny));
          for (const col of it.cols) { const a = col[3], fg = [0, 1, 2].map((k) => Math.round(col[k] * a + bg[k] * (1 - a))); const q = ratio(fg, bg); if (q < low) { low = q; bad = { fg, bg }; } }
        }
      }
      checked++; worst = Math.min(worst, low);
      if (low < min) fails.push({ text: it.text, ratio: +low.toFixed(2), size: it.size, cls: it.cls.slice(0, 30), disabled: it.disabled, fg: bad.fg.join(','), bg: bad.bg.join(','), grad: it.grad, how });
      break;
    }
  }
  return { checked, worst: +worst.toFixed(2), approx, fails };
}
