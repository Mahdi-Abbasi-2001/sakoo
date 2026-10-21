// Step 3 against the REAL backend (and the stand-in Liara of fake-liara.mjs): the whole path a person takes through the dashboard,
// the new-deployment form and the project page.
import fs from 'node:fs'; import os from 'node:os'; import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { launch, sleep, open, type, click, shot, errors, MEASURE, waitFor, setFiles } from './helpers.mjs';
import { startServer } from './server.mjs';
import { startFakeLiara } from './fake-liara.mjs';
const liara = await startFakeLiara(); const srv = await startServer({ env: liara.env }); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const zip = path.join(os.tmpdir(), 'tiny-compose.zip'); execFileSync('python3', [path.resolve(import.meta.dirname, 'fixtures/make-zip.py'), zip]);
const bad = path.join(os.tmpdir(), 'broken.zip'); fs.writeFileSync(bad, 'this is not a zip file at all');
const txt = (sel) => c.ev(`(document.querySelector(${JSON.stringify(sel)})||{}).textContent||null`);
const sit = () => c.ev(`(document.querySelector('.run')||{dataset:{}}).dataset.situation`);
const measure = async (label, w, h, lang) => { await open(c, label.path, { w, h }); if (lang) { await c.ev(`localStorage.setItem('lang','${lang}')`); await c.send('Page.reload'); } await sleep(1300); const m = await c.ev(MEASURE); out[label.name] = { overflow: m.overflow, under44: m.under44, dir: m.dir }; await shot(c, `${OUT}/s3-${label.name}.png`); };

// an account with no projects and no Liara token yet: the first-visit steps
const user = 'dash' + Date.now().toString(36);
await open(c, '/register'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await open(c, '/dashboard'); await waitFor(c, `!!document.querySelector('.step-card')`);
out.firstVisitNotConnected = { steps: await c.ev(`document.querySelectorAll('.step-card').length`), active: await c.ev(`[...document.querySelectorAll('.step-card.active h2')].map(h=>h.textContent).join()`), newDisabled: await c.ev(`!![...document.querySelectorAll('.step-card button')].find(b=>b.disabled)`) };
await shot(c, `${OUT}/s3-first-visit-notconnected.png`);
// connect (a placeholder token is saved; the stand-in Liara answers the reads)
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({liara_token:'placeholder-token-not-valid-0000',language:'en'})})`); await sleep(500);
await open(c, '/dashboard'); await waitFor(c, `!!document.querySelector('.step-card')`); await sleep(700);
out.firstVisitConnected = { active: await c.ev(`[...document.querySelectorAll('.step-card.active h2')].map(h=>h.textContent).join()`), newLink: await c.ev(`!!document.querySelector('.step-card a[href="/new"]')` ) };
for (const [n, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) await measure({ name: `first-visit-${n}`, path: '/dashboard' }, w, h);

// the new-deployment form, with its errors
await open(c, '/new'); await waitFor(c, `!!document.querySelector('#zipfile')`);
await setFiles(c, '#zipfile', [bad]); await sleep(300); await click(c, 'button[type=submit]'); await sleep(1500);
out.errBrokenZip = await txt('#zip-e');
await click(c, '[role=tablist] button:nth-child(2)'); await type(c, '#giturl', 'https://gitlab.example.org/acme/blog'); await sleep(200); await click(c, 'button[type=submit]'); await sleep(1500);
out.errGitHost = await txt('#git-e');
await c.ev(`(function(){var e=document.querySelector('#giturl');e.focus();e.select()})()`); await c.send('Input.insertText', { text: 'https://me:ghp_secret@github.com/acme/blog' }); await click(c, 'button[type=submit]'); await sleep(1500);
out.errGitCredentials = await txt('#git-e');
out.tokenNotEchoed = !(await c.ev(`document.body.innerText.includes('ghp_secret')`)) || 'the credentials text appears in the page: check';
await shot(c, `${OUT}/s3-new-errors.png`);
await click(c, '[role=tablist] button:nth-child(1)'); await sleep(200);
await setFiles(c, '#zipfile', [zip]); await sleep(300);
out.nameSuggested = await c.ev(`document.querySelector('#pname').value`);
for (const [n, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) { await open(c, '/new', { w, h }); await sleep(900); const m = await c.ev(MEASURE); out[`new-${n}`] = { overflow: m.overflow, under44: m.under44 }; await shot(c, `${OUT}/s3-new-${n}.png`); }
// create for real: upload, project row, run, and the page lands on the approval
await open(c, '/new'); await waitFor(c, `!!document.querySelector('#zipfile')`); await setFiles(c, '#zipfile', [zip]); await sleep(300); await click(c, 'button[type=submit]');
out.reachedRun = await waitFor(c, `location.pathname.startsWith('/runs/')&&!!document.querySelector('.appr .btn-p')`, 25000);
out.approvalTitle = await txt('.appr .ap-title');
await shot(c, `${OUT}/s3-approval-plural.png`);
await c.ev(`document.querySelector('.appr .ap-acts .btn:not(.btn-p)').click()`); await waitFor(c, `document.querySelector('.run').dataset.situation==='cancelled'`, 6000); out.declined = await sit();

// the dashboard now has one project whose last run was declined
await open(c, '/dashboard'); await waitFor(c, `!!document.querySelector('.prow .nm')`); await sleep(600);
out.dashboardRow = { name: await txt('.prow .nm'), badge: await txt('.prow .badge'), cost: await txt('.prow .cost'), actions: await c.ev(`[...document.querySelectorAll('.prow .acts-cell .btn')].map(b=>b.textContent.trim()).join(' | ')`), summary: await c.ev(`[...document.querySelectorAll('.sumcards .card')].map(x=>x.textContent.replace(/\\s+/g,' ').trim()).join(' || ')`) };
for (const [n, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) await measure({ name: `dashboard-${n}`, path: '/dashboard' }, w, h);
// project page: history, start a preview run, which finishes without creating anything
await click(c, '.prow .nm'); await waitFor(c, `!!document.querySelector('.proj-h')`); await sleep(500);
out.projectHeader = { title: await txt('.proj-h .title'), history: await c.ev(`[...document.querySelectorAll('.rrow:not(.rhead)')].map(r=>r.textContent.replace(/\\s+/g,' ').trim()).join(' || ')`) };
for (const [n, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) { const u = await c.ev('location.pathname'); await measure({ name: `project-${n}`, path: u }, w, h); }
await click(c, '.proj-h .link'); out.previewRun = await waitFor(c, `location.pathname.startsWith('/runs/')&&document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='preview'`, 25000);
await c.ev(`history.back()`); await waitFor(c, `!!document.querySelector('.proj-h')`); await sleep(1200);
out.historyAfterPreview = await c.ev(`[...document.querySelectorAll('.rrow:not(.rhead)')].map(r=>r.textContent.replace(/\\s+/g,' ').trim().slice(0,110)).join(' || ')`);
// persian
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({language:'fa'})})`); await sleep(400);
for (const [name, p] of [['dashboard-fa', '/dashboard'], ['new-fa', '/new'], ['project-fa', await c.ev('location.pathname')]]) for (const [n, w, h] of [['desktop', 1440, 900], ['phone', 390, 844]]) { await open(c, p, { w, h }); await sleep(1200); const m = await c.ev(MEASURE); out[`${name}-${n}`] = { overflow: m.overflow, under44: m.under44, dir: m.dir }; await shot(c, `${OUT}/s3-${name}-${n}.png`); }
// remove the project from the project page
await open(c, await c.ev('location.pathname')); await waitFor(c, `!!document.querySelector('.danger')`); await click(c, '.danger .btn-d'); await sleep(200); await click(c, '.danger .row .btn-d'); await sleep(1200);
out.afterRemove = await c.ev('location.pathname');
out.liaraWrites = liara.log.filter((l) => !l.startsWith('GET')); out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop(); liara.stop();
