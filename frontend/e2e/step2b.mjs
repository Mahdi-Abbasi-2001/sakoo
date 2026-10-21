// Walks the demo runs through their states, the way a reviewer would: answer, approve, decline.
import { launch, sleep, open, click, shot, errors, MEASURE, waitFor, APPLY } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer(); const c = await launch(); const OUT = process.env.SHOTS || '/tmp'; const out = {};
const txt = (sel) => c.ev(`(document.querySelector(${JSON.stringify(sel)})||{}).textContent||null`);
const state = () => c.ev(`({sit:document.querySelector('.run').dataset.situation,agent:document.querySelector('.run').dataset.agent,h1:document.querySelector('.h1').textContent})`);

// 1. build timeout, fixed: secret -> plan approval -> fix approval -> live
await open(c, '/demo/failed');
await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='waiting'`); out.planStop = await state();
await click(c, '.appr .btn-p');                                   // approve the plan
await waitFor(c, `document.querySelector('.run').dataset.situation==='secret'`); out.secretStop = await state();
out.secretInputIsPassword = await c.ev(`document.querySelector('#sv').type`);
await c.ev(`(function(){var e=document.querySelector('#sv');e.focus()})()`); await c.send('Input.insertText', { text: 'super-secret-value-42' });
out.secretTypedInDom = await c.ev(`document.querySelector('#sv').value.length`);
await click(c, '.appr form button[type=submit]'); await sleep(300);
out.secretAfterSend = await c.ev(`({leak:document.documentElement.outerHTML.includes('super-secret-value-42')||JSON.stringify(localStorage).includes('super-secret')||location.href.includes('super-secret')})`);
await waitFor(c, `document.querySelector('.run').dataset.situation==='fix'`, 30000); out.fixStop = await state();
await sleep(500); await shot(c, `${OUT}/s2-failed-fix-desktop.png`);
out.fixCard = { title: await txt('.appr .ap-title'), apply: await txt('.appr .btn-p'), decline: await txt('.appr .ap-acts .btn:not(.btn-p)'), pipeline: await c.ev(`[...document.querySelectorAll('.pipe .pn')].map(n=>n.dataset.state).join(',')`), resources: await c.ev(`[...document.querySelectorAll('.arch-list li:not(.edge-li)')].map(l=>l.textContent.trim().replace(/\\s+/g,' ')).join(' | ')`) };
await click(c, '.appr .btn-p');
await waitFor(c, `document.querySelector('.run').dataset.situation==='live'`, 30000); out.live = await state(); await sleep(1200);
await shot(c, `${OUT}/s2-live-desktop.png`);
out.liveFacts = { url: await txt('.url'), api: await c.ev(`[...document.querySelectorAll('.arch-list li:not(.edge-li)')].map(l=>l.textContent.trim().replace(/\\s+/g,' ')).join(' | ')`), tiles: await c.ev(`[...document.querySelectorAll('.tile')].map(t=>t.textContent.replace(/\\s+/g,' ').trim()).join(' | ')`) };

// 2. waiting sample: decline is calm
await open(c, '/demo/waiting'); await waitFor(c, `!!document.querySelector('.appr .opts .opt')`); await click(c, '.appr .opts .opt');
await waitFor(c, `!!document.querySelector('.appr .btn-p')`); await click(c, '.appr .ap-acts .btn:not(.btn-p)'); await sleep(900);
out.declined = await state(); out.declinedBadges = await c.ev(`[...document.querySelectorAll('.run .badge')].map(b=>b.textContent.trim()).join(',')`);
out.declinedPipeline = await c.ev(`[...document.querySelectorAll('.pipe .pn')].map(n=>n.dataset.state).join(',')`);
await shot(c, `${OUT}/s2-declined-desktop.png`);
// 3. waiting sample: apply continues with the dry-run deploy events
await open(c, '/demo/waiting'); await waitFor(c, `!!document.querySelector('.appr .opts .opt')`); await click(c, '.appr .opts .opt');
await waitFor(c, `!!document.querySelector('.appr .btn-p')`); await click(c, '.appr .btn-p');
await waitFor(c, `document.querySelector('.run').dataset.situation==='preview'||document.querySelector('.run').dataset.situation==='live'`, 30000); out.applied = await state();
await shot(c, `${OUT}/s2-applied-desktop.png`);
// 4. dry run
await open(c, '/demo/dryrun'); await waitFor(c, `!!document.querySelector('.appr .opts .opt')`); await click(c, '.appr .opts .opt'); await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='preview'`, 30000);
out.dry = await state(); await sleep(1000); await shot(c, `${OUT}/s2-dryrun-desktop.png`);
// 5. phone: the sticky bar carries Apply and Decline when the card's own Apply is out of view
await open(c, '/demo/waiting', { w: 390, h: 844 }); await waitFor(c, `!!document.querySelector('.appr .opts .opt')`); await click(c, '.appr .opts .opt');
await waitFor(c, `!!document.querySelector('.appr .btn-p')`); await sleep(600);
out.phoneSticky = await c.ev(`(function(){var b=document.querySelector('.sticky-bar');if(!b)return null;var r=b.getBoundingClientRect();var bs=[...b.querySelectorAll('button')].map(x=>Math.round(x.getBoundingClientRect().height));return {bottom:Math.round(r.bottom),vh:innerHeight,buttons:bs}})()`);
await shot(c, `${OUT}/s2-phone-waiting.png`);
out.under44 = (await c.ev(MEASURE)).under44;
out.consoleErrors = errors(c);
console.log(JSON.stringify(out, null, 1)); c.close(); srv.stop();
