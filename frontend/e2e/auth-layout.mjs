import { launch, sleep, open, MEASURE, shot } from './helpers.mjs';
import { startServer } from './server.mjs';
import { press, tabThrough, brief } from './lib/keys.mjs';
const srv = await startServer(); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = [];
await open(c, '/login'); 
for (const lang of ['en', 'fa']) for (const path of ['/login', '/register']) for (const [w, h] of [[1920, 1080], [1440, 900], [768, 1024], [390, 844]]) {
  await open(c, path, { w, h, lang: lang === 'fa' ? 'fa' : undefined }); await sleep(600);
  const g = await c.ev(`(function(){var f=document.querySelector('form').getBoundingClientRect(),h=document.querySelector('.auth-in h1').getBoundingClientRect(),vw=document.documentElement.clientWidth;return {cardL:Math.round(f.left),cardR:Math.round(vw-f.right),cardW:Math.round(f.width),cardTop:Math.round(f.top),cardBottom:Math.round(innerHeight-f.bottom),h1Center:Math.round(h.left+h.width/2-vw/2),textAlign:getComputedStyle(document.querySelector('.auth-in h1')).textAlign}})()`);
  const m = await c.ev(MEASURE); const stops = await tabThrough(c, { max: 12 });
  out.push({ lang, path, w, ...g, under44: m.under44, sideways: m.overflow[0] > m.overflow[1], tab: stops.map((s) => s.name.slice(0, 14)).join(' > '), rings: stops.filter((s) => s.ring && s.ring.sides < 3).length });
  if (path === '/login') await shot(c, `${OUT}/auth-${lang}-${w}.png`);
}
for (const o of out) console.log(JSON.stringify(o));
c.close(); srv.stop(); process.exit(0);
