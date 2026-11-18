// Headless Chrome screenshots of the built site, over the Chrome DevTools protocol. No dependency: Node 22 or newer.
// usage: node shoot.mjs <out dir> <job> [<job> ...]   job = name|url|width|light or dark|max height|scale|top offset
// SHOW=1 lists every request a page made besides itself.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
const out = resolve(process.argv[2]); mkdirSync(out, { recursive: true });
const jobs = process.argv.slice(3).map((j) => { const [name, url, width, mode, maxh, scale, top] = j.split('|'); return { name, url, width: +width, mode, maxh: +(maxh || 6000), scale: +(scale || 1), top: +(top || 0) }; });
const profile = mkdtempSync(join(tmpdir(), 'recortex-site-chrome-'));
const chrome = spawn('google-chrome', ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run', '--hide-scrollbars', '--window-size=1440,900', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let port = null;
for (let i = 0; i < 100 && !port; i++) { await sleep(100); const p = join(profile, 'DevToolsActivePort'); if (existsSync(p)) port = readFileSync(p, 'utf8').split('\n')[0]; }
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let seq = 0; const waiting = new Map(); let messages = [], requests = [];
ws.addEventListener('message', (m) => { const msg = JSON.parse(m.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg.result || msg); waiting.delete(msg.id); return; }
  if (msg.method === 'Runtime.consoleAPICalled') messages.push('console ' + msg.params.type + ': ' + msg.params.args.map((a) => a.value ?? a.description).join(' '));
  if (msg.method === 'Runtime.exceptionThrown') messages.push('exception: ' + msg.params.exceptionDetails.text + ' ' + (msg.params.exceptionDetails.exception?.description || '').slice(0, 200));
  if (msg.method === 'Log.entryAdded') messages.push('log: ' + msg.params.entry.text.slice(0, 300));
  if (msg.method === 'Network.requestWillBeSent') requests.push(msg.params.request.url); });
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text); return r.result.value; };
for (const d of ['Runtime', 'Log', 'Network', 'Page']) await send(d + '.enable');
for (const job of jobs) {
  messages = []; requests = [];
  const phone = job.width < 700;
  await send('Emulation.setDeviceMetricsOverride', { width: job.width, height: phone ? 844 : 900, deviceScaleFactor: job.scale, mobile: phone });
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: job.mode }] });
  await send('Page.navigate', { url: job.url }); await sleep(1100);
  const h = await ev('Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)');
  const w = await ev('document.documentElement.scrollWidth');
  const clip = { x: 0, y: job.top, width: job.width, height: Math.max(1, Math.min(h - job.top, job.maxh)), scale: 1 };
  const r = await send('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true });
  writeFileSync(join(out, job.name + '.png'), Buffer.from(r.data, 'base64'));
  const others = requests.filter((u) => u !== job.url.split('#')[0] && u !== job.url);
  console.log(`${job.name}: height ${h}, page width ${w}${w > job.width ? ' (WIDER THAN THE SCREEN)' : ''}, other requests ${others.length}, messages ${messages.length}`);
  if (process.env.SHOW) { others.forEach((u) => console.log('   req ' + u)); }
  messages.slice(0, 6).forEach((m) => console.log('   ' + m));
}
ws.close(); chrome.kill();
