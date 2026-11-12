#!/usr/bin/env node
// A screenshot of the app as a bot sees it, read only (backend/app_view.py; `tico bot app-shot`, clients/commands/bots.py).
// Starts its own headless Chrome with an empty profile, sends the view's token only to the app's own origin, opens the
// address at the width asked for and writes a PNG. No dependencies: Chrome's DevTools protocol over Node's WebSocket.
//
//   TICO_VIEW_TOKEN=tico_av_... node clients/app_shot.mjs <url> <out.png> [width] [height] [wait ms] [full]
//
// The token comes in the environment, never on the command line, so it is not in the computer's process list. Chrome is
// TICO_CHROME, else the first of the usual places that exists.
import {spawn} from 'node:child_process';
import {existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';

const [url, out, width = '390', height = '', wait = '2500', full = ''] = process.argv.slice(2);
const token = process.env.TICO_VIEW_TOKEN || '';
const fail = (message) => { process.stderr.write('app shot: ' + message + '\n'); process.exit(1); };
if (!url || !out) fail('usage: app_shot.mjs <url> <out.png> [width] [height] [wait ms] [full]');
if (!token.startsWith('tico_av_')) fail('TICO_VIEW_TOKEN is not a view token');
const origin = new URL(url).origin;
const w = Number(width), h = Number(height) || (w < 600 ? 844 : 800), mobile = w < 600;

const CHROMES = [process.env.TICO_CHROME, 'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
const chrome = CHROMES.find((c) => c.includes('/') ? existsSync(c)
  : (process.env.PATH || '').split(':').some((d) => existsSync(join(d, c))));
if (!chrome) fail('no Chrome found; set TICO_CHROME');

const profile = mkdtempSync(join(tmpdir(), 'tico-app-shot-'));
const browser = spawn(chrome, ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile,
  '--no-first-run', '--no-default-browser-check', '--hide-scrollbars', '--mute-audio', 'about:blank'],
{stdio: 'ignore'});
const done = (code) => { try { browser.kill(); } catch {} try { rmSync(profile, {recursive: true, force: true}); } catch {} process.exit(code); };
const timer = setTimeout(() => { process.stderr.write('app shot: timed out\n'); done(1); }, 60_000 + Number(wait));

async function endpoint() {
  for (let i = 0; i < 200; i++) {
    const file = join(profile, 'DevToolsActivePort');
    if (existsSync(file)) {
      const [port, path] = readFileSync(file, 'utf8').split('\n');
      if (port && path) return `ws://127.0.0.1:${port}${path}`;
    }
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error('Chrome did not start');
}

try {
  const ws = new WebSocket(await endpoint());
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let next = 0;
  const pending = new Map(), listeners = [];
  ws.onmessage = (event) => {
    const m = JSON.parse(String(event.data));
    if (m.id && pending.has(m.id)) {
      const {resolve, reject} = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(m.error.message)) : resolve(m.result);
    } else if (m.method) for (const l of listeners) l(m);
  };
  const send = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
  const {targetId} = await send('Target.createTarget', {url: 'about:blank'});
  const {sessionId} = await send('Target.attachToTarget', {targetId, flatten: true});
  const s = (method, params) => send(method, params, sessionId);
  // Only the app's own requests carry the token: a picture, font or link elsewhere never sees it.
  listeners.push((m) => {
    if (m.sessionId !== sessionId || m.method !== 'Fetch.requestPaused') return;
    const {requestId, request} = m.params;
    const ours = new URL(request.url).origin === origin;
    const headers = Object.entries(request.headers).filter(([k]) => k.toLowerCase() !== 'authorization')
      .map(([name, value]) => ({name, value: String(value)}));
    if (ours) headers.push({name: 'Authorization', value: 'Bearer ' + token});
    s('Fetch.continueRequest', ours ? {requestId, headers} : {requestId}).catch(() => {});
  });
  await s('Fetch.enable', {patterns: [{urlPattern: origin + '/*'}]});
  await s('Emulation.setDeviceMetricsOverride', {width: w, height: h, deviceScaleFactor: mobile ? 2 : 1, mobile});
  await s('Emulation.setTouchEmulationEnabled', {enabled: mobile});
  await s('Page.enable');
  const loaded = new Promise((resolve) => listeners.push((m) => m.sessionId === sessionId && m.method === 'Page.loadEventFired' && resolve()));
  await s('Page.navigate', {url});
  await loaded;
  await new Promise((r) => setTimeout(r, Number(wait)));  // the page reads its data after it loads
  const shot = await s('Page.captureScreenshot', {format: 'png', captureBeyondViewport: !!full});
  writeFileSync(out, Buffer.from(shot.data, 'base64'));
  clearTimeout(timer);
  process.stdout.write(JSON.stringify({file: out, url, width: w, height: h}) + '\n');
  ws.close();
  done(0);
} catch (err) {
  process.stderr.write('app shot: ' + (err && err.message || err) + '\n');
  done(1);
}
