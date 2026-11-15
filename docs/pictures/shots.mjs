// Headless Chrome screenshots. usage: node shots.mjs <file.html> <shots dir> <prefix> <width> <hash,hash,...|-> [maxheight] [scale] [top]
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync, writeFileSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
const file = resolve(process.argv[2]), shots = resolve(process.argv[3]), prefix = process.argv[4], width = +process.argv[5];
const hashes = process.argv[6] === '-' ? [''] : process.argv[6].split(','), maxh = +(process.argv[7] || 2600), scale = +(process.argv[8] || 2), top = +(process.argv[9] || 0);
mkdirSync(shots, { recursive: true }); const profile = mkdtempSync(join(tmpdir(), 'recortex-chrome-'));
const chrome = spawn('google-chrome', ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run', '--window-size=1400,1000', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let port = null;
for (let i = 0; i < 100 && !port; i++) { await sleep(100); const p = join(profile, 'DevToolsActivePort'); if (existsSync(p)) port = readFileSync(p, 'utf8').split('\n')[0]; }
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let seq = 0; const waiting = new Map(); const messages = [], requests = [];
ws.addEventListener('message', (m) => { const msg = JSON.parse(m.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg.result || msg); waiting.delete(msg.id); return; }
  if (msg.method === 'Runtime.consoleAPICalled') messages.push('console ' + msg.params.type + ': ' + msg.params.args.map((a) => a.value ?? a.description).join(' '));
  if (msg.method === 'Runtime.exceptionThrown') messages.push('exception: ' + msg.params.exceptionDetails.text);
  if (msg.method === 'Log.entryAdded') messages.push('log: ' + msg.params.entry.text.slice(0, 300));
  if (msg.method === 'Network.requestWillBeSent') requests.push(msg.params.request.url); });
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text); return r.result.value; };
for (const d of ['Runtime', 'Log', 'Network', 'Page']) await send(d + '.enable');
await send('Emulation.setDeviceMetricsOverride', { width, height: Math.min(maxh, 1200), deviceScaleFactor: scale, mobile: false });
const shot = async (name) => { await ev('scrollTo(0,0)'); await sleep(400);
  const m = await send('Page.getLayoutMetrics'); const h = await ev('Math.max(document.body.scrollHeight, document.querySelector("main") ? document.querySelector("main").scrollHeight : 0)');
  const clip = { x: 0, y: top, width: m.cssContentSize.width, height: Math.min(Math.max(h - top, 600), maxh), scale: 1 };
  const r = await send('Page.captureScreenshot', { format: 'png', clip, captureBeyondViewport: true }); writeFileSync(join(shots, name + '.png'), Buffer.from(r.data, 'base64')); };
for (const mode of ['light', 'dark']) {
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: mode }] });
  await send('Page.navigate', { url: 'file://' + file }); await sleep(900);
  for (const h of hashes) { if (h) { await ev(`location.hash = ${JSON.stringify('#' + h)}`); await sleep(300); } await shot(`${prefix}-${mode}${h ? '-' + h : ''}`); }
}
const outside = requests.filter((u) => u !== 'file://' + file && u !== 'about:blank');
console.log('requests besides the page:', outside.length, outside.slice(0, 5), '| console, exceptions, log:', messages.length); messages.slice(0, 8).forEach((m) => console.log('  ' + m));
console.log('page width:', await ev('document.documentElement.scrollWidth'));
ws.close(); chrome.kill();
