import { launch, sleep, open, click, shot, errors, MEASURE, waitFor, APPLY } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer(); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = {};
for (const [name, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) {
  for (const lang of ['en', 'fa']) {
    await open(c, '/demo/waiting', { w, h, lang });
    await waitFor(c, `!!document.querySelector('.appr .opts .opt')`);
    await click(c, '.appr .opts .opt');                       // the first stop is the worker question: answer it, the replay goes on
    out[`${name}-${lang}-appeared`] = await waitFor(c, `!!document.querySelector('.appr .btn-p')`);
    await sleep(900);
    const m = await c.ev(MEASURE); const a = await c.ev(APPLY);
    out[`${name}-${lang}`] = { overflow: m.overflow, under44: m.under44, dir: m.dir, apply: a };
    await shot(c, `${OUT}/s2-waiting-${name}-${lang}.png`);
  }
}
out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop();
