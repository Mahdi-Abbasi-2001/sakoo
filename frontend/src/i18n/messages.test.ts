import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import en from './en.json';
import fa from './fa.json';
import { msgText, msgKey, type MsgType } from './messages';
import { translate } from './index';
import type { RunEvent } from '../api/events';

const dir = path.resolve(__dirname, '../demo/samples');
const streams = ['events_waiting_for_approval.json', 'events_dry_run_compose.json', 'events_build_timeout_then_fixed.json'].map((f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')) as RunEvent[]);

/** Every (type, code) pair a sample uses. */
function codesUsed(): { type: MsgType; code: string; where: string }[] {
  const out: { type: MsgType; code: string; where: string }[] = [];
  const add = (type: MsgType, code: unknown, where: string) => { if (typeof code === 'string' && code) out.push({ type, code, where }); };
  for (const evs of streams) for (const e of evs) {
    const d = e.data as Record<string, any>;
    switch (e.kind) {
      case 'plan_ready':
        for (const n of d.plan.notes) add('note', n.code, 'plan.notes');
        for (const q of d.plan.questions) if (typeof q === 'object') add('question', q.code, 'plan.questions');
        for (const f of d.readiness.findings) add('readiness', f.code, 'readiness');
        break;
      case 'question': add('question', d.code, 'question'); for (const o of d.options ?? []) if (d.code) add('option', `${d.code}.${o}`, 'question.options'); break;
      case 'confirm_request': add('confirm', d.code, 'confirm_request'); break;
      case 'warning': add('warning', d.code, 'warning'); break;
      case 'diagnosis': add('diagnosis', d.code, 'diagnosis'); for (const f of d.fixes ?? []) add('fix', f.code, 'diagnosis.fixes'); break;
      case 'fix_applied': case 'fix_declined': add('fix', d.code, e.kind); break;
      case 'run_finished': if (d.reason_code) out.push({ type: (en as Record<string, string>)[msgKey('outcome', d.reason_code)] ? 'outcome' : 'diagnosis', code: d.reason_code, where: 'run_finished.reason_code' }); break;
    }
  }
  return out;
}

describe('i18n: the codes in the samples', () => {
  const used = codesUsed();
  it('finds codes of every kind the samples use', () => { expect(new Set(used.map((u) => u.type))).toEqual(new Set(['note', 'question', 'option', 'confirm', 'diagnosis', 'fix', 'outcome', 'readiness'].filter((t) => used.some((u) => u.type === t)))); expect(used.length).toBeGreaterThan(8); });
  it('every code used in the samples has an English entry', () => {
    const missing = used.filter((u) => !(en as Record<string, string>)[msgKey(u.type, u.code)]).map((u) => `${u.type}:${u.code} (${u.where})`);
    expect(missing).toEqual([]);
  });
  it('and a Persian entry', () => {
    const missing = used.filter((u) => !(fa as Record<string, string>)[msgKey(u.type, u.code)]).map((u) => `${u.type}:${u.code}`);
    expect(missing).toEqual([]);
  });
  it('wording fills its parameters (numbers localized, names left alone)', () => {
    expect(msgText('en', 'confirm', 'create_resources', { apps: 2, databases: 2, hourly: 4583, monthly: 3300000 })).toBe('Create 2 apps and 2 databases on your Liara account? About 4,583 toman/hour (3,300,000/month) including VAT.');
    expect(msgText('fa', 'confirm', 'create_resources', { apps: 2, databases: 2, hourly: 4583, monthly: 3300000 })).toContain('۴٬۵۸۳');
    expect(msgText('fa', 'question', 'worker_without_port', { service: 'worker' })).toContain('worker');
    expect(msgText('en', 'diagnosis', 'build_timeout', { tier_label: 'basic', limit_minutes: 5 })).toContain('5-minute');
  });
  it('the plan approval has correct plural forms in English', () => {
    const say = (apps: number, databases: number) => msgText('en', 'confirm', 'create_resources', { apps, databases, hourly: 1146, monthly: 825000 })!.split(' on your')[0];
    expect(say(1, 0)).toBe('Create 1 app and no databases');
    expect(say(1, 1)).toBe('Create 1 app and 1 database');
    expect(say(2, 1)).toBe('Create 2 apps and 1 database');
    expect(say(0, 2)).toBe('Create no apps and 2 databases');
    expect(say(3, 2)).toBe('Create 3 apps and 2 databases');
  });
  it('and in Persian (one form for counted nouns, a word for none)', () => {
    const say = (apps: number, databases: number) => msgText('fa', 'confirm', 'create_resources', { apps, databases, hourly: 1146, monthly: 825000 })!.split(' روی حساب')[0];
    expect(say(1, 0)).toBe('۱ برنامه و هیچ دیتابیسی');
    expect(say(2, 2)).toBe('۲ برنامه و ۲ دیتابیس');
    expect(say(0, 1)).toBe('هیچ برنامه‌ای و ۱ دیتابیس');
  });
  it('a code with no entry gives undefined so the caller can show the backend text', () => { expect(msgText('en', 'note', 'a_code_from_the_future', {})).toBeUndefined(); });
  it('a missing Persian key falls back to English, then to the key', () => { expect(translate('fa', 'app.name')).toBe(fa['app.name']); expect(translate('fa', 'no.such.key')).toBe('no.such.key'); });
});

describe('i18n: catalog hygiene', () => {
  const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',');
  it('Persian has every English key', () => { expect(Object.keys(en).filter((k) => !(k in fa))).toEqual([]); });
  it('Persian has no key English lacks', () => { expect(Object.keys(fa).filter((k) => !(k in en))).toEqual([]); });
  it('both languages use the same {placeholders}', () => {
    const bad = Object.keys(en).filter((k) => k in fa && ph((en as Record<string, string>)[k]) !== ph((fa as Record<string, string>)[k]));
    expect(bad).toEqual([]);
  });
});

import { howText, sendText } from './messages';
describe('plan source and planned requests', () => {
  it('words how_code and would_send ops in both languages, English summary only as fallback', () => {
    expect(howText('en', 'from_compose', { file: 'docker-compose.yml' }, 'x')).toContain('docker-compose.yml');
    expect(howText('fa', 'investigated_by_agent', {}, 'x')).not.toBe('x');
    expect(howText('fa', 'unknown_code', {}, 'fallback')).toBe('fallback');
    const w = { op: 'set_env', params: { app: 'blog-web', count: 3 }, summary: 'set 3 environment variables on blog-web' };
    expect(sendText('en', w)).toBe('Set 3 environment variables on blog-web');
    expect(sendText('fa', w)).toContain('۳ متغیر محیطی');
    expect(sendText('en', { ...w, params: { app: 'a', count: 1 } })).toContain('1 environment variable on');
    expect(sendText('fa', { summary: 'raw' })).toBe('raw');
    for (const op of ['create_app', 'create_database', 'upload_source', 'create_release', 'resize_app', 'update_port'])
      expect(sendText('fa', { op, params: { name: 'n', platform: 'docker', plan: 'p', tier: 't', type: 'postgres', version: '16', app: 'a', port: 80 }, summary: 'S' })).not.toBe('S');
  });
});
