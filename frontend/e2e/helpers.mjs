// Test helpers: drive headless Chrome over the DevTools protocol (no extra packages). Used by the e2e scripts in this folder.
import fs from 'node:fs';
import { launch, sleep } from './cdp.mjs';
export { launch, sleep };
const base = () => process.env.APP_URL;
export async function open(c, path, { w = 1440, h = 900, rm = false, lang, theme } = {}) {
  await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
  await c.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: rm ? 'reduce' : 'no-preference' }] });
  c.events.length = 0;
  await c.send('Page.navigate', { url: base() + path });
  await sleep(1200);
  if (lang || theme) { await c.ev(`(function(){${lang ? `localStorage.setItem('lang','${lang}');` : ''}${theme ? `localStorage.setItem('theme','${theme}');` : ''}})()`); await c.send('Page.reload'); await sleep(1500); }
}
export async function type(c, sel, text) {
  await c.ev(`(function(){var e=document.querySelector(${JSON.stringify(sel)});e.focus();e.select&&e.select()})()`);
  await c.send('Input.insertText', { text });
}
export async function click(c, sel) { await c.ev(`document.querySelector(${JSON.stringify(sel)}).click()`); }
export async function shot(c, file) { const r = await c.send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(file, Buffer.from(r.result.data, 'base64')); }
export function errors(c) { return c.events.filter((e) => e.method === 'Runtime.exceptionThrown' || (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error') || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error' && !/favicon/.test(e.params.entry.url || ''))).map((e) => JSON.stringify(e.params).slice(0, +(process.env.ERRLEN || 200))); }
/** Layout facts every page must satisfy: overflow, small controls, direction. */
export const MEASURE = `(function(){var d=document.documentElement;var small=[].slice.call(document.querySelectorAll('button,a[href],input,select,textarea,[role=radio]')).filter(function(e){var r=e.getBoundingClientRect();var cs=getComputedStyle(e);return r.width>0&&r.height>0&&cs.visibility!=='hidden'&&r.height<44&&!e.closest('.sr-only')&&!(e.tagName==='A'&&e.closest('p,li,.hint,.lead'))&&!((e.type==='checkbox'||e.type==='radio')&&e.closest('label')&&e.closest('label').getBoundingClientRect().height>=44)&&!e.classList.contains('sr-only')&&!e.classList.contains('skip')}).map(function(e){return (e.tagName+'.'+(e.className||'')+':'+Math.round(e.getBoundingClientRect().height)+':'+(e.textContent||'').trim().slice(0,18))});
 return {overflow:[d.scrollWidth,d.clientWidth],under44:small,dir:d.dir,lang:d.lang,title:document.title}})()`;
export async function waitFor(c, expr, ms = 15000) { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await c.ev(expr)) return true; } catch { /* not ready */ } await sleep(150); } return false; }
/** Where is the Apply button, in viewport pixels? */
export const APPLY = `(function(){var b=document.querySelector('.appr .btn-p');if(!b)return null;var r=b.getBoundingClientRect();return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height),bottom:Math.round(r.bottom),vh:innerHeight,visible:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth}})()`;
/** Answers some API calls from the page with the given bodies (everything else goes to the real backend). */
export async function mockApi(c, table) {
  await c.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/*', requestStage: 'Request' }] });
  const pump = setInterval(() => { while (c.events.length) { const e = c.events.shift(); if (e.method !== 'Fetch.requestPaused') continue; const u = new URL(e.params.request.url); const key = e.params.request.method + ' ' + u.pathname; const hit = table[key];
    if (hit !== undefined) c.send('Fetch.fulfillRequest', { requestId: e.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(typeof hit === 'function' ? hit(u) : hit)).toString('base64') }).catch(() => {});
    else c.send('Fetch.continueRequest', { requestId: e.params.requestId }).catch(() => {}); } }, 15);
  return () => { clearInterval(pump); c.send('Fetch.disable').catch(() => {}); };
}
export async function setFiles(c, selector, files) {
  const { result } = await c.send('Runtime.evaluate', { expression: `document.querySelector(${JSON.stringify(selector)})` });
  await c.send('DOM.enable'); await c.send('DOM.setFileInputFiles', { objectId: result.result.objectId, files });
}
