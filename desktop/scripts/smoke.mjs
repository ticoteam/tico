// Smoke run: starts the built app against a tiny local page standing in for a company's /next, and checks that the
// window opens, the page gets `window.tico` and no Node, and a frame from another origin gets no bridge. It quits on
// its own within 30 seconds whatever happens. Run after `npm run build` (npm run smoke does both).
import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { createServer } from 'node:http';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const electron = createRequire(import.meta.url)('electron');
let child = null;

function finish(ok, message) {
  console.log(`${ok ? 'smoke ok' : 'smoke FAILED'}: ${message}`);
  if (child && child.exitCode === null) child.kill('SIGKILL');
  server.close();
  process.exit(ok ? 0 : 1);
}
setTimeout(() => finish(false, 'no report within 25 seconds'), 25_000).unref();

// The stand-in /next page: reports what it can see, and what a frame from another origin (localhost, not
// 127.0.0.1) can see, then posts both back here.
const page = `<!doctype html><meta charset="utf-8"><title>stand-in</title><body>
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
    report.companies = list.map((c) => ({ origin: c.origin, active: c.active, self: c.self }));
  } catch (e) { report.listError = String(e); }
  try { await t.callWindow.open('../../etc'); report.badCallId = 'accepted'; } catch { report.badCallId = 'refused'; }
  report.frame = await frame;
  await fetch('/report', { method: 'POST', body: JSON.stringify(report) });
})();
</script>`;
const framePage = `<!doctype html><script>parent.postMessage({ bridge: typeof window.tico }, '*');</script>`;

const server = createServer((req, res) => {
  const port = server.address().port;
  if (req.url === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' }).end('{"service":"tico"}');
  } else if (req.url === '/next/' || req.url === '/next') {
    res.writeHead(200, { 'content-type': 'text/html' }).end(page.replaceAll('PORT', String(port)));
  } else if (req.url === '/frame') {
    res.writeHead(200, { 'content-type': 'text/html' }).end(framePage);
  } else if (req.url === '/report' && req.method === 'POST') {
    let body = '';
    req.on('data', (d) => (body += d));
    req.on('end', () => {
      res.writeHead(204).end();
      check(JSON.parse(body), port);
    });
  } else {
    res.writeHead(404).end();
  }
});

function check(r, port) {
  const problems = [];
  if (r.bridge !== 'object') problems.push('window.tico missing');
  const want = ['app', 'callWindow', 'companies', 'platform', 'setBadge', 'setFilesOrigin', 'version'];
  if (JSON.stringify(r.keys) !== JSON.stringify(want)) problems.push(`bridge keys ${r.keys}`);
  if (r.require !== 'undefined' || r.process !== 'undefined') problems.push('Node reached the page');
  if (!r.companies || r.companies.length !== 1 || r.companies[0].origin !== `http://127.0.0.1:${port}` || !r.companies[0].self) {
    problems.push(`companies.list ${JSON.stringify(r.companies ?? r.listError)}`);
  }
  if (r.badCallId !== 'refused') problems.push('a bad call id was accepted');
  if (!r.frame || r.frame.bridge !== 'undefined') problems.push(`a frame from another origin saw ${JSON.stringify(r.frame)}`);
  if (/Electron\//.test(r.ua) || !/TicoDesktop\/3/.test(r.ua)) problems.push(`user agent ${r.ua}`);
  if (!(r.width > 0)) problems.push('no window width');
  if (r.desktop !== r.platform) problems.push(`<html data-desktop> was ${r.desktop} when the page's first script ran`);
  finish(!problems.length, problems.length ? problems.join('; ') : `bridge ${r.version} on ${r.platform}, html data-desktop=${r.desktop}, inset ${r.insetLeft}, ${r.width}px wide`);
}

server.listen(0, '127.0.0.1', () => {
  const { port } = server.address();
  child = spawn(electron, ['.'], {
    cwd: root,
    stdio: ['ignore', 'inherit', 'inherit'],
    env: {
      ...process.env,
      TICO_SERVER: `http://127.0.0.1:${port}`,
      TICO_USER_DATA: mkdtempSync(join(tmpdir(), 'tico-desktop-smoke-')),
      TICO_NO_UPDATES: '1',
    },
  });
  child.on('exit', (code) => finish(false, `the app quit early (code ${code})`));
});
