// Finds words in the TSX files that a person would read but that do not go through the catalog: text between tags, and the string
// attributes aria-label, placeholder, title, alt and aria-description. Identifiers, URLs, code names and single symbols are allowed.
import fs from 'node:fs';
import path from 'node:path';

const ALLOW_FILES = new Set(['src/components/Icon.tsx']);
const ALLOW_WORDS = /^(HTTPS?|JSON|API|URL|EN|FA|فا|Deploy Agent|docker|node|web|app|db|net1|http_200|POST|PUT|GET|DELETE|:\d+|[#×·•→←≈+\-–—\/\\|:;,.()0-9\s%]+)$/;
const looksTechnical = (s) => /^[\w@./:\-<>=%{}$#]+$/.test(s) && !/^[A-Z][a-z]+$/.test(s) && (/[./:_\-\d]/.test(s) || /^[A-Z0-9_]+$/.test(s) || /^[a-z]+$/.test(s) && s.length < 3);
const hasWords = (s) => /[A-Za-z؀-ۿ]{2,}/.test(s);

export function scanFile(file, root) {
  const rel = path.relative(root, file).split(path.sep).join('/');
  if (ALLOW_FILES.has(rel)) return [];
  const src = fs.readFileSync(file, 'utf8');
  const code = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/(^|[^:])\/\/[^\n]*/g, (m, a) => a + ' '.repeat(m.length - a.length));
  const out = [];
  const lineOf = (i) => code.slice(0, i).split('\n').length;
  // text between tags
  for (const m of code.matchAll(/>([^<>{}=]*[A-Za-z؀-ۿ][^<>{}]*)</g)) {
    const text = m[1].replace(/\s+/g, ' ').trim();
    if (!text || !hasWords(text) || ALLOW_WORDS.test(text) || looksTechnical(text)) continue;
    // comparison operators and generics (a > b, Promise<T>) are code, not JSX text
    if (/[;=(){}]|&&|\|\||=>|\?|\breturn\b|\bconst\b|\bPromise\b/.test(text)) continue;
    out.push({ file: rel, line: lineOf(m.index), kind: 'text', text });
  }
  for (const m of code.matchAll(/\b(aria-label|aria-description|placeholder|title|alt)=("([^"]*)"|'([^']*)')/g)) {
    const text = (m[3] ?? m[4] ?? '').trim();
    if (!text || !hasWords(text) || ALLOW_WORDS.test(text) || looksTechnical(text) || /^https?:/.test(text)) continue;
    out.push({ file: rel, line: lineOf(m.index), kind: m[1], text });
  }
  return out;
}
export function scanAll(root) {
  const res = [];
  const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) { if (e.name !== 'node_modules' && e.name !== 'demo' && e.name !== 'api') walk(p); } else if (p.endsWith('.tsx')) res.push(...scanFile(p, root)); } };
  walk(path.join(root, 'src'));
  return res;
}
