import path from 'node:path';
import { scanAll } from './lib/hardcoded.mjs';
const found = scanAll(path.resolve(import.meta.dirname, '..'));
for (const f of found) console.log(`${f.file}:${f.line}  [${f.kind}]  ${f.text}`);
console.log(found.length ? `\n${found.length} string(s) outside the catalog` : 'no strings outside the catalog');
process.exit(found.length ? 1 : 0);
