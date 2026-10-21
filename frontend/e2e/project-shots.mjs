// usage: node e2e/project-shots.mjs <outdir> [lang] [width]: the project page, one picture per tab, with made-up projects
import { launch, sleep, open, shot, waitFor, click, mockApi, type } from './helpers.mjs';
import { startServer } from './server.mjs';
import { buildMock } from './fixtures/mock-projects.mjs';
const [out = '/tmp', lang = 'en', width = '1440'] = process.argv.slice(2);
const srv = await startServer(); const c = await launch();
await open(c, '/register'); await waitFor(c, `!!document.querySelector('form')`);
await type(c, '#u', 'shots' + Date.now().toString(36)); await type(c, '#p', 'password123'); await click(c, 'button[type=submit]'); await sleep(1500);
await c.ev(`fetch('/api/settings',{method:'PUT',headers:{'Content-Type':'application/json','X-Requested-With':'XMLHttpRequest'},body:JSON.stringify({language:'${lang}'})})`);
const now = Date.now() / 1000;
const series = Array.from({ length: 150 }, (_, i) => { const ts = now - 86400 + i * 570; const down = i > 60 && i < 66; return { ts, ok: !down, ms: down ? 0 : 90 + Math.round(40 * Math.sin(i / 7) + (i % 11) * 4) }; });
const uptime = { interval_seconds: 300, watching: ['https://api.liara.run/'], checks: 150, up_percent: 96, current: true, last: null, avg_ms: 112, incidents: [{ down_at: now - 86400 + 61 * 570, up_at: now - 86400 + 66 * 570 }], series };
await mockApi(c, { ...buildMock(), 'GET /api/projects/2/uptime': uptime });
for (const tab of ['', '?tab=deploys', '?tab=settings']) {
  await open(c, '/projects/2' + tab, { w: +width, h: 1000, lang: lang === 'en' ? undefined : lang });
  await waitFor(c, `!!document.querySelector('.ptabs')`, 20000); await sleep(1800);
  await shot(c, `${out}/project-${lang}-${width}-${tab.replace('?tab=', '') || 'overview'}.png`);
}
c.close(); srv.stop();
