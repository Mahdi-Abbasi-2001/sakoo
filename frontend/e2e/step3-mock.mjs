// Dashboard and project page in every state a person can meet. A real backend cannot be put into "live" or "failed for lack of balance" without
// spending on Liara, so these API answers are written by hand in the shape of docs/api.md and the samples. Everything else is the real page.
import fs from 'node:fs'; import path from 'node:path';
import { launch, sleep, open, shot, errors, MEASURE, waitFor, mockApi } from './helpers.mjs';
import { startServer } from './server.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';
const srv = await startServer(); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const table = buildMock();
const stop = await mockApi(c, table);
await open(c, '/register'); // a real account so the shell and session are real
const sleepy = (ms) => new Promise((r) => setTimeout(r, ms));
await c.ev(`fetch('/api/auth/register',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({username:'mock${Date.now().toString(36)}',password:'password123'})})`); await sleepy(700);
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({liara_token:'placeholder-token-not-valid-0000'})})`); await sleepy(500);
for (const lang of ['en', 'fa']) {
  await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({language:'${lang}'})})`); await sleepy(300);
  for (const [n, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) {
    await open(c, '/dashboard', { w, h }); await waitFor(c, `document.querySelectorAll('.prow .nm').length===4`); await sleepy(1300);
    const m = await c.ev(MEASURE); out[`dashboard-${lang}-${n}`] = { overflow: m.overflow, under44: m.under44, dir: m.dir }; await shot(c, `${OUT}/s3m-dashboard-${lang}-${n}.png`);
    await open(c, '/projects/2', { w, h }); await waitFor(c, `!!document.querySelector('.rrow:not(.rhead)')`); await sleepy(1500);
    const p = await c.ev(MEASURE); out[`project-${lang}-${n}`] = { overflow: p.overflow, under44: p.under44, dir: p.dir }; await shot(c, `${OUT}/s3m-project-${lang}-${n}.png`);
  }
}
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({language:'en'})})`); await sleepy(300);
await open(c, '/dashboard'); await waitFor(c, `document.querySelectorAll('.prow .nm').length===4`); await sleepy(900);
out.rows = await c.ev(`[...document.querySelectorAll('.prow')].slice(1).map(r=>[...r.querySelectorAll('.pcell')].map(x=>x.textContent.replace(/\\s+/g,' ').trim()).join(' | ')).join('\\n')`);
out.summary = await c.ev(`[...document.querySelectorAll('.sumcards .card')].map(x=>x.textContent.replace(/\\s+/g,' ').trim()).join('\\n')`);
await open(c, '/projects/2'); await waitFor(c, `!!document.querySelector('.rrow:not(.rhead)')`); await sleepy(1500);
out.projectRuns = await c.ev(`[...document.querySelectorAll('.rrow:not(.rhead)')].map(r=>r.textContent.replace(/\\s+/g,' ').trim()).join('\\n')`);
out.projectEnv = await c.ev(`[...document.querySelectorAll('.projpage aside section:nth-child(2) .rw')].map(r=>r.textContent.replace(/\\s+/g,' ').trim()).join(' | ')`);
out.deleteDisabledUntilTyped = await c.ev(`document.querySelector('.danger .btn-d').disabled`);
out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1)); stop(); c.close(); srv.stop();
