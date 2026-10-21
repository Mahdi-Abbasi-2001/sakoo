// Merges the message catalog (src/i18n/catalog.source.json, taken from the design's catalog board) into en.json and fa.json.
// Keys: msg.<type>.<code> for the wording, msg.diagnosis.<code>.title, msg.fix.<code>.detail, err.<code> for API errors, status.<code>.
// Hand-written err.* keys that carry a {status} placeholder are kept. The list of wording that is only inferred goes to inferred.json for review.
import fs from 'node:fs';
import path from 'node:path';
const dir = path.resolve(import.meta.dirname, '../src/i18n');
const src = JSON.parse(fs.readFileSync(path.join(dir, 'catalog.source.json'), 'utf8')).rows;
const files = { en: path.join(dir, 'en.json'), fa: path.join(dir, 'fa.json') };
const cat = { en: JSON.parse(fs.readFileSync(files.en, 'utf8')), fa: JSON.parse(fs.readFileSync(files.fa, 'utf8')) };
for (const l of ['en', 'fa']) for (const k of Object.keys(cat[l])) if (k.startsWith('msg.') || k.startsWith('status.run') || /^status\.(running|waiting|succeeded|failed|cancelled|interrupted)$/.test(k)) delete cat[l][k];
const inferred = [];
function put(key, en, fa, inf) { cat.en[key] = en; if (fa != null) cat.fa[key] = fa; if (inf) inferred.push(key); }
for (const r of src) {
  const inf = r.src === 'inferred';
  if (r.type === 'apierror') { const k = 'err.' + r.code; if (!(k in cat.en)) put(k, r.en, r.fa, inf); continue; }
  if (r.type === 'status') { put('status.' + r.code, r.en, r.fa, inf); continue; }
  const base = `msg.${r.type}.${r.code}`;
  put(base, r.en, r.fa, inf);
  if (r.ten) put(base + '.title', r.ten, r.tfa, inf);          // diagnosis title, fix detail
  if (r.type === 'fix' && r.ten) { delete cat.en[base + '.title']; delete cat.fa[base + '.title']; put(base + '.detail', r.ten, r.tfa, inf); }
}
const ov = JSON.parse(fs.readFileSync(path.join(dir, 'overrides.json'), 'utf8'));
for (const l of ['en', 'fa']) for (const [k, v] of Object.entries(ov[l] ?? {})) { cat[l][k] = v; const i = inferred.indexOf(k); if (i >= 0) inferred.splice(i, 1); }
const sorted = (o) => Object.fromEntries(Object.entries(o).sort(([a], [b]) => a.localeCompare(b)));
fs.writeFileSync(files.en, JSON.stringify(sorted(cat.en), null, 2) + '\n');
fs.writeFileSync(files.fa, JSON.stringify(sorted(cat.fa), null, 2) + '\n');
fs.writeFileSync(path.join(dir, 'inferred.json'), JSON.stringify({ _about: 'Persian and English wording written from the code name and params only (not from a real sample). Confirm these first.', keys: [...new Set(inferred)].sort() }, null, 1) + '\n');
console.log('messages merged:', Object.keys(cat.en).length, 'en keys,', Object.keys(cat.fa).length, 'fa keys,', new Set(inferred).size, 'flagged inferred');
