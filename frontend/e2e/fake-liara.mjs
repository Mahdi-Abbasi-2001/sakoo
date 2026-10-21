// A stand-in for api.liara.ir, so the REAL backend can run a REAL deploy run (up to the approval) without a Liara token and without touching Liara.
// The backend talks to https://api.liara.ir; here that name is reached through a local CONNECT proxy (HTTPS_PROXY) that tunnels to a local TLS
// server with a throw-away certificate (SSL_CERT_FILE). Only the read calls the preflight makes are answered; any write would get a 404.
import https from 'node:https';
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
const ROOT = path.resolve(import.meta.dirname, '../..');
export async function startFakeLiara({ teams = [], networks = [{ _id: 'n1', name: 'net1' }], apps = [], writes = false } = {}) {
  // writes: true also answers the calls of a deploy and of a cleanup (create, upload, release, delete) and the address check, all in memory
  const made = { apps: new Set(), dbs: new Set() };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'fake-liara-'));
  const key = path.join(dir, 'k.pem'), cert = path.join(dir, 'c.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert, '-days', '2', '-subj', '/CN=api.liara.ir', '-addext', 'subjectAltName=DNS:api.liara.ir,DNS:*.liara.run'], { stdio: 'ignore' });
  const me = JSON.parse(fs.readFileSync(path.join(ROOT, 'src/liara_agent/liara/me_snapshot.json'), 'utf8'));
  const log = [];
  const tls = https.createServer({ key: fs.readFileSync(key), cert: fs.readFileSync(cert) }, (q, r) => {
    const u = new URL(q.url, 'https://api.liara.ir'); log.push(q.method + ' ' + u.pathname);
    const send = (o, c = 200) => { r.writeHead(c, { 'Content-Type': 'application/json' }); r.end(JSON.stringify(o)); };
    if ((q.headers.host || '').endsWith('.liara.run')) { r.writeHead(writes ? 200 : 502, { 'Content-Type': 'text/plain' }); return r.end('ok'); }
    if (writes && q.method !== 'GET') {
      if (u.pathname === '/v1/projects' && q.method === 'POST') { made.apps.add('app' + log.length); return send({}); }
      if (u.pathname === '/v1/databases' && q.method === 'POST') { const h = 'db-' + log.length; made.dbs.add(h); return send({ databaseID: h }); }
      if (u.pathname === '/v1/projects/update-envs') return send({});
      if (/\/v2\/projects\/[^/]+\/sources$/.test(u.pathname)) return send({ sourceID: 'src1' });
      if (/\/v2\/projects\/[^/]+\/releases$/.test(u.pathname)) return send({ releaseID: 'rel1' });
      if (q.method === 'DELETE' && /^\/v1\/(projects|databases)\//.test(u.pathname)) { made.apps.delete(u.pathname.split('/').pop()); made.dbs.delete(u.pathname.split('/').pop()); return send({}); }
      return send({}, 200);
    }
    if (writes && /\/releases?\//.test(u.pathname) && q.method === 'GET') return send({ release: { state: 'READY' }, buildOutput: [] });
    if (q.method !== 'GET') return send({ error: 'the fake Liara answers reads only' }, 404);
    if (u.pathname === '/v1/me') return send(me);
    if (u.pathname === '/v2/teams') return send({ teams });
    if (u.pathname === '/v1/networks') return send({ networks });
    if (u.pathname === '/v1/projects') return send({ projects: apps.map((a) => ({ project_id: a })) });
    if (u.pathname === '/v1/databases') return send({ databases: [] });
    return send({ error: 'not found' }, 404);
  });
  await new Promise((r) => tls.listen(0, '127.0.0.1', r));
  const proxy = http.createServer((q, r) => { r.writeHead(502); r.end(); });
  proxy.on('connect', (req, sock, head) => {
    const [host] = req.url.split(':');
    if (host !== 'api.liara.ir' && !(writes && host.endsWith('.liara.run'))) { sock.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); return; }
    const up = net.connect(tls.address().port, '127.0.0.1', () => { sock.write('HTTP/1.1 200 Connection Established\r\n\r\n'); up.write(head); up.pipe(sock); sock.pipe(up); });
    up.on('error', () => sock.destroy()); sock.on('error', () => up.destroy());
  });
  await new Promise((r) => proxy.listen(0, '127.0.0.1', r));
  const p = `http://127.0.0.1:${proxy.address().port}`;
  return {
    log, made, env: { HTTPS_PROXY: p, https_proxy: p, SSL_CERT_FILE: cert, REQUESTS_CA_BUNDLE: cert, NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost' },
    stop: () => { tls.close(); proxy.close(); fs.rmSync(dir, { recursive: true, force: true }); },
  };
}
