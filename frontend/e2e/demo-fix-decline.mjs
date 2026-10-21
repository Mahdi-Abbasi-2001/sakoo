import { launch, sleep, open, click, errors, waitFor } from './helpers.mjs';
import { startServer } from './server.mjs';
const srv = await startServer(); const c = await launch();
await open(c, '/demo/failed?at=23'); await waitFor(c, `document.querySelector('.run')&&document.querySelector('.run').dataset.situation==='fix'`);
await click(c, '.appr .ap-acts .btn:not(.btn-p)'); await sleep(700);
console.log(JSON.stringify({ situation: await c.ev(`document.querySelector('.run').dataset.situation`), h1: await c.ev(`document.querySelector('.h1').textContent`), badges: await c.ev(`[...document.querySelectorAll('.result .badge')].map(b=>b.textContent.trim()).join(',')`), card: await c.ev(`(document.querySelector('.result .ap-title')||{}).textContent`), created: await c.ev(`(document.querySelector('.result .hint')||{}).textContent`), cleanupButton: await c.ev(`!!document.querySelector('.result .btn-d')`), errors: errors(c) }, null, 1));
c.close(); srv.stop();
