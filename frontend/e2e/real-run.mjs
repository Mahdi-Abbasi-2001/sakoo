// A REAL run against the real API (same origin, throw-away data, no .env): register, save a placeholder Liara token, upload a tiny compose project,
// start a "deploy" run through the API. It pauses at the approval and creates nothing on Liara. Then the UI declines it, and we time how long the
// page takes to show the declined outcome. The placeholder token is never valid and never leaves this machine except toward Liara as a 401.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { launch, sleep, open, type, click, shot, errors, waitFor, APPLY } from './helpers.mjs';
import { startServer } from './server.mjs';
import { startFakeLiara } from './fake-liara.mjs';
const liara = await startFakeLiara();                       // see fake-liara.mjs: the backend is real, only api.liara.ir is stood in for
const srv = await startServer({ env: liara.env }); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const zip = path.join(os.tmpdir(), 'tiny-compose.zip'); execFileSync('python3', [path.resolve(import.meta.dirname, 'fixtures/make-zip.py'), zip]);
const user = 'real' + Date.now().toString(36); const H = { 'X-Requested-With': 'XMLHttpRequest' };
let cookie = '';
const api = async (method, p, body, extra = {}) => {
  const r = await fetch(srv.url + p, { method, headers: { ...H, ...(body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), cookie, ...extra }, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const sc = r.headers.getSetCookie?.(); if (sc?.length) cookie = sc.map((x) => x.split(';')[0]).join('; ');
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t; } return { status: r.status, body: j };
};
out.register = (await api('POST', '/api/auth/register', { username: user, password: 'password123' })).status;
out.settings = (await api('PUT', '/api/settings', { liara_token: 'placeholder-token-not-valid-0000', language: 'en' })).status;
const fd = new FormData(); fd.append('name', 'tiny'); fd.append('file', new Blob([fs.readFileSync(zip)], { type: 'application/zip' }), 'tiny.zip');
const proj = await api('POST', '/api/projects', fd); out.project = proj.status + ' ' + JSON.stringify(proj.body);

const c = await launch();
await open(c, '/login'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await c.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
// keep a handle on the page's EventSource, and time every server-sent event as it arrives
await c.send('Page.addScriptToEvaluateOnNewDocument', { source: `(function(){var O=window.EventSource;window.__es=[];window.EventSource=function(u,o){var s=new O(u,o);var add=s.addEventListener.bind(s);s.addEventListener=function(k,f,x){return add(k,function(m){window.__es.push(k+'@'+Math.round(performance.now()));return f(m)},x)};window.__src=s;window.__esUrl=u;return s};window.EventSource.prototype=O.prototype;window.EventSource.CLOSED=O.CLOSED;})()` });

async function scenario(label, { dropStream = false, blockStream = false }) {
  const r = {};
  const run = await api('POST', `/api/projects/${proj.body.id}/runs`, { mode: 'deploy' }); const rid = run.body.id; r.run = run.status;
  for (let i = 0; i < 40; i++) { const d = await api('GET', `/api/runs/${rid}`); r.statusBeforeUi = d.body.status; if (d.body.status === 'waiting') break; await sleep(300); }
  r.serverEventsBefore = (await api('GET', `/api/runs/${rid}/events?after=0`)).body.events.map((e) => e.id + ':' + e.kind).join(' ');
  if (blockStream) { await c.send('Fetch.enable', { patterns: [{ urlPattern: '*/stream*', requestStage: 'Request' }] }); }   // the stream request is never answered
  await c.send('Page.navigate', { url: `${srv.url}/runs/${rid}` });
  r.appeared = await waitFor(c, `!!document.querySelector('.appr .btn-p')`, 20000);
  if (!r.appeared) { r.pageText = (await c.ev(`document.body.innerText.slice(0,300)`)); out[label] = r; console.log(JSON.stringify(out, null, 1)); await shot(c, `${OUT}/s3-real-debug.png`); c.close(); srv.stop(); liara.stop(); process.exit(1); }
  r.situationBefore = await c.ev(`document.querySelector('.run').dataset.situation`);
  r.applyVisible = (await c.ev(APPLY))?.visible;
  r.countdown = await c.ev(`(document.querySelector('.ap-exp')||{}).textContent`);
  if (dropStream) await c.ev('window.__src.close()');                   // the stream dies silently: no error event reaches the page
  await sleep(300);
  const tClick = await c.ev(`(function(){var t=performance.now();document.querySelector('.appr .ap-acts .btn:not(.btn-p)').click();return t})()`);
  let t1 = null;
  for (let i = 0; i < 240; i++) { const x = (await c.ev(`document.querySelector('.run').dataset.situation+'|'+performance.now()`)).split('|'); if (x[0] === 'cancelled') { t1 = +x[1]; break; } await sleep(20); }
  r.declinedShownAfter_ms = t1 === null ? 'NEVER (waited 5 s)' : Math.round(t1 - tClick);
  await sleep(300);
  r.h1 = await c.ev(`document.querySelector('.h1').textContent`);
  r.approvalGone = await c.ev(`!document.querySelector('.appr')`);
  r.pipeline = await c.ev(`[...document.querySelectorAll('.pipe .pn')].map(n=>n.dataset.state).join(',')`);
  r.sse = await c.ev('window.__es.join(" ")');
  r.serverStatus = (await api('GET', `/api/runs/${rid}`)).body.status;
  if (blockStream) await c.send('Fetch.disable');
  out[label] = r;
  return rid;
}
out.fixture = { project: proj.status };
await scenario('A. normal: stream working', {});
await shot(c, `${OUT}/s3-real-declined.png`);
const rid2 = await scenario('B. the stream dies silently before the click', { dropStream: true });
await scenario('C. the stream never delivers anything (blocked)', { blockStream: true });
await c.send('Page.navigate', { url: `${srv.url}/runs/${rid2}` });
await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='cancelled'`, 8000);
out.reloadShowsDeclined = await c.ev(`document.querySelector('.run').dataset.situation`);
out.consoleErrors = errors(c);
out.fakeLiaraCalls = [...new Set(liara.log)]; out.writesToLiara = liara.log.filter((l) => !l.startsWith('GET'));
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop(); liara.stop();
