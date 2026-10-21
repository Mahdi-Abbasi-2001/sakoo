import { spawn } from 'node:child_process';
import fs from 'node:fs';
const PORT = +(process.env.CDP_PORT || 9333);
export async function launch() {
  const ud = fs.mkdtempSync('/tmp/cdp-ud-');
  const p = spawn(process.env.CHROME || '/usr/bin/google-chrome', ['--headless=new', '--no-sandbox', '--disable-gpu', ...(process.env.SHOW_SCROLLBARS ? [] : ['--hide-scrollbars']), '--remote-debugging-port=' + PORT, '--user-data-dir=' + ud, 'about:blank'], { stdio: 'ignore', detached: true });
  let info;
  for (let i = 0; i < 50; i++) { try { info = await (await fetch('http://127.0.0.1:' + PORT + '/json/list')).json(); if (info.length) break; } catch (e) {} await new Promise(r => setTimeout(r, 200)); }
  const page = info.find(x => x.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let id = 0; const pend = new Map(); const events = [];
  ws.addEventListener('message', m => { const d = JSON.parse(m.data); if (d.id && pend.has(d.id)) { pend.get(d.id)(d); pend.delete(d.id); } else if (d.method) events.push(d); });
  const send = (method, params = {}) => new Promise(res => { const i = ++id; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  await send('Runtime.enable'); await send('Page.enable'); await send('Log.enable');
  const ev = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true }); if (r.result.exceptionDetails) throw new Error(JSON.stringify(r.result.exceptionDetails).slice(0, 400)); return r.result.result.value; };
  const close = () => { try { ws.close(); } catch (e) {} try { process.kill(-p.pid, 'SIGKILL'); } catch (e) { try { p.kill('SIGKILL'); } catch (e2) {} } setTimeout(() => { try { fs.rmSync(ud, { recursive: true, force: true }); } catch (e) {} }, 500); };
  return { send, ev, events, close };
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
