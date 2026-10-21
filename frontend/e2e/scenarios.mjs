// Walks every page and state of the app (the same list the Persian audit uses) and calls onState(label, width, c) at each one.
// Parameters: lang ('en'|'fa'), theme ('dark'|'light'), rm (prefers-reduced-motion), widths (override of the two widths used for dashboard/project/run pages).
import { sleep, open as openBase, type, click, waitFor, mockApi } from './helpers.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';

export async function walk(c, { lang = 'en', theme = 'dark', rm = false, onState, only = null, widths = [1440, 390], base = 1440 } = {}) {
  const want = (label) => !only || only.test(label);
  const scan = async (label, w = 1440) => { if (want(label)) await onState(label, w, c); };
  const H = (w) => (w > 600 ? 900 : 844);
  const open = (cc, path, o = {}) => openBase(cc, path, { w: base, h: H(o.w ?? base), rm, ...o });
  const post = (b, m = 'PUT', p = '/api/settings') => c.ev(`fetch('${p}',{method:'${m}',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify(${JSON.stringify(b)})}).then(r=>r.status)`);
  const go = async (label, path, ready, opts = {}) => { await open(c, path, { w: opts.w ?? base, h: opts.h ?? H(opts.w ?? base) }); if (ready) await waitFor(c, ready, 20000); await sleep(opts.wait ?? 1200); await scan(label + (opts.w ? '@' + opts.w : ''), opts.w ?? base); };
  const user = lang + Date.now().toString(36);
  
  // anonymous pages in Persian
  await open(c, '/login'); await c.ev(`localStorage.setItem('lang','${lang}');localStorage.setItem('theme','${theme}')`);
  for (const w of widths) await go('landing', '/', `!!document.querySelector('.lp')`, { w, h: w > 600 ? 900 : 844 });
  await go('login', '/login', `!!document.querySelector('form')`);
  await go('register', '/register', `!!document.querySelector('form')`);
  await type(c, '#u', 'ab'); await type(c, '#p', 'x'); await click(c, 'button[type=submit]'); await sleep(900); await scan('register: error');
  await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
  await post({ language: lang });
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
  await go('dashboard: first visit', '/', `!!document.querySelector('.startgrid')`);
  const stop = await mockApi(c, buildMock());
  for (const w of widths) {
    await go('dashboard', '/', `document.querySelectorAll('.prow .nm').length===4`, { w, h: w > 600 ? 900 : 844 });
    await go('project', '/projects/2', `!!document.querySelector('.rrow:not(.rhead)')`, { w, h: w > 600 ? 900 : 844, wait: 1500 });
  }
  stop();
  await go('new deployment', '/new', `!!document.querySelector('#zipfile')`);
  await click(c, 'button[type=submit]'); await sleep(500); await scan('new deployment: errors');
  await click(c, '[role=tablist] button:nth-child(2)'); await type(c, '#giturl', 'https://gitlab.example.org/acme/blog'); await sleep(200); await scan('new deployment: git');
  await click(c, 'button[type=submit]'); await sleep(300); await type(c, '#pname', 'x'); await click(c, 'button[type=submit]'); await sleep(1500); await scan('new deployment: git host error');
  // the run page in every situation (recorded runs replayed in the page)
  const demo = async (label, path, ready, tabs = true, w = base, h = H(w)) => { await open(c, path, { w, h }); await waitFor(c, ready, 25000); await sleep(900); await scan(label, w);
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
  
}
