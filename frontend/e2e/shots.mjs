// usage: node e2e/shots.mjs <path> <w> <h> <lang> <out.png> [js to run before the shot]
import { launch, sleep, open, shot, waitFor, click } from './helpers.mjs';
import { startServer } from './server.mjs';
const [path, w, h, lang, out, pre] = process.argv.slice(2);
const srv = await startServer(); const c = await launch();
await open(c, path, { w: +w, h: +h, lang: lang === 'en' ? undefined : lang });
if (pre) await c.ev(pre);
await sleep(+(process.env.WAIT || 1500));
await shot(c, out); c.close(); srv.stop();
