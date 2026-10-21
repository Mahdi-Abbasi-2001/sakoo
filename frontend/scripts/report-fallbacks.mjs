// What a Persian reader still gets in English, and why. Three parts:
//  1. catalog keys whose Persian text is the English text (not translated yet);
//  2. wording that exists in Persian but was written from the code name and params only (to be reviewed);
//  3. text the backend writes itself and the page can only show as received, with real examples from docs/samples.
import fs from 'node:fs';
import path from 'node:path';
const dir = path.resolve(import.meta.dirname, '../src');
const en = JSON.parse(fs.readFileSync(path.join(dir, 'i18n/en.json'), 'utf8')), fa = JSON.parse(fs.readFileSync(path.join(dir, 'i18n/fa.json'), 'utf8'));
const inferred = JSON.parse(fs.readFileSync(path.join(dir, 'i18n/inferred.json'), 'utf8')).keys;
const samples = ['events_waiting_for_approval', 'events_dry_run_compose', 'events_build_timeout_then_fixed'].map((f) => JSON.parse(fs.readFileSync(path.join(dir, `demo/samples/${f}.json`), 'utf8')));
const words = (s) => (s.match(/[A-Za-z]{3,}/g) ?? []);
console.log('1. KEYS WHOSE PERSIAN IS THE ENGLISH TEXT');
const same = Object.keys(en).filter((k) => fa[k] === en[k] && words(en[k].replace(/\{\w+\}/g, '')).length > 0);
console.log(same.length ? same.map((k) => `   ${k} = ${JSON.stringify(en[k])}`).join('\n') : '   none');
console.log('\n   keys missing in fa:', Object.keys(en).filter((k) => !(k in fa)).length, '  keys only in fa:', Object.keys(fa).filter((k) => !(k in en)).length);
console.log('\n2. PERSIAN WORDING WRITTEN FROM THE CODE NAME AND PARAMS ONLY (' + inferred.length + ' keys, list in src/i18n/inferred.json)');
const by = {}; for (const k of inferred) { const t = k.split('.')[1] ?? k.split('.')[0]; by[t] = (by[t] ?? 0) + 1; }
console.log('   ' + Object.entries(by).map(([k, v]) => `${k}: ${v}`).join(', '));
console.log('\n3. TEXT THE BACKEND WRITES ITSELF, SHOWN AS RECEIVED');
const uniq = (a) => [...new Set(a)];
const take = (kind, f) => uniq(samples.flat().filter((e) => e.kind === kind).flatMap((e) => f(e.data)).filter(Boolean));
const rows = [
  ['plan evidence[].note (identifiers by contract, shown as written)', 'chips next to every app and database', take('plan_ready', (d) => [...d.plan.apps, ...d.plan.databases].flatMap((x) => x.evidence.map((v) => v.note)))],
  ['diagnosis.evidence[]', 'timeline, under a diagnosis', take('diagnosis', (d) => d.evidence)],
  ['tool_call.name / arguments, every log line', 'Logs tab (technical, left to right on purpose)', take('tool_call', (d) => [d.name])],
  ['notes[].message, questions[].text, options[], agent_message.text with code null', 'written by the AI in the language chosen in Settings; none in the samples', []],
  ['warning / diagnosis / confirm / fix / run_finished text for a code the catalog does not know', 'the backend English is shown (a newer backend than this page)', []],
];
for (const [field, where, ex] of rows) { console.log(`   - ${field}\n       shown in: ${where}${ex.length ? '\n       e.g. ' + ex.map((x) => JSON.stringify(String(x).slice(0, 80))).join(', ') : ''}`); }
console.log('\n   Kept in English on purpose (identifiers, never translated): file and path names, variable names, ports, versions, engine and platform names (docker, postgres, redis, node), plan names (small-g2), tier labels (basic, silver, gold), app and database names, URLs, HTTP status numbers, event kinds in the log, language names (English / فارسی).');
