// Cold load: an empty cache (fresh browser profile) and a slow network, so the webfont swap really happens in the middle of the page.
// It samples the Apply and Decline rectangles on every frame from the first moment they exist, and reports how far they ever move.
import { launch, sleep } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer();
const results = {};
async function run(label, { lang = 'en', w = 1440, h = 900, adjust = true, hold = 0 }) {
  const c = await launch();            // a new Chrome with a new, empty profile: nothing cached
  await c.send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
  await c.send('Network.enable'); await c.send('Network.setCacheDisabled', { cacheDisabled: true });
  await c.send('Network.emulateNetworkConditions', { offline: false, latency: 150, downloadThroughput: 250 * 1024, uploadThroughput: 250 * 1024 });
  if (!adjust) await c.send('Page.addScriptToEvaluateOnNewDocument', { source: `document.addEventListener('DOMContentLoaded',function(){var s=document.createElement('style');s.textContent='@font-face{font-family:"Sora Fallback";src:local("Arial");size-adjust:100%;ascent-override:normal;descent-override:normal;line-gap-override:normal}@font-face{font-family:"Vazirmatn Fallback";src:local("Arial");size-adjust:100%;ascent-override:normal;descent-override:normal;line-gap-override:normal}@font-face{font-family:"JetBrains Mono Fallback";src:local("Courier New");size-adjust:100%;ascent-override:normal;descent-override:normal;line-gap-override:normal}';document.head.appendChild(s)})` });
  await c.send('Page.addScriptToEvaluateOnNewDocument', { source: `localStorage.setItem('lang','${lang}');window.__s=[];window.__f=[];new PerformanceObserver(l=>l.getEntries().forEach(e=>{if(!e.hadRecentInput)window.__s.push(+e.value)})).observe({type:'layout-shift',buffered:true});
    (function f(){var a=document.querySelector('.appr .btn-p'),d=document.querySelector('.appr .ap-acts .btn:not(.btn-p)');if(a&&d){var r=a.getBoundingClientRect(),q=d.getBoundingClientRect();var sb=document.querySelector('.sticky-bar .btn-p');var sr=sb?sb.getBoundingClientRect():null;window.__f.push([Math.round(performance.now()),r.x,r.y,r.width,r.height,q.x,q.y,q.width,q.height,sr?sr.x:-1,sr?sr.y:-1].map(function(v){return Math.round(v*100)/100}))}requestAnimationFrame(f)})();
    new PerformanceObserver(l=>l.getEntries().forEach(e=>{if(/woff2/.test(e.name))window.__w=(window.__w||[]).concat([[e.name.split('/').pop(),Math.round(e.responseEnd)]])})).observe({type:'resource',buffered:true});` });
  // hold = the font files arrive this many ms late, so the swap happens AFTER the approval card is on screen (the worst case for a layout shift)
  let pump = null;
  if (hold) {
    await c.send('Fetch.enable', { patterns: [{ urlPattern: '*.woff2', requestStage: 'Request' }] });
    pump = setInterval(() => { while (c.events.length) { const e = c.events.shift(); if (e.method === 'Fetch.requestPaused') setTimeout(() => c.send('Fetch.continueRequest', { requestId: e.params.requestId }).catch(() => {}), hold); } }, 20);
  }
  await c.send('Page.navigate', { url: srv.url + '/demo/waiting?at=10' });
  await sleep(hold ? hold + 9000 : 8500);
  if (pump) clearInterval(pump);
  const r = await c.ev(`(function(){var f=window.__f;var first=f[0],last=f[f.length-1];var xs=f.map(r=>r[1]),ys=f.map(r=>r[2]),w=f.map(r=>r[3]),h=f.map(r=>r[4]),dx=f.map(r=>r[5]),dy=f.map(r=>r[6]);
    var span=a=>+(Math.max.apply(null,a)-Math.min.apply(null,a)).toFixed(2);
    return {samples:f.length,firstAt:first&&first[0],apply:{x:span(xs),y:span(ys),w:span(w),h:span(h)},decline:{x:span(dx),y:span(dy)},sticky:(function(){var ys=f.map(r=>r[10]).filter(v=>v>=0);return ys.length?{samples:ys.length,movedY:span(ys)}:null})(),finalApply:last&&[last[1],last[2],last[3],last[4]],fonts:window.__w||[],clsTotal:+window.__s.reduce((a,b)=>a+b,0).toFixed(4),fontsReady:document.fonts.status}})()`);
  results[label] = r; c.close();
}
await run('en 1440x900 | fonts arrive with the page (preloaded)', { lang: 'en' });
await run('en 1440x900 | fonts arrive 6 s LATE, metric-adjusted fallback (the build)', { lang: 'en', hold: 6000 });
await run('en 1440x900 | fonts arrive 6 s LATE, fallback NOT adjusted (comparison)', { lang: 'en', hold: 6000, adjust: false });
await run('fa 1440x900 | fonts arrive 6 s LATE, metric-adjusted fallback (the build)', { lang: 'fa', hold: 6000 });
await run('fa 1440x900 | fonts arrive 6 s LATE, fallback NOT adjusted (comparison)', { lang: 'fa', hold: 6000, adjust: false });
await run('en 390x844 phone | fonts arrive 6 s LATE, metric-adjusted fallback', { lang: 'en', w: 390, h: 844, hold: 6000 });
await run('fa 390x844 phone | fonts arrive 6 s LATE, metric-adjusted fallback', { lang: 'fa', w: 390, h: 844, hold: 6000 });
console.log(JSON.stringify(Object.fromEntries(Object.entries(results).map(([k, v]) => [k, { firstApplyAt_ms: v.firstAt, applyMovedPx: v.apply, declineMovedPx: v.decline, stickyBarApplyMovedPx: v.sticky, layoutShiftScore: v.clsTotal, fontsLoadedAt_ms: v.fonts.map((f) => f[1]).join(',') }])), null, 1)); srv.stop();
