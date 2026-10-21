import { describe, expect, it } from 'vitest';
import path from 'node:path';
// @ts-expect-error a plain .mjs helper shared with `npm run lint:i18n`
import { scanAll } from '../../scripts/lib/hardcoded.mjs';

describe('every word a person reads goes through the catalog', () => {
  it('finds no text between tags and no aria-label, placeholder, title or alt outside the catalog', () => {
    const found = scanAll(path.resolve(__dirname, '../..')) as { file: string; line: number; kind: string; text: string }[];
    expect(found.map((f) => `${f.file}:${f.line} [${f.kind}] ${f.text}`)).toEqual([]);
  });
});
