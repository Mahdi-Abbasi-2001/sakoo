import { launch, sleep, open, type, click, shot, errors, MEASURE } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer();
const c = await launch();
const out = {}; const OUT = process.env.SHOTS || '/tmp';
const user = 'tester' + Date.now().toString(36);
for (const [name, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) {
  await open(c, '/login', { w, h }); out[`login-${name}`] = await c.ev(MEASURE); await shot(c, `${OUT}/s1-login-${name}.png`);
}
// wrong password shows a localized error, not a crash
await open(c, '/login'); await type(c, '#u', 'nobody_here'); await type(c, '#p', 'wrongpass1'); await click(c, 'button[type=submit]'); await sleep(900);
out.badLogin = await c.ev(`(document.querySelector('[role=alert]')||{}).textContent`);
// register -> lands on settings
await open(c, '/register'); await type(c, '#u', user); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
out.afterRegister = await c.ev('location.pathname');
for (const [name, w, h] of [['desktop', 1440, 900], ['tablet', 768, 1024], ['phone', 390, 844]]) {
  await open(c, '/settings', { w, h }); out[`settings-${name}`] = await c.ev(MEASURE); await shot(c, `${OUT}/s1-settings-${name}.png`);
}
// connect flow with a fake token: the real backend answers; we only check the UI states
await open(c, '/settings'); await type(c, '#tok', 'fake-token-for-testing-0000'); await click(c, '#chk-h ~ div ~ .row button, section[aria-labelledby=chk-h] .row .btn-p'); await sleep(2500);
out.checkState = await c.ev(`({alert:(document.querySelector('section[aria-labelledby=chk-h] [role=alert]')||{}).textContent||null,badge:(document.querySelector('section[aria-labelledby=chk-h] .badge')||{}).textContent||null,tokenInUrl:location.href.includes('fake-token')})`);
await shot(c, `${OUT}/s1-check.png`);
// persian: switch with the toggle (it also saves the choice to the account), then dir=rtl and the same checks
await open(c, '/settings'); await click(c, 'button[lang=fa]'); await sleep(800);
for (const [name, w, h] of [['desktop', 1440, 900], ['phone', 390, 844]]) {
  await open(c, '/settings', { w, h }); out[`settings-fa-${name}`] = await c.ev(MEASURE); await shot(c, `${OUT}/s1-settings-fa-${name}.png`);
}
await c.ev("fetch('/api/auth/logout',{method:'POST',headers:{'X-Requested-With':'XMLHttpRequest'}})"); await c.ev("localStorage.removeItem('signed_in')"); await sleep(400); await open(c, '/login'); out['login-fa'] = await c.ev(MEASURE); await shot(c, `${OUT}/s1-login-fa.png`);
out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop();
