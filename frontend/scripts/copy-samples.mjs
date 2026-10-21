// Copies docs/samples/*.json into src/demo/samples so the demo routes and the tests work without the backend and without the docs folder.
import fs from 'node:fs'; import path from 'node:path';
const from = path.resolve(import.meta.dirname, '../../docs/samples'), to = path.resolve(import.meta.dirname, '../src/demo/samples');
fs.mkdirSync(to, { recursive: true });
for (const f of fs.readdirSync(from).filter((x) => x.endsWith('.json'))) fs.copyFileSync(path.join(from, f), path.join(to, f));
console.log('samples copied:', fs.readdirSync(to).join(', '));
