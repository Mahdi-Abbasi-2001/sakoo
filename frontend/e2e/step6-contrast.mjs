// Contrast of every text on every page and state, motion running, against the pixels actually painted behind it.
// usage: node e2e/step6-contrast.mjs <lang> <theme>
import fs from 'node:fs';
import { launch, errors } from './helpers.mjs';
import { startServer } from './server.mjs';
import { walk } from './scenarios.mjs';
import { measureContrast } from './lib/a11y.mjs';
const [lang = 'en', theme = 'dark'] = process.argv.slice(2);
const srv = await startServer(); const c = await launch();
const rows = []; const fails = new Map(); const disabled = new Map();
await walk(c, { lang, theme, widths: [1440, 390], async onState(label, w) {
  const r = await measureContrast(c);
  rows.push({ label: label + '@' + w, checked: r.checked, worst: r.worst });
  for (const f of r.fails) { const m = f.disabled ? disabled : fails; const k = f.text + '|' + f.cls; if (!m.has(k)) m.set(k, { ...f, where: [] }); m.get(k).where.push(label + '@' + w); }
} });
const out = { lang, theme, states: rows.length, textsChecked: rows.reduce((a, r) => a + r.checked, 0), worstOverall: Math.min(...rows.map((r) => r.worst)),
  fails: [...fails.values()].map((f) => ({ text: f.text, ratio: f.ratio, size: f.size, cls: f.cls, fg: f.fg, bg: f.bg, where: f.where.slice(0, 4) })),
  disabledOnly: [...disabled.values()].map((f) => ({ text: f.text, ratio: f.ratio, where: f.where.slice(0, 2) })), consoleErrors: errors(c).length };
fs.writeFileSync(`/tmp/contrast-${lang}-${theme}.json`, JSON.stringify({ ...out, rows }, null, 1));
console.log(JSON.stringify(out, null, 1));
c.close(); srv.stop();
process.exit(0);
