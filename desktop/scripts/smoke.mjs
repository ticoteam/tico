// Smoke run (platforms.md 8, the Electron row): starts the built app against two tiny local servers standing in for
// two companies' /next, and checks that
// - the window opens and company A's page gets `window.tico` and no Node, and a frame from another origin gets no bridge;
// - the bridge refuses a bad argument;
// - the companies are sealed apart: a cookie A's server sets is never sent to B (cookies ignore ports, so only the
//   per-company session keeps it out);
// - the small call window opens on A's call route;
// - a tico://open link handed to a second launch switches the running app to B at that page (single instance).
// It quits on its own within 40 seconds whatever happens. Run after `npm run build` (npm run smoke does both).
import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const electron = createRequire(import.meta.url)('electron');
const children = [];
const seen = { report: null, bCookie: null, call: false, deep: false };
const passed = [];
let releaseB = null;

function finish(ok, message) {
  console.log(`${ok ? 'smoke ok' : 'smoke FAILED'}: ${message}`);
  for (const c of children) if (c.exitCode === null) c.kill('SIGKILL');
  a.close();
  b.close();
  process.exit(ok ? 0 : 1);
}
setTimeout(() => finish(false, `stopped after 40 seconds; passed so far: ${passed.join(', ') || 'nothing'}`), 40_000).unref();

// Company A's stand-in /next page: reports what it can see, and what a frame from another origin (localhost, not
// 127.0.0.1) can see, then posts both back here.
const pageA = `<!doctype html><meta charset="utf-8"><title>A</title><body>
<iframe id="f" src="http://localhost:PORT/frame"></iframe>
<script>
const frame = new Promise((resolve) => {
  addEventListener('message', (e) => { if (e.origin === 'http://localhost:PORT') resolve(e.data); });
  setTimeout(() => resolve('no answer'), 5000);
});
(async () => {
  const t = window.tico;
  const report = {
    bridge: typeof t,
    keys: t ? Object.keys(t).sort() : [],
    version: t && t.version,
    platform: t && t.platform,
    require: typeof require,
    process: typeof process,
    desktop: document.documentElement.dataset.desktop || null,
    insetLeft: getComputedStyle(document.documentElement).getPropertyValue('--desktop-inset-left'),
    width: innerWidth,
    ua: navigator.userAgent,
  };
  try {
    const list = await t.companies.list();
    report.companies = list.map((c) => ({ id: c.id, origin: c.origin, active: c.active, self: c.self }));
  } catch (e) { report.listError = String(e); }
  try { await t.callWindow.open('../../etc'); report.badCallId = 'accepted'; } catch { report.badCallId = 'refused'; }
  report.frame = await frame;
  await fetch('/report', { method: 'POST', body: JSON.stringify(report) });
  await t.callWindow.open('smoke-call');
})();
</script>`;
const framePage = `<!doctype html><script>parent.postMessage({ bridge: typeof window.tico }, '*');</script>`;
// Company B's stand-in page: once A has its cookie, asks B's server what cookies came with a request.
const pageB = `<!doctype html><meta charset="utf-8"><title>B</title><script>
fetch('/wait').then(() => fetch('/cookie'));
</script>`;

const html = (res, body) => res.writeHead(200, { 'content-type': 'text/html' }).end(body);

const a = createServer((req, res) => {
  const port = a.address().port;
  const path = req.url.split('?')[0];
  if (path === '/next/' || path === '/next') {
    res.setHeader('set-cookie', 'tico_smoke=a; Path=/; HttpOnly');
    html(res, pageA.replaceAll('PORT', String(port)));
  } else if (path === '/frame') {
    html(res, framePage);
  } else if (path === '/next/call/smoke-call' && new URL(req.url, 'http://x').searchParams.get('window') === '1') {
    html(res, '<!doctype html><title>call</title>');
    seen.call = true;
    step();
  } else if (path === '/report' && req.method === 'POST') {
    let body = '';
    req.on('data', (d) => (body += d));
    req.on('end', () => {
      res.writeHead(204).end();
      seen.report = JSON.parse(body);
      releaseB?.();
      step();
    });
  } else {
    res.writeHead(404).end();
  }
});

const b = createServer((req, res) => {
  const path = req.url.split('?')[0];
  if (path === '/next/' || path === '/next') {
    html(res, pageB);
  } else if (path === '/wait') {
    // answered once A's page has reported, so A's cookie is set by then
    const go = () => res.writeHead(204).end();
    if (seen.report) go();
    else releaseB = go;
  } else if (path === '/cookie') {
    seen.bCookie = req.headers.cookie ?? '';
    res.writeHead(204).end();
    step();
  } else if (path === '/next/deep') {
    html(res, '<!doctype html><title>deep</title>');
    seen.deep = true;
    step();
  } else {
    res.writeHead(404).end();
  }
});

const origin = (server) => `http://127.0.0.1:${server.address().port}`;
let deepSent = false;

function checkReport(r) {
  const problems = [];
  if (r.bridge !== 'object') problems.push('window.tico missing');
  const want = ['app', 'callWindow', 'companies', 'platform', 'reportCall', 'setBadge', 'setFilesOrigin', 'signingOut', 'version'];
  if (JSON.stringify(r.keys) !== JSON.stringify(want)) problems.push(`bridge keys ${r.keys}`);
  if (r.require !== 'undefined' || r.process !== 'undefined') problems.push('Node reached the page');
  const list = r.companies ?? [];
  const me = list.find((c) => c.self);
  if (list.length !== 2 || !me || me.origin !== origin(a) || !me.active || list.some((c) => !c.self && c.origin !== '')) {
    problems.push(`companies.list ${JSON.stringify(r.companies ?? r.listError)}`);
  }
  if (r.badCallId !== 'refused') problems.push('a bad call id was accepted');
  if (!r.frame || r.frame.bridge !== 'undefined') problems.push(`a frame from another origin saw ${JSON.stringify(r.frame)}`);
  if (/Electron\//.test(r.ua) || !/TicoDesktop\/3/.test(r.ua)) problems.push(`user agent ${r.ua}`);
  if (!(r.width > 0)) problems.push('no window width');
  if (r.desktop !== r.platform) problems.push(`<html data-desktop> was ${r.desktop} when the page's first script ran`);
  return problems;
}

function step() {
  if (seen.report && !passed.includes('bridge')) {
    const problems = checkReport(seen.report);
    if (problems.length) return finish(false, problems.join('; '));
    passed.push('bridge');
  }
  if (seen.bCookie !== null && !passed.includes('isolation')) {
    if (seen.bCookie.includes('tico_smoke')) return finish(false, `company B was sent company A's cookie: ${seen.bCookie}`);
    passed.push('isolation');
  }
  if (seen.call && !passed.includes('call window')) passed.push('call window');
  // The deep link goes last, once the rest is in: a second launch hands it over and quits.
  if (passed.length === 3 && !deepSent) {
    deepSent = true;
    launch(`tico://open?u=${encodeURIComponent(`${origin(b)}/next/deep`)}`).on('exit', (code) => {
      if (code !== 0) finish(false, `the second launch exited with ${code}`);
    });
  }
  if (seen.deep && !passed.includes('deep link')) passed.push('deep link');
  if (passed.length === 4) {
    const r = seen.report;
    finish(true, `${passed.join(', ')}; bridge ${r.version} on ${r.platform}, html data-desktop=${r.desktop}, inset ${r.insetLeft}, ${r.width}px wide`);
  }
}

const userData = mkdtempSync(join(tmpdir(), 'tico-desktop-smoke-'));
const env = { ...process.env, TICO_USER_DATA: userData, TICO_NO_UPDATES: '1' };
function launch(...args) {
  const child = spawn(electron, ['.', ...args], { cwd: root, stdio: ['ignore', 'inherit', 'inherit'], env });
  children.push(child);
  return child;
}

let listening = 0;
const ready = () => {
  if (++listening < 2) return;
  const company = (id, server) => ({ id, url: origin(server), partition: `persist:company-${id}`, name: id, email: '' });
  writeFileSync(join(userData, 'companies.json'), JSON.stringify({ version: 1, active: 'ca', companies: [company('ca', a), company('cb', b)] }));
  launch().on('exit', (code) => finish(false, `the app quit early (code ${code})`));
};
a.listen(0, '127.0.0.1', ready);
b.listen(0, '127.0.0.1', ready);
