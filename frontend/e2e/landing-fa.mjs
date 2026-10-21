// The Persian landing page at every width: overflow, small controls, contrast on every text (dark and light, hero at several moments), text cut off by a box.
import { launch, sleep, open, waitFor, MEASURE, shot } from './helpers.mjs';
import { startServer } from './server.mjs';
import { measureContrast } from './lib/a11y.mjs';
const srv = await startServer(); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = [];
const CLIP = `[...document.querySelectorAll('.lp *')].filter(e=>{const cs=getComputedStyle(e);if(cs.display==='none'||!e.childNodes.length)return false;const own=[...e.childNodes].some(n=>n.nodeType===3&&n.nodeValue.trim());return own&&(cs.overflow!=='visible')&&(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1)&&!e.closest('svg')}).map(e=>e.tagName+'.'+String(e.className).slice(0,20)+' "'+e.textContent.trim().slice(0,20)+'"').slice(0,5)`;
for (const theme of ['dark', 'light']) for (const [w, h] of [[320, 700], [390, 844], [768, 1024], [1024, 800], [1440, 900], [1920, 1080]]) for (const t of ['10.4', '26.4']) {
  await open(c, `/?t=${t}&nt=6.2`, { w, h, lang: process.env.LP_LANG === 'en' ? undefined : 'fa' }); await c.ev(`localStorage.setItem('theme','${theme}')`); await c.send('Page.reload'); await waitFor(c, `!!document.querySelector('.lp')`); await sleep(900);
  const m = await c.ev(MEASURE); const clip = await c.ev(CLIP); const r = await measureContrast(c);
  out.push({ theme, w, t, sideways: m.overflow[0] > m.overflow[1], under44: m.under44.filter((x) => !/skip/.test(x)), clipped: clip, texts: r.checked, fails: r.fails.filter((f) => !f.disabled).map((f) => `${f.text} ${f.ratio}`) });
  if (theme === 'dark' && t === '10.4' && (w === 390 || w === 768)) { const full = await c.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true }); (await import('node:fs')).writeFileSync(`${OUT}/lf-${w}.png`, Buffer.from(full.result.data, 'base64')); }
}
for (const o of out) console.log(JSON.stringify(o));
c.close(); srv.stop(); process.exit(0);
