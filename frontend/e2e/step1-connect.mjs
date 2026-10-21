// The team selector path needs a Liara account with teams, which a test cannot have. So /api/liara/check is answered here (the browser's
// network layer is intercepted); every other call goes to the real backend. This checks the UI states, not Liara.
import { launch, sleep, open, type, click, shot, errors } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer();
const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const user = 'conn' + Date.now().toString(36);
await open(c, '/register'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await c.send('Fetch.enable', { patterns: [{ urlPattern: '*/api/liara/check', requestStage: 'Request' }] });
const seen = [];
c.onEvent = async (e) => {
  if (e.method !== 'Fetch.requestPaused') return;
  const body = JSON.parse(e.params.request.postData || '{}'); seen.push({ team_id: body.team_id, hasToken: !!body.token });
  const res = body.team_id ? { valid: true, teams: [{ id: 't2', name: 'Side projects' }], team_required: false, networks: ['net1'], apps: ['shop', 'docs'] }
    : { valid: true, teams: [{ id: 't1', name: 'Acme Studio' }, { id: 't2', name: 'Side projects' }], team_required: true, networks: [], apps: [] };
  await c.send('Fetch.fulfillRequest', { requestId: e.params.requestId, responseCode: 200, responseHeaders: [{ name: 'Content-Type', value: 'application/json' }], body: Buffer.from(JSON.stringify(res)).toString('base64') });
};
// cdp.mjs keeps unmatched events in c.events; poll it
const pump = setInterval(() => { while (c.events.length) { const e = c.events.shift(); if (c.onEvent) c.onEvent(e); } }, 30);
const TOKEN = 'secret-token-value-123456';
await open(c, '/settings'); await type(c, '#tok', TOKEN);
await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(900);
out.teamStep = await c.ev(`({badge:(document.querySelector('section[aria-labelledby=chk-h] .badge')||{}).textContent,radios:document.querySelectorAll('section[aria-labelledby=chk-h] [role=radio]').length,checkDisabled:document.querySelector('section[aria-labelledby=chk-h] .row .btn-p').disabled,saveShown:/Save and connect/.test(document.body.textContent)})`);
await shot(c, `${OUT}/s1-team.png`);
await click(c, 'section[aria-labelledby=chk-h] [role=radio]:nth-child(2)'); await sleep(200);
out.afterPick = await c.ev(`({checkDisabled:document.querySelector('section[aria-labelledby=chk-h] .row .btn-p').disabled})`);
await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(900);
out.readyStep = await c.ev(`({badge:(document.querySelector('section[aria-labelledby=chk-h] .badge')||{}).textContent,seesApps:/shop/.test(document.body.textContent),saveShown:/Save and connect/.test(document.body.textContent)})`);
await shot(c, `${OUT}/s1-ready.png`);
await click(c, 'section[aria-labelledby=chk-h] .row .btn-p'); await sleep(1500);
out.connected = await c.ev(`({text:(document.querySelector('#done-h')||{}).textContent,badge:(document.querySelector('.shell .badge')||{}).textContent})`);
out.tokenLeaks = await c.ev(`({inDom:document.documentElement.outerHTML.includes(${JSON.stringify(TOKEN)}),inUrl:location.href.includes('secret-token'),inStorage:JSON.stringify(localStorage).includes('secret-token'),inInputs:[].slice.call(document.querySelectorAll('input')).some(function(i){return i.value.includes('secret-token')})})`);
await shot(c, `${OUT}/s1-connected.png`);
out.checkCalls = seen; out.consoleErrors = errors(c);
clearInterval(pump); console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop();
