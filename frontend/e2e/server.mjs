// Starts the real API (liara-agent serve) serving frontend/dist from its own origin, with a throw-away data dir and no .env (cwd is a temp dir).
// No WEB_ALLOWED_ORIGINS override: the same-origin path is exactly what production uses. Always stopped when the script ends.
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
const ROOT = path.resolve(import.meta.dirname, '../..');           // liara-agent/
const free = () => new Promise((res) => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => res(p)); }); });
export async function startServer({ env = {} } = {}) {
  const port = await free();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-e2e-'));
  const bin = process.env.LIARA_AGENT_BIN || path.join(ROOT, '.venv/bin/liara-agent');
  const p = spawn(bin, ['serve', '--port', String(port)], { cwd: dir, stdio: 'ignore', env: { ...process.env, ...env, WEB_DATA_DIR: path.join(dir, 'data'), WEB_STATIC_DIR: path.join(ROOT, 'frontend/dist') } });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 60; i++) { try { if ((await fetch(url + '/api/health')).ok) break; } catch { /* not up yet */ } await new Promise((r) => setTimeout(r, 250)); }
  process.env.APP_URL = url;
  const stop = () => { try { p.kill('SIGTERM'); } catch { /* gone */ } fs.rmSync(dir, { recursive: true, force: true }); };
  process.on('exit', stop);
  return { url, stop };
}
