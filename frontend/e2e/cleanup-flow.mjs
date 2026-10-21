// A REAL deploy through the real API (stand-in for api.liara.ir that also answers the deploy and delete calls), approved in the page, then "Delete what this run
// created", then a reload, the dashboard and the project page, in both languages. Nothing leaves this machine: the proxy only tunnels to the local stand-in.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { launch, sleep, open, type, click, shot, errors, waitFor, MEASURE } from './helpers.mjs';
import { startServer } from './server.mjs';
import { startFakeLiara } from './fake-liara.mjs';
import { measureContrast } from './lib/a11y.mjs';
const liara = await startFakeLiara({ writes: true });
const srv = await startServer({ env: liara.env }); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const zip = path.join(os.tmpdir(), 'tiny-compose.zip'); execFileSync('python3', [path.resolve(import.meta.dirname, 'fixtures/make-zip.py'), zip]);
const user = 'clean' + Date.now().toString(36); const H = { 'X-Requested-With': 'XMLHttpRequest' }; let cookie = '';
const api = async (method, p, body) => {
  const r = await fetch(srv.url + p, { method, headers: { ...H, ...(body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), cookie }, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const sc = r.headers.getSetCookie?.(); if (sc?.length) cookie = sc.map((x) => x.split(';')[0]).join('; ');
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t; } return { status: r.status, body: j };
};
await api('POST', '/api/auth/register', { username: user, password: 'password123' });
await api('PUT', '/api/settings', { liara_token: 'placeholder-token-not-valid-0000', language: 'en' });
const fd = new FormData(); fd.append('name', 'tiny'); fd.append('file', new Blob([fs.readFileSync(zip)], { type: 'application/zip' }), 'tiny.zip');
const proj = await api('POST', '/api/projects', fd); const pid = proj.body.id;
const c = await launch();
await open(c, '/login'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
const run = await api('POST', `/api/projects/${pid}/runs`, { mode: 'deploy' }); const rid = run.body.id;
await c.send('Page.navigate', { url: `${srv.url}/runs/${rid}` });
out.approvalShown = await waitFor(c, `!!document.querySelector('.appr .btn-p')`, 20000);
await click(c, '.appr .ap-acts .btn-p');
out.reachedLive = await waitFor(c, `document.querySelector('.run').dataset.situation==='live'`, 90000);
const LIVE = `(function(){var t=document.body.innerText;return {situation:document.querySelector('.run').dataset.situation,h1:document.querySelector('.h1').textContent,openApp:!!document.querySelector('a[href*="liara.run"]'),copyBtn:!!document.querySelector('.url-row'),freeWarning:/free address|نشانی رایگان/i.test(t)&&!!document.querySelector('.result.live, .tile .big-clock'),deleteBtn:!!document.querySelector('.run .btn-d'),tiles:[...document.querySelectorAll('.tile')].map(x=>x.innerText.replace(/\\s+/g,' ').trim()),removedCard:!!document.querySelector('.result.removed'),listed:[...document.querySelectorAll('.result.removed li')].map(x=>x.innerText.replace(/\\s+/g,' ').trim()),when:(document.querySelector('.result.removed time')||{}).textContent||null,timeline:document.querySelectorAll('.tl-item,.tlitem,.timeline > *').length,pipeline:[...document.querySelectorAll('.pipe .pn')].map(n=>n.dataset.state).join(',')}})()`;
out.beforeCleanup = await c.ev(LIVE); await shot(c, `${OUT}/cl-live.png`);
out.apiBefore = { cleaned_at: (await api('GET', `/api/runs/${rid}`)).body.cleaned_at };
// clean up through the page
await click(c, '.run .btn-d'); await waitFor(c, `!!document.querySelector('#cl-n')`);
out.dialogFocus = await c.ev(`document.activeElement.id`);
await c.ev(`document.querySelector('#cl-n').focus()`); await c.send('Input.insertText', { text: 'tiny' }); await sleep(200);
await click(c, '[role=dialog] .btn-d');
const t0 = Date.now(); out.removedShownAfter_ms = (await waitFor(c, `document.querySelector('.run').dataset.situation==='removed'`, 10000)) ? Date.now() - t0 : 'NEVER';
await sleep(500); out.afterCleanup = await c.ev(LIVE); out.focusAfter = await c.ev(`document.activeElement.tagName`); out.titleAfter = await c.ev('document.title');
out.announce = await c.ev(`(document.querySelector('[data-live=polite]')||{}).textContent`);
await shot(c, `${OUT}/cl-removed.png`);
out.apiAfter = (await api('GET', `/api/runs/${rid}`)).body.cleaned_at;
out.standIn = { deletes: liara.log.filter((l) => l.startsWith('DELETE')) };
// reload
await c.send('Page.reload'); await waitFor(c, `document.querySelector('.run')`, 15000); await sleep(1200);
out.afterReload = await c.ev(LIVE); await shot(c, `${OUT}/cl-reload.png`);
// project page and dashboard
const projApi = (await api('GET', `/api/projects/${pid}`)).body; out.projectApi = { live: projApi.live, lastRunCleaned: projApi.last_run?.cleaned_at };
await open(c, `/projects/${pid}`); await waitFor(c, `!!document.querySelector('h1')`, 15000); await sleep(1200);
out.projectPage = await c.ev(`(function(){var t=document.body.innerText;return {liveCard:!!document.querySelector('.result.live'),openLink:!!document.querySelector('a[href*="liara.run"]'),mentionsLive:/Your app is live/i.test(t),badges:[...document.querySelectorAll('.badge')].map(b=>b.innerText.trim()),removedText:/Removed from Liara/.test(t),nowCost:(document.querySelector('.aside, .proj-aside')||{}).innerText?.slice(0,160)||null}})()`);
await shot(c, `${OUT}/cl-project.png`);
await open(c, '/dashboard'); await waitFor(c, `document.querySelector('.prow .nm')`, 15000); await sleep(1200);
out.dashboard = await c.ev(`(function(){var r=document.querySelector('.prow:not(.phead)');return {row:r.innerText.replace(/\\s+/g,' ').trim(),link:!!r.querySelector('a[href*="liara.run"], .url'),tiles:[...document.querySelectorAll('.card .big, .tiles .big')].map(x=>x.innerText.replace(/\\s+/g,' ').trim()).slice(0,4)}})()`);
await shot(c, `${OUT}/cl-dashboard.png`);
// Persian
await api('PUT', '/api/settings', { language: 'fa' });
await c.ev(`localStorage.setItem('lang','fa')`);
await open(c, `/runs/${rid}`, { lang: 'fa' }); await waitFor(c, `document.querySelector('.run')`, 15000); await sleep(1200);
out.persian = await c.ev(LIVE); out.persianLayout = await c.ev(MEASURE); await shot(c, `${OUT}/cl-fa.png`);
await open(c, `/projects/${pid}`, { lang: 'fa' }); await sleep(1500); out.persianProject = await c.ev(`[...document.querySelectorAll('.badge')].map(b=>b.innerText.trim())`);
for (const w of [768, 390]) { await open(c, `/runs/${rid}`, { w, h: w > 600 ? 1024 : 844, lang: 'fa' }); await waitFor(c, `document.querySelector('.run')`, 15000); await sleep(900); const m = await c.ev(MEASURE); out['persian@' + w] = { sideways: m.overflow[0] > m.overflow[1], under44: m.under44.filter((x) => !/skip/.test(x)) }; }
out.contrast = {};
for (const [lang, theme] of [['en', 'dark'], ['en', 'light'], ['fa', 'dark'], ['fa', 'light']]) {
  await c.ev(`localStorage.setItem('lang','${lang}');localStorage.setItem('theme','${theme}')`); await api('PUT', '/api/settings', { language: lang });
  await open(c, `/runs/${rid}`); await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='removed'`, 15000); await sleep(1200);
  const r = await measureContrast(c); out.contrast[lang + '-' + theme] = { texts: r.checked, fails: r.fails.filter((f) => !f.disabled).map((f) => `${f.text} ${f.ratio}`) };
}
out.consoleErrors = errors(c);
out.writesGoOnlyToTheStandIn = [...new Set(liara.log.filter((l) => !l.startsWith('GET')))];
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop(); liara.stop(); process.exit(0);
