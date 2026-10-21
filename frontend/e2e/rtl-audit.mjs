// Visits every page and state in Persian and collects (1) digits written the Latin way and (2) English words that a person would read, outside
// the places where Latin is correct on purpose (code, paths, URLs, identifiers, logs). The second list is the strings that "fall back to English".
// Also checks that nothing overflows sideways, and that the reading direction is right-to-left.
import fs from 'node:fs';
import { launch, sleep, open, type, click, errors, waitFor, mockApi } from './helpers.mjs';
import { startServer } from './server.mjs';
import { startFakeLiara } from './fake-liara.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';
const liara = await startFakeLiara(); const srv = await startServer({ env: liara.env }); const c = await launch();
const found = new Map(); const layout = [];
const SCAN = `(function(){
  const ok='.ltr,.mono,.code,.term,.url,.chip,.tm,.ll,.sends,.diff,[dir=ltr],input,textarea,script,style,svg text,.sr-only';
  const w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);const out=[];let n;
  const bidi=/[\\u2066-\\u2069]/g;
  while(n=w.nextNode()){const raw=n.nodeValue;const t=raw.replace(bidi,'').trim();if(!t)continue;const el=n.parentElement;if(!el||el.closest('script,style'))continue;
    const cs=getComputedStyle(el);if(cs.display==='none'||cs.visibility==='hidden')continue;
    // a Latin run is fine when the element is isolated as LTR (dir or unicode-bidi isolate with direction ltr) or is technical
    const tech=!!el.closest(ok)||(cs.direction==='ltr'&&document.documentElement.dir==='rtl'&&/isolate/.test(cs.unicodeBidi));
    const hasDigit=/[0-9]/.test(t), hasEn=/[A-Za-z]{3,}/.test(t);
    if(!(hasDigit||hasEn))continue;
    // text made only of isolated Latin runs inside a Persian sentence: strip those runs and look again
    const stripped=raw.replace(/\\u2066[^\\u2069]*\\u2069/g,' ').trim();
    const d=/[0-9]/.test(stripped), e=/[A-Za-z]{3,}/.test(stripped);
    if(tech)continue;
    out.push({t:t.slice(0,110),digit:d,en:e,cls:(el.className&&el.className.baseVal!==undefined?el.className.baseVal:el.className)||el.tagName});
  }
  const dd=document.documentElement;return {items:out,overflow:[dd.scrollWidth,dd.clientWidth],dir:dd.dir,lang:dd.lang};
})()`;
async function scan(label, w = 1440) {
  const r = await c.ev(SCAN);
  layout.push({ label, w, overflow: r.overflow, dir: r.dir, lang: r.lang, sideways: r.overflow[0] > r.overflow[1] });
  for (const it of r.items) { if (!it.digit && !it.en) continue; const k = (it.digit ? 'DIGIT ' : 'ENGLISH ') + it.t; if (!found.has(k)) found.set(k, { kind: it.digit ? 'latin digit' : 'english', text: it.t, cls: it.cls, pages: new Set() }); found.get(k).pages.add(label); }
}
const post = (b, m = 'PUT', p = '/api/settings') => c.ev(`fetch('${p}',{method:'${m}',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(${JSON.stringify(b)})}).then(r=>r.status)`);
const go = async (label, path, ready, opts = {}) => { await open(c, path, { w: opts.w ?? 1440, h: opts.h ?? 900 }); if (ready) await waitFor(c, ready, 20000); await sleep(opts.wait ?? 1200); await scan(label + (opts.w ? '@' + opts.w : ''), opts.w); };
const user = 'fa' + Date.now().toString(36);

// anonymous pages in Persian
await open(c, '/login'); await c.ev(`localStorage.setItem('lang','fa')`);
await go('login', '/login', `!!document.querySelector('form')`);
await go('register', '/register', `!!document.querySelector('form')`);
await type(c, '#u', 'ab'); await type(c, '#p', 'x'); await click(c, 'button[type=submit]'); await sleep(900); await scan('register: error');
await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await post({ language: 'fa' });
await go('settings', '/settings', `!!document.querySelector('#tok')`);
// connect flow states with the answers of /liara/check made up
await c.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/liara/check', requestStage: 'Request' }] });
let mode = 'invalid'; const pump = setInterval(() => { while (c.events.length) { const e = c.events.shift(); if (e.method !== 'Fetch.requestPaused') continue; const b = JSON.parse(e.params.request.postData || '{}');
  const res = mode === 'invalid' ? { valid: false, code: 'liara_token_invalid', params: { status: 401 } } : mode === 'forbidden' ? { valid: false, code: 'liara_forbidden', params: { status: 403 } } : mode === 'down' ? { valid: false, code: 'liara_unreachable', params: {} }
    : b.team_id ? { valid: true, teams: [{ id: 't2', name: 'Side projects' }], team_required: false, networks: ['net1'], apps: ['shop', 'docs'] } : { valid: true, teams: [{ id: 't1', name: 'Acme Studio' }, { id: 't2', name: 'Side projects' }], team_required: true, networks: [], apps: [] };
  c.send('Fetch.fulfillRequest', { requestId: e.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(res)).toString('base64') }).catch(() => {}); } }, 15);
for (const m of ['invalid', 'forbidden', 'down']) { mode = m; await open(c, '/settings'); await waitFor(c, `!!document.querySelector('#tok')`); await type(c, '#tok', 'placeholder-token-0000000'); await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(900); await scan('connect: ' + m); }
mode = 'team'; await open(c, '/settings'); await waitFor(c, `!!document.querySelector('#tok')`); await type(c, '#tok', 'placeholder-token-0000000'); await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(900); await scan('connect: choose team');
await click(c, 'section[aria-labelledby=chk-h] [role=radio]:nth-child(2)'); await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(900); await scan('connect: ready');
await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(1500); await scan('connect: connected');
clearInterval(pump); await c.send('Fetch.disable');
// dashboard: first visit, then with the made-up projects
await go('dashboard: first visit', '/', `!!document.querySelector('.step-card')`);
const stop = await mockApi(c, buildMock());
for (const w of [1440, 390]) {
  await go('dashboard', '/', `document.querySelectorAll('.prow .nm').length===4`, { w, h: w > 600 ? 900 : 844 });
  await go('project', '/projects/2', `!!document.querySelector('.rrow:not(.rhead)')`, { w, h: w > 600 ? 900 : 844, wait: 1500 });
}
stop();
await go('new deployment', '/new', `!!document.querySelector('#zipfile')`);
await click(c, 'button[type=submit]'); await sleep(500); await scan('new deployment: errors');
await click(c, '[role=tablist] button:nth-child(2)'); await type(c, '#giturl', 'https://gitlab.example.org/acme/blog'); await sleep(200); await scan('new deployment: git');
await click(c, 'button[type=submit]'); await sleep(300); await type(c, '#pname', 'x'); await click(c, 'button[type=submit]'); await sleep(1500); await scan('new deployment: git host error');
// the run page in every situation (recorded runs replayed in the page)
const demo = async (label, path, ready, tabs = true, w = 1440, h = 900) => { await open(c, path, { w, h }); await waitFor(c, ready, 25000); await sleep(900); await scan(label, w);
  if (tabs) for (const tab of ['tests', 'plan', 'files', 'costs']) { await c.ev(`document.querySelector('#tab-${tab}').click()`); await sleep(250); await scan(label + ' / ' + tab, w); } };
await demo('run: reading', '/demo/waiting?at=2', `document.querySelector('.run')`, false);
await demo('run: question', '/demo/waiting?at=5', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='question'`);
await demo('run: plan approval', '/demo/waiting?at=10', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='waiting'`);
await demo('run: plan approval phone', '/demo/waiting?at=10', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='waiting'`, false, 390, 844);
await demo('run: secret', '/demo/failed?at=12', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='secret'`, false);
await demo('run: fix approval', '/demo/failed?at=23', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='fix'`);
await demo('run: live', '/demo/failed?at=30', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='live'`);
await demo('run: preview', '/demo/dryrun?at=25', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='preview'`);
await open(c, '/demo/failed?at=23'); await waitFor(c, `document.querySelector('.run').dataset.situation==='fix'`); await click(c, '.appr .ap-acts .btn:not(.btn-p)'); await sleep(900); await scan('run: failed after declined fix');
await open(c, '/demo/waiting?at=10'); await waitFor(c, `document.querySelector('.run').dataset.situation==='waiting'`); await click(c, '.appr .ap-acts .btn:not(.btn-p)'); await sleep(900); await scan('run: declined');
await open(c, '/runs/nonexistent'); await sleep(1500); await scan('run: not found');
const report = [...found.values()].map((v) => ({ kind: v.kind, text: v.text, where: [...v.pages].slice(0, 4).join(', '), element: String(v.cls).slice(0, 40) }));
fs.writeFileSync(process.env.REPORT || '/tmp/rtl-audit.json', JSON.stringify({ layout, report }, null, 1));
console.log(JSON.stringify({ pagesChecked: layout.length, sideways: layout.filter((l) => l.sideways), notRtl: layout.filter((l) => l.dir !== 'rtl'), consoleErrors: errors(c) }, null, 1));
console.log(report.length + ' distinct items'); for (const r of report) console.log(r.kind.padEnd(11), JSON.stringify(r.text).padEnd(70), r.element.padEnd(14), r.where);
c.close(); srv.stop(); liara.stop();
