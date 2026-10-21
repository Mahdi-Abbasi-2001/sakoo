// Keyboard operation: every page's tab order with the focus indicator checked on screen pixels, then the controls that matter operated with the keyboard alone.
import fs from 'node:fs';
import { launch, sleep, open, type, click, errors, waitFor, mockApi } from './helpers.mjs';
import { startServer } from './server.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';
import { press, tabThrough, FOCUS, INTERACTIVE, brief } from './lib/keys.mjs';
const lang = process.argv[2] || 'en', rm = process.argv[3] === 'rm';
const srv = await startServer(); const c = await launch();
const report = { lang, pages: {}, operate: {}, problems: [] };
const problem = (m) => { report.problems.push(m); };

async function audit(label, path, ready, o = {}) {
  await open(c, path, { w: o.w ?? 1440, h: o.h ?? 900, rm }); if (ready) await waitFor(c, ready, 20000); await sleep(900);
  const stops = await tabThrough(c, { max: o.max ?? 70 });
  const all = await c.ev(INTERACTIVE);
  const visited = new Set(stops.map((s) => s.tag + (s.id ? '#' + s.id : '') + ':' + s.name.slice(0, 24)));
  const idOf = (x) => (x.match(/^[a-z]+#[\w-]+/) || [''])[0];
  const visitedIds = new Set(stops.map((s) => s.tag + (s.id ? '#' + s.id : '')));
  const unreachable = all.filter((a) => !visited.has(a) && !(idOf(a) && visitedIds.has(idOf(a))) && !/^button:(Apply|Decline|Accept|Reject)$/.test(a) || false).filter((a) => !/^button:(Apply|Decline|اعمال|رد کردن)$/.test(a));
  const weak = stops.filter((s) => s.ring && s.ring.sides < 3 && !s.visibleSkip);
  const off = stops.filter((s) => !s.inView), covered = stops.filter((s) => s.covered && !(s.tag === 'input' && s.rect[2] <= 2));
  const skip = stops[0] && /skip/.test(stops[0].cls) ? stops[0] : null;
  report.pages[label] = { stops: stops.length, order: stops.map(brief), weakRing: weak.map((s) => ({ el: brief(s), ring: s.ring, outline: s.outline, shadow: s.shadow })), offscreen: off.map(brief), covered: covered.map(brief), unreachable, skipFirst: !!skip, skipVisible: skip ? skip.visibleSkip : null };
  for (const s of weak) problem(`${label}: focus ring weak on ${brief(s)} sides=${s.ring.sides} ${JSON.stringify(s.ring.per)}`);
  for (const s of off) problem(`${label}: focused element off screen ${brief(s)}`);
  for (const s of covered) problem(`${label}: focused element covered ${brief(s)}`);
  for (const u of unreachable) problem(`${label}: not reachable by Tab: ${u}`);
  return stops;
}
const focusTo = async (sel, max = 60) => { for (let i = 0; i < max; i++) { await press(c, 'Tab'); if (await c.ev(`!!document.activeElement&&document.activeElement.matches(${JSON.stringify(sel)})`)) return true; } return false; };
const sit = () => c.ev(`document.querySelector('.run')&&document.querySelector('.run').dataset.situation`);

// ---- visitor pages
await open(c, '/login', { rm }); await c.ev(`localStorage.setItem('lang','${lang}')`);
await audit('landing', '/', `!!document.querySelector('.lp')`, { max: 90 });
await audit('login', '/login', `!!document.querySelector('form')`);
await audit('register', '/register', `!!document.querySelector('form')`);
// language toggle and theme toggle, by keyboard, on the login page
await open(c, '/login', { rm }); await waitFor(c, `!!document.querySelector('form')`);
report.operate.langToggle = {};
const gotFa = await focusTo('button[lang=fa]');
await press(c, 'Enter'); await sleep(500);
report.operate.langToggle.toFa = { reached: gotFa, dir: await c.ev('document.documentElement.dir'), lang: await c.ev('document.documentElement.lang'), stillFocused: await c.ev(`document.activeElement.matches('button[lang=fa]')`), pressed: await c.ev(`document.activeElement.getAttribute('aria-pressed')`) };
await press(c, 'Tab', { shift: true }); await press(c, 'Space'); await sleep(500);
report.operate.langToggle.toEn = { dir: await c.ev('document.documentElement.dir'), focusOn: await c.ev(`document.activeElement.getAttribute('lang')`) };
await focusTo('.icon-btn'); const th0 = await c.ev(`document.documentElement.dataset.theme||'dark'`); await press(c, 'Space'); await sleep(300);
report.operate.themeToggle = { before: th0, after: await c.ev(`document.documentElement.dataset.theme||'dark'`), label: await c.ev(`document.activeElement.getAttribute('aria-label')`) };
await c.ev(`localStorage.removeItem('theme');document.documentElement.removeAttribute('data-theme')`);

// ---- sign up with the keyboard (Enter submits), land on settings
const user = 'kb' + Date.now().toString(36);
await open(c, '/register', { rm }); await waitFor(c, `!!document.querySelector('#u')`);
await c.ev(`document.querySelector('#u').focus()`); await c.send('Input.insertText', { text: user }); await press(c, 'Tab'); await c.send('Input.insertText', { text: 'password123' }); await press(c, 'Enter'); await sleep(1800);
report.operate.registerByKeyboard = await c.ev('location.pathname');
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({language:'${lang}'})})`); await sleep(300);
await audit('settings', '/settings', `!!document.querySelector('#tok')`);
// the token check by keyboard: Enter in the token field runs the check
await open(c, '/settings', { rm }); await waitFor(c, `!!document.querySelector('#tok')`);
await c.ev(`document.querySelector('#tok').focus()`); await c.send('Input.insertText', { text: 'not-a-real-token-0000000' }); await press(c, 'Enter'); await sleep(2500);
report.operate.tokenEnter = await c.ev(`({alert:(document.querySelector('section[aria-labelledby=chk-h] [role=alert]')||{}).textContent||null,tokenFieldCleared:document.querySelector('#tok').value===''||document.querySelector('#tok').type==='password',typeAttr:document.querySelector('#tok').type})`);
// radio groups: arrow keys move and select
await open(c, '/settings', { rm }); await waitFor(c, `!!document.querySelector('[role=radiogroup]')`);
await c.ev(`document.querySelector('[role=radiogroup] [role=radio][aria-checked=true]').focus()`);
const r0 = await c.ev(`document.activeElement.textContent`); await press(c, 'ArrowRight'); await sleep(300);
report.operate.radioArrows = { from: r0, to: await c.ev(`document.activeElement.textContent`), checked: await c.ev(`document.activeElement.getAttribute('aria-checked')`) };
await press(c, 'ArrowLeft'); await sleep(400);   // put the language back
await open(c, '/settings', { rm }); await c.ev(`localStorage.setItem('lang','${lang}')`);

// ---- signed-in pages with made-up projects
const stop = await mockApi(c, buildMock());
await audit('dashboard', '/dashboard', `document.querySelectorAll('.prow .nm').length===4`, { max: 40 });
await audit('project', '/projects/2', `!!document.querySelector('.rrow:not(.rhead)')`, { max: 50 });
stop();
await audit('new deployment', '/new', `!!document.querySelector('#zipfile')`, { max: 30 });
// new deployment tabs with arrows
await open(c, '/new', { rm }); await waitFor(c, `!!document.querySelector('[role=tablist]')`);
await c.ev(`document.querySelector('[role=tablist] [role=tab][aria-selected=true]').focus()`); await press(c, 'ArrowRight'); await sleep(300);
report.operate.newTabs = await c.ev(`({focus:document.activeElement.textContent.trim(),selected:document.activeElement.getAttribute('aria-selected'),gitFieldShown:!!document.querySelector('#giturl')})`);

// ---- run pages (recorded runs replayed): audit each situation, then operate
const demo = async (label, path, want) => audit(label, path, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='${want}'`, { max: 80 });
await demo('run: plan approval', '/demo/waiting?at=10', 'waiting');
await demo('run: question', '/demo/waiting?at=5', 'question');
await demo('run: secret', '/demo/failed?at=12', 'secret');
await demo('run: fix approval', '/demo/failed?at=23', 'fix');
await demo('run: live', '/demo/failed?at=30', 'live');
await demo('run: preview', '/demo/dryrun?at=25', 'preview');
await audit('run: plan approval (phone)', '/demo/waiting?at=10', `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='waiting'`, { w: 390, h: 844, max: 80 });

// plan approval: Apply with Space, then focus is not lost
await open(c, '/demo/waiting?at=10', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='waiting'`); await sleep(500);
report.operate.applyReached = await focusTo('.ap-acts .btn-p');
report.operate.applyDescribedBy = await c.ev(`(function(){var b=document.activeElement;var d=b.getAttribute('aria-describedby');return {id:d,text:d&&document.getElementById(d)?document.getElementById(d).textContent.slice(0,60):null}})()`);
await press(c, 'Space'); await sleep(900);
report.operate.afterApply = { situation: await sit(), focus: await c.ev(`(function(){var e=document.activeElement;return e?e.tagName+'.'+e.className.toString().slice(0,12)+':'+(e.textContent||'').slice(0,30):null})()`) };
// decline with Enter
await open(c, '/demo/waiting?at=10', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='waiting'`); await sleep(500);
await focusTo('.ap-acts .btn-p'); await press(c, 'Tab'); const onDecline = await c.ev(`document.activeElement.textContent`);
await press(c, 'Enter'); await sleep(1000);
report.operate.decline = { focused: onDecline, situation: await sit(), focus: await c.ev(`document.activeElement.tagName`), bannerIsError: await c.ev(`!!document.querySelector('.run .fail, .run .err')`) };
// question options: Enter on an option answers
await open(c, '/demo/waiting?at=5', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='question'`); await sleep(400);
report.operate.questionReached = await focusTo('.opts .opt'); await press(c, 'Enter'); await sleep(900);
report.operate.questionAnswered = await sit();
// secret: type with the keyboard, Enter sends, field is cleared, value never in the page
await open(c, '/demo/failed?at=12', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='secret'`); await sleep(400);
report.operate.secretReached = await focusTo('#sv');
report.operate.secretType = await c.ev(`document.activeElement.type`);
await c.send('Input.insertText', { text: 'hunter2-SECRET' }); report.operate.secretMasked = await c.ev(`document.activeElement.type==='password'&&document.activeElement.value.length`);
await press(c, 'Enter'); await sleep(900);
report.operate.secretSent = { situation: await sit(), valueInDom: await c.ev(`document.documentElement.innerHTML.includes('hunter2-SECRET')`), storage: await c.ev(`JSON.stringify(localStorage)+JSON.stringify(sessionStorage)+location.href`).then((s) => s.includes('hunter2-SECRET')) };
// fix approval: Apply with Enter
await open(c, '/demo/failed?at=23', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='fix'`); await sleep(400);
await focusTo('.ap-acts .btn-p'); await press(c, 'Enter'); await sleep(1000);
report.operate.fixApply = { situation: await sit() };
// log tabs: arrows
await open(c, '/demo/failed?at=30', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='live'`);
await c.ev(`document.querySelector('[role=tab][aria-selected=true]').focus()`); await press(c, 'ArrowRight'); await sleep(300);
report.operate.detailTabs = await c.ev(`({focus:document.activeElement.id,selected:document.activeElement.getAttribute('aria-selected'),panel:document.querySelector('[role=tabpanel]').getAttribute('aria-labelledby')})`);
// cleanup dialog: Escape closes
await open(c, '/demo/failed?at=30', { rm }); await waitFor(c, `document.querySelector('.run').dataset.situation==='live'`);
await c.ev(`[...document.querySelectorAll('.run .btn-d')].find(b=>b.textContent)&&[...document.querySelectorAll('.run .btn-d')][0].click()`); await sleep(500);
report.operate.cleanupOpened = await c.ev(`!!document.querySelector('[role=dialog]')`);
report.operate.cleanupFocus = await c.ev(`(function(){var e=document.activeElement;return e?e.tagName+'#'+e.id:null})()`);
await press(c, 'Escape'); await sleep(400);
report.operate.cleanupEscape = { closed: await c.ev(`!document.querySelector('[role=dialog]')`) };

report.consoleErrors = errors(c).length;
fs.writeFileSync(`/tmp/keyboard-${lang}${rm ? '-rm' : ''}.json`, JSON.stringify(report, null, 1));
const brief2 = { ...report, pages: Object.fromEntries(Object.entries(report.pages).map(([k, v]) => [k, { stops: v.stops, weakRing: v.weakRing.length, offscreen: v.offscreen.length, covered: v.covered.length, unreachable: v.unreachable, skipFirst: v.skipFirst, skipVisible: v.skipVisible }])) };
console.log(JSON.stringify(brief2, null, 1));
c.close(); srv.stop();
process.exit(0);
