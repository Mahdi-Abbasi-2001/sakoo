// Screen-reader pass of the approval flow. No screen reader runs here, so this records what one is given: the text of the two live regions as they change (with
// the time), and the browser's accessibility tree under the approval card, the secret field and the language toggle (role, accessible name, state).
// usage: node e2e/step6-sr.mjs <lang>
import fs from 'node:fs';
import { launch, sleep, open, waitFor } from './helpers.mjs';
import { startServer } from './server.mjs';
import { press } from './lib/keys.mjs';
const lang = process.argv[2] || 'en';
const srv = await startServer(); const c = await launch();
const OBS = `(function(){window.__ann=[];var last={};var go=function(){new MutationObserver(function(ms){ms.forEach(function(m){var n=m.target.nodeType===3?m.target.parentElement:m.target;var r=n&&n.closest&&n.closest('[data-live]');if(!r)return;var t=r.textContent.trim();var k=r.dataset.live;if(last[k]===t)return;last[k]=t;if(t)window.__ann.push({t:Math.round(performance.now()),region:k,mode:r.getAttribute('aria-live'),role:r.getAttribute('role'),text:t})})}).observe(document.documentElement,{subtree:true,childList:true,characterData:true})};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',go);else go()})()`;
await c.send('Page.addScriptToEvaluateOnNewDocument', { source: OBS });
await open(c, '/login'); await c.ev(`localStorage.setItem('lang','${lang}')`);
const ax = async (selector) => {
  await c.send('DOM.enable'); await c.send('Accessibility.enable');
  const doc = await c.send('DOM.getDocument', { depth: 0 }); const q = await c.send('DOM.querySelector', { nodeId: doc.result.root.nodeId, selector });
  if (!q.result.nodeId) return ['(not found: ' + selector + ')'];
  const d = await c.send('DOM.describeNode', { nodeId: q.result.nodeId }); const bid = d.result.node.backendNodeId;
  const tree = await c.send('Accessibility.getFullAXTree'); const nodes = new Map(tree.result.nodes.map((n) => [n.nodeId, n]));
  const root = tree.result.nodes.find((n) => n.backendDOMNodeId === bid); const lines = [];
  const walk = (n, depth) => {
    if (!n) return;
    const role = n.role?.value, name = n.name?.value, ign = n.ignored;
    const props = (n.properties || []).filter((p) => ['focusable', 'checked', 'disabled', 'expanded', 'pressed', 'selected', 'required', 'invalid', 'level', 'live', 'atomic', 'relevant'].includes(p.name) && p.value?.value !== false).map((p) => `${p.name}=${p.value?.value}`);
    const desc = n.description?.value;
    if (!ign && role && role !== 'none' && role !== 'generic' && (name || ['button', 'textbox', 'heading', 'region', 'status', 'alert', 'list', 'group', 'radio', 'tab', 'link'].includes(role))) lines.push(`${'  '.repeat(depth)}${role}${name ? ' “' + String(name).replace(/\s+/g, ' ').slice(0, 110) + '”' : ''}${props.length ? ' (' + props.join(', ') + ')' : ''}${desc ? ' — described: “' + String(desc).slice(0, 80) + '”' : ''}`);
    for (const id of n.childIds || []) walk(nodes.get(id), depth + (ign || !role || role === 'generic' || role === 'none' ? 0 : 1));
  };
  walk(root, 0); return lines;
};
const sit = () => c.ev(`document.querySelector('.run')&&document.querySelector('.run').dataset.situation`);
const waitSit = async (s, ms = 25000) => waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='${s}'`, ms);
const focusTo = async (sel, max = 80) => { for (let i = 0; i < max; i++) { await press(c, 'Tab'); if (await c.ev(`!!document.activeElement&&document.activeElement.matches(${JSON.stringify(sel)})`)) return true; } return false; };
const out = { lang, flows: {} };
const flow = async (name, path, steps) => {
  await open(c, path, { w: 1440, h: 900 }); await sleep(600);
  const rec = { announcements: [], tree: {}, notes: [] };
  const t0 = await c.ev('performance.now()');
  await steps(rec);
  const ann = await c.ev('window.__ann'); rec.announcements = ann.map((a) => `+${((a.t - t0) / 1000).toFixed(1)}s ${a.region === 'urgent' ? 'ASSERTIVE (role=alert)' : 'polite (role=status)'}: ${a.text}`);
  rec.title = await c.ev('document.title'); out.flows[name] = rec;
};
// 1. plan approval: opens, is answered with Apply
await flow('plan approval then Apply', '/demo/waiting', async (rec) => {
  await waitSit('question'); rec.notes.push('situation question'); await focusTo('.opts .opt'); rec.tree.question = await ax('.appr'); await press(c, 'Enter');
  await waitSit('waiting'); await sleep(500); rec.titleWhenOpen = await c.ev('document.title');
  rec.tree.planApproval = await ax('.appr'); await focusTo('.ap-acts .btn-p'); rec.focusOnApply = await c.ev(`document.activeElement.textContent.trim()+' | describedby: '+document.activeElement.getAttribute('aria-describedby')`);
  await press(c, 'Enter'); await sleep(1500); rec.after = { situation: await sit(), focusedElement: await c.ev(`document.activeElement.tagName+(document.activeElement.className?'.'+String(document.activeElement.className).split(' ')[0]:'')+' “'+(document.activeElement.textContent||'').trim().slice(0,40)+'”'`) };
});
// 2. plan approval answered with Decline
await flow('plan approval then Decline', '/demo/waiting?at=10', async (rec) => {
  await waitSit('waiting'); await sleep(400); await focusTo('.ap-acts .btn-p'); await press(c, 'Tab'); rec.focusOnDecline = await c.ev(`document.activeElement.textContent.trim()`); await press(c, 'Enter'); await sleep(1500);
  rec.after = { situation: await sit(), focusedElement: await c.ev(`document.activeElement.tagName+' “'+(document.activeElement.textContent||'').trim().slice(0,40)+'”'`) };
});
// 3. failed run: secret, fix approval, Apply, live
await flow('secret, fix approval, live', '/demo/failed?at=12', async (rec) => {
  await waitSit('secret'); await sleep(400); rec.tree.secret = await ax('.appr'); const gotSecret = await focusTo('#sv'); if (!gotSecret) await c.ev(`document.querySelector('#sv')&&document.querySelector('#sv').focus()`); rec.focusOnSecret = await c.ev(`'type='+document.activeElement.type+' name="'+((document.activeElement.labels||[])[0]||{}).textContent+'" describedby='+document.activeElement.getAttribute('aria-describedby')`);
  await c.send('Input.insertText', { text: 'a-secret-value' }); await press(c, 'Enter'); await sleep(1200);
  await waitSit('fix'); await sleep(500); rec.tree.fixApproval = await ax('.appr'); await focusTo('.ap-acts .btn-p'); await press(c, 'Enter'); await waitSit('live', 30000); await sleep(800);
  rec.tree.liveResult = await ax('.result.live'); rec.after = { situation: await sit(), focused: await c.ev(`document.activeElement.tagName`) };
});
// 4. failed run where the fix is declined
await flow('fix declined, run fails', '/demo/failed?at=23', async (rec) => { await waitSit('fix'); await sleep(400); await focusTo('.ap-acts .btn-p'); await press(c, 'Tab'); await press(c, 'Enter'); await sleep(1800); rec.after = { situation: await sit() }; });
// 5. preview finishes
await flow('preview finishes', '/demo/dryrun', async (rec) => { await waitSit('question'); await focusTo('.opts .opt'); await press(c, 'Enter'); await waitSit('preview', 40000); await sleep(800); rec.after = { situation: await sit() }; });
// 6. the language toggle and the page landmarks
await open(c, '/login'); await sleep(800);
out.langToggle = await ax('.seg'); out.themeToggle = await ax('.icon-btn');
out.landmarks = await c.ev(`[...document.querySelectorAll('header,nav,main,footer,[role=region],[role=status],[role=alert]')].map(e=>e.tagName.toLowerCase()+(e.getAttribute('role')?'[role='+e.getAttribute('role')+']':'')+(e.getAttribute('aria-label')?' “'+e.getAttribute('aria-label')+'”':''))`);
fs.writeFileSync(`/tmp/sr-${lang}.json`, JSON.stringify(out, null, 1));
for (const [k, v] of Object.entries(out.flows)) { console.log('\n== ' + k + '  (title: ' + v.title + ')'); v.announcements.forEach((a) => console.log('  ' + a)); if (v.after) console.log('  after:', JSON.stringify(v.after)); for (const n of ['focusOnApply', 'focusOnDecline', 'focusOnSecret', 'titleWhenOpen']) if (v[n]) console.log('  ' + n + ':', v[n]); }
console.log('\nlang toggle tree:', JSON.stringify(out.langToggle), '\nlandmarks:', JSON.stringify(out.landmarks));
c.close(); srv.stop();
process.exit(0);
