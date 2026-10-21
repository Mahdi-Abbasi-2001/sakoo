// Responsive layout and document structure on every page and state at one viewport width.
// usage: node e2e/step6-layout.mjs <width> <lang>
import fs from 'node:fs';
import { launch, errors, MEASURE } from './helpers.mjs';
import { startServer } from './server.mjs';
import { walk } from './scenarios.mjs';
const [width = '390', lang = 'en'] = process.argv.slice(2);
const srv = await startServer(); const c = await launch();
const EDGE = `(function(){
  const vw=document.documentElement.clientWidth;const sel=(e)=>e.tagName.toLowerCase()+(typeof e.className==='string'&&e.className?'.'+e.className.trim().split(/\\s+/).slice(0,2).join('.'):'');
  const scroller=(e)=>{for(let p=e.parentElement;p&&p!==document.body;p=p.parentElement){const o=getComputedStyle(p).overflowX;if(o==='auto'||o==='scroll'||o==='hidden'||o==='clip'){const r=p.getBoundingClientRect();if(r.left>=-1&&r.right<=vw+1)return true}}return false};
  const out=[];for(const e of document.body.querySelectorAll('*')){if(e.closest('svg')&&e.tagName!=='svg')continue;const cs=getComputedStyle(e);if(cs.display==='none'||cs.visibility==='hidden'||cs.position==='fixed'&&false)continue;const r=e.getBoundingClientRect();if(r.width<2||r.height<2)continue;
    if((r.right>vw+1||r.left<-1)&&!scroller(e)&&!e.closest('.sr-only,.skip'))out.push(sel(e)+' '+Math.round(r.left)+'..'+Math.round(r.right))}
  const clipped=[...document.querySelectorAll('body *')].filter(e=>{const cs=getComputedStyle(e);return cs.textOverflow==='ellipsis'&&e.scrollWidth>e.clientWidth+1&&cs.visibility!=='hidden'}).map(e=>sel(e)+' "'+(e.textContent||'').trim().slice(0,24)+'"');
  const ids={};document.querySelectorAll('[id]').forEach(e=>{ids[e.id]=(ids[e.id]||0)+1});
  return {offEdge:out.slice(0,6),ellipsis:clipped.slice(0,6),dupIds:Object.keys(ids).filter(k=>ids[k]>1),vw,minLine:0}
})()`;
const STRUCT = `(function(){
  const probs=[];const vis=(e)=>{const r=e.getBoundingClientRect(),cs=getComputedStyle(e);return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&!e.closest('[hidden]')};
  const text=(e)=>(e.textContent||'').replace(/\\s+/g,' ').trim();
  const name=(e)=>{const l=e.getAttribute('aria-labelledby');if(l){const t=l.split(' ').map(i=>{const x=document.getElementById(i);return x?text(x):''}).join(' ').trim();if(t)return t}
    return e.getAttribute('aria-label')||(e.labels&&e.labels.length?text(e.labels[0]):'')||text(e)||e.getAttribute('title')||(e.querySelector('svg[aria-label],img[alt]')?'x':'')||''};
  for(const e of document.querySelectorAll('button,a[href],input:not([type=hidden]),select,textarea,[role=button],[role=tab],[role=radio]')){if(!vis(e)||e.closest('.sr-only')&&!e.classList.contains('skip'))continue;if(!name(e))probs.push('no name: '+e.tagName.toLowerCase()+'.'+(e.className||'').toString().slice(0,20))}
  for(const e of document.querySelectorAll('[aria-labelledby],[aria-describedby],[aria-controls]')){for(const a of ['aria-labelledby','aria-describedby','aria-controls']){const v=e.getAttribute(a);if(v)for(const id of v.split(' '))if(!document.getElementById(id)&&!(a==='aria-controls'&&e.getAttribute('role')==='tab'))probs.push(a+' -> missing #'+id)}}
  const hs=[...document.querySelectorAll('h1,h2,h3,h4')].filter(vis).map(h=>+h.tagName[1]);
  const h1=hs.filter(x=>x===1).length;if(h1!==1)probs.push('h1 count '+h1);
  for(let i=1;i<hs.length;i++)if(hs[i]>hs[i-1]+1)probs.push('heading jumps h'+hs[i-1]+' -> h'+hs[i]);
  for(const s of document.querySelectorAll('svg[role=img]'))if(!s.getAttribute('aria-label')&&!s.getAttribute('aria-labelledby'))probs.push('svg img without name');
  for(const a of document.querySelectorAll('a[target=_blank]'))if(!/noopener/.test(a.rel))probs.push('target=_blank without noopener');
  const lm={main:document.querySelectorAll('main').length,header:document.querySelectorAll('header').length,nav:document.querySelectorAll('nav').length};
  if(lm.main!==1)probs.push('main landmarks '+lm.main);
  const inputsNoAuto=[...document.querySelectorAll('input[type=password],input#u,input#tok')].filter(e=>!e.getAttribute('autocomplete')).length;
  return {probs,hs:hs.join('')}
})()`;
const rows = []; const seen = new Map();
const add = (kind, label, items) => { for (const it of items) { const k = kind + '|' + it; if (!seen.has(k)) seen.set(k, { kind, item: it, where: [] }); seen.get(k).where.push(label); } };
await walk(c, { lang, base: +width, widths: [+width], async onState(label, w) {
  const m = await c.ev(MEASURE); const e = await c.ev(EDGE); const s = await c.ev(STRUCT);
  const sideways = m.overflow[0] > m.overflow[1];
  rows.push({ label, sideways });
  if (sideways) add('page scrolls sideways', label, [m.overflow.join('>')]);
  if (w <= 600) add('control under 44px', label, m.under44);
  add('element past the viewport edge', label, e.offEdge); add('ellipsis hides text', label, e.ellipsis); add('duplicate id', label, e.dupIds);
  add('structure', label, s.probs);
} });
const out = { width: +width, lang, states: rows.length, sidewaysStates: rows.filter((r) => r.sideways).length, problems: [...seen.values()].map((v) => ({ kind: v.kind, item: v.item, where: [...new Set(v.where)].slice(0, 3) })), consoleErrors: errors(c).length };
fs.writeFileSync(`/tmp/layout-${width}-${lang}.json`, JSON.stringify(out, null, 1)); console.log(JSON.stringify(out, null, 1));
c.close(); srv.stop();
process.exit(0);
