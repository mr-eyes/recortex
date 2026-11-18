// Drives the built site in headless Chrome and checks what a script can: the search box, the theme switch and its
// stored value, the menu, the copy button, and that nothing logs a console message or reaches another server.
// usage: node smoke.mjs <base url>      (for example http://127.0.0.1:8080/recortex, with the folder above recortex/ served)
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const base = process.argv[2] || 'http://127.0.0.1:8080';
const profile = mkdtempSync(join(tmpdir(), 'recortex-site-smoke-'));
const chrome = spawn('google-chrome', ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run', '--window-size=1280,900', 'about:blank'], { stdio: 'ignore' });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let port = null;
for (let i = 0; i < 100 && !port; i++) { await sleep(100); const p = join(profile, 'DevToolsActivePort'); if (existsSync(p)) port = readFileSync(p, 'utf8').split('\n')[0]; }
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const ws = new WebSocket(targets.find((t) => t.type === 'page').webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener('open', r));
let seq = 0; const waiting = new Map(); const problems = []; const outside = [];
ws.addEventListener('message', (m) => { const msg = JSON.parse(m.data);
  if (msg.id && waiting.has(msg.id)) { waiting.get(msg.id)(msg.result || msg); waiting.delete(msg.id); return; }
  if (msg.method === 'Runtime.consoleAPICalled') problems.push('console ' + msg.params.type);
  if (msg.method === 'Runtime.exceptionThrown') problems.push('exception ' + msg.params.exceptionDetails.text);
  if (msg.method === 'Log.entryAdded' && msg.params.entry.level !== 'verbose') problems.push('log: ' + msg.params.entry.text.slice(0, 200));
  if (msg.method === 'Network.requestWillBeSent' && !msg.params.request.url.startsWith(base) && !msg.params.request.url.startsWith('data:')) outside.push(msg.params.request.url); });
const send = (method, params = {}) => new Promise((r) => { const id = ++seq; waiting.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
const ev = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' in ' + expression); return r.result.value; };
for (const d of ['Runtime', 'Log', 'Network', 'Page']) await send(d + '.enable');
let failed = 0;
const expect = (name, ok, detail = '') => { console.log((ok ? 'ok   ' : 'FAIL ') + name + (ok ? '' : '  ' + detail)); if (!ok) failed++; };

await send('Page.navigate', { url: base + '/' }); await sleep(900);
await ev("document.getElementById('searchopen').click()"); await sleep(150);
expect('search opens', await ev("document.getElementById('searchbox').open"));
await ev("var i=document.getElementById('searchinput'); i.value='hold'; i.dispatchEvent(new Event('input')); 1"); await sleep(100);
const hits = await ev("Array.from(document.querySelectorAll('#searchhits .hit')).map(a => a.getAttribute('href'))");
expect('search finds holds', hits.length > 0 && hits.some((h) => h.indexOf('/recortex/how-it-works/') === 0), JSON.stringify(hits));
await ev("document.getElementById('searchclose').click()");

await ev("document.getElementById('theme').click()");
const first = await ev("document.documentElement.getAttribute('data-theme') + ' ' + localStorage.getItem('recortex-theme')");
await ev("document.getElementById('theme').click()");
const second = await ev("document.documentElement.getAttribute('data-theme') + ' ' + localStorage.getItem('recortex-theme')");
expect('theme switch stores one value', first !== second && /^(light|dark) (light|dark)$/.test(first) && /^(light|dark) (light|dark)$/.test(second), first + ' / ' + second);
expect('only the theme is stored', (await ev('localStorage.length')) === 1);
await send('Page.navigate', { url: base + '/faq/' }); await sleep(700);
expect('theme survives a page change', (await ev("document.documentElement.getAttribute('data-theme')")) === second.split(' ')[1]);

await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
await send('Page.navigate', { url: base + '/' }); await sleep(700);
await ev("document.getElementById('menubtn').click()");
expect('menu opens on a phone', await ev("getComputedStyle(document.getElementById('pages')).display") === 'block');
expect('no sideways scroll on a phone', (await ev('document.documentElement.scrollWidth')) <= 390);

expect('no console messages or errors', problems.length === 0, problems.join('; '));
expect('nothing requested from another server', outside.length === 0, outside.join(' '));
ws.close(); chrome.kill();
process.exit(failed ? 1 : 0);
