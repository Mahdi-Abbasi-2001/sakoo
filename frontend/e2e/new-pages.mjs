// The pages added after the first design (ready-made apps, GitHub/alerts settings, WordPress, CI, uptime, metrics, auto-deploy, beginner mode),
// in English and Persian at two widths, against the real API with a stand-in Liara: layout facts, console errors and screenshots.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { launch, sleep, open, type, click, shot, errors, waitFor, MEASURE } from './helpers.mjs';
import { startServer } from './server.mjs';
import { startFakeLiara } from './fake-liara.mjs';
const liara = await startFakeLiara({ writes: true });
const srv = await startServer({ env: liara.env }); const OUT = process.env.SHOTS || '/tmp/newpages'; fs.mkdirSync(OUT, { recursive: true });
const zip = path.join(os.tmpdir(), 'tiny-compose.zip'); execFileSync('python3', [path.resolve(import.meta.dirname, 'fixtures/make-zip.py'), zip]);
const wpZip = path.resolve(import.meta.dirname, '../../docs/samples/wp-test.zip');
const user = 'np' + Date.now().toString(36); const H = { 'X-Requested-With': 'XMLHttpRequest' }; let cookie = '';
const api = async (method, p, body) => {
  const r = await fetch(srv.url + p, { method, headers: { ...H, ...(body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), cookie }, body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined });
  const sc = r.headers.getSetCookie?.(); if (sc?.length) cookie = sc.map((x) => x.split(';')[0]).join('; ');
  const t = await r.text(); let j; try { j = JSON.parse(t); } catch { j = t; } return { status: r.status, body: j };
};
await api('POST', '/api/auth/register', { username: user, password: 'password123' });
await api('PUT', '/api/settings', { liara_token: 'placeholder-token-not-valid-0000', language: 'en' });
const up = async (name, file) => { const fd = new FormData(); fd.append('name', name); fd.append('file', new Blob([fs.readFileSync(file)], { type: 'application/zip' }), name + '.zip'); return (await api('POST', '/api/projects', fd)).body.id; };
const tiny = await up('tiny', zip), wp = await up('wptest', wpZip);
const c = await launch();
await open(c, '/login'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
const run = await api('POST', `/api/projects/${tiny}/runs`, { mode: 'deploy' }); const rid = run.body.id;
await c.send('Page.navigate', { url: `${srv.url}/runs/${rid}` });
await waitFor(c, `!!document.querySelector('.appr .btn-p')`, 20000); await click(c, '.appr .ap-acts .btn-p');
const live = await waitFor(c, `document.querySelector('.run').dataset.situation==='live'`, 90000);
console.log(JSON.stringify({ reachedLive: live, tiny, wp, rid }));
const pages = [['apps', '/apps'], ['new-git', '/new', `document.querySelector('.card [role=tablist]').querySelectorAll('button')[1].click()`], ['settings', '/settings'], ['project-live', `/projects/${tiny}`], ['project-wp', `/projects/${wp}`], ['run-live', `/runs/${rid}`]];
const rows = [];
for (const lang of ['en', 'fa']) for (const [w, h] of [[1440, 900], [390, 844]]) for (const [name, p, act] of pages) {
  if (name === 'apps' && w === 1440) await api('PUT', '/api/settings', { language: lang });
  await open(c, p, { w, h, lang }); if (act) { await c.ev(act); } await sleep(1800);
  const m = await c.ev(MEASURE); const errs = errors(c);
  const seen = await c.ev(`JSON.stringify({ls: localStorage.getItem('lang'), html: document.documentElement.lang})`); rows.push({ lang, w, page: name, seen, dir: m.dir, sideways: m.overflow[0] > m.overflow[1], under44: m.under44.slice(0, 6), errs: errs.length, firstErr: errs[0]?.slice(0, 140) });
  await shot(c, `${OUT}/${name}-${lang}-${w}.png`);
}
for (const r of rows) console.log(JSON.stringify(r));
c.close(); srv.stop(); liara.stop?.(); process.exit(0);
