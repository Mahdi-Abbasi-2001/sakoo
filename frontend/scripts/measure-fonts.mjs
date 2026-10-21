// Measures how wide each web font is next to the system font it falls back to, per weight, in a real browser, so the fallback faces in
// fonts.css can be scaled to match (size-adjust). Writes src/assets/fonts/metrics.json. Needs the built app in dist/ and Chrome (CHROME=...).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { launch } from '../e2e/cdp.mjs';
const ROOT = path.resolve(import.meta.dirname, '..'), DIST = path.join(ROOT, 'dist');
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' };
const srv = http.createServer((q, r) => { const p = path.join(DIST, q.url === '/' ? 'index.html' : q.url.split('?')[0]); fs.readFile(p, (e, b) => { if (e) { r.writeHead(404); r.end(); } else { r.writeHead(200, { 'Content-Type': types[path.extname(p)] || 'application/octet-stream' }); r.end(b); } }); });
await new Promise((r) => srv.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${srv.address().port}/`;
const c = await launch();
await c.send('Page.navigate', { url }); await new Promise((r) => setTimeout(r, 1500));
const out = await c.ev(`(async function(){
  const F={a:8.17,b:1.49,c:2.78,d:4.25,e:12.7,f:2.23,g:2.02,h:6.09,i:6.97,j:.15,k:.77,l:4.03,m:2.41,n:6.75,o:7.51,p:1.93,q:.1,r:5.99,s:6.33,t:9.06,u:2.76,v:.98,w:2.36,x:.15,y:1.97,z:.07,' ':15};
  let text='';for(const[k,v]of Object.entries(F))text+=k.repeat(Math.round(v*4));text+='0123456789'.repeat(3);
  const ctx=document.createElement('canvas').getContext('2d');
  const W=(font)=>{ctx.font=font;return ctx.measureText(text).width};
  const res={};
  const set=[['Sora','Arial',[400,600,800]],['Vazirmatn','Arial',[400,600,800]],['JetBrains Mono','Courier New',[400,600,800]]];
  for(const[fam,ref,ws]of set){res[fam]={};for(const w of ws){await document.fonts.load(w+' 100px "'+fam+'"','abc');res[fam][w]=+(W(w+' 100px "'+fam+'"')/W((w>=600?'bold':'normal')+' 100px '+ref)).toFixed(4)}}
  res._ref=['Arial','Courier New'].map(f=>{ctx.font='100px '+f;return f+': '+ctx.measureText('abcdefghijklmnopqrstuvwxyz').width.toFixed(1)});
  return res})()`);
fs.writeFileSync(path.join(ROOT, 'src/assets/fonts/metrics.json'), JSON.stringify(out, null, 1) + '\n');
console.log(JSON.stringify(out)); c.close(); srv.close();
