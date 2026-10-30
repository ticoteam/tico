// The client half of the browser live check (src/browser/live.rs): Playwright, chrome-devtools-mcp and /fill through
// TICO_BROWSER_URL. Prints one line per check and exits 1 when any fails.
import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import http from 'node:http';
import { chromium } from 'playwright-core';

const url = process.env.TICO_BROWSER_URL;
const failures = [];
function check(ok, what, detail) {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}${detail === undefined ? '' : ' ' + JSON.stringify(detail)}`);
  if (!ok) failures.push(what);
}

// Plain http.request: fetch may add an Origin header, which the endpoint refuses on purpose.
function call(base, method, path, body, headers = {}, chunked = false) {
  const target = new URL(base + path);
  const data = body === undefined ? null : JSON.stringify(body);
  return new Promise((resolve) => {
    const sized = data && !chunked ? { 'content-length': Buffer.byteLength(data) } : {};
    const req = http.request(target, { method, headers: { ...(data ? { 'content-type': 'application/json' } : {}), ...sized, ...headers } }, (res) => {
      let text = '';
      res.on('data', (c) => (text += c));
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(text); } catch {}
        resolve({ status: res.statusCode, json });
      });
    });
    req.on('error', (e) => resolve({ status: 0, json: { error: String(e) } }));
    req.setTimeout(90000, () => req.destroy(new Error('timeout')));
    if (data) req.write(data);
    req.end();
  });
}

function totp(secret, at = Date.now() / 1000, period = 30, digits = 6) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const ch of secret) bits += alphabet.indexOf(ch).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(at / period)));
  const mac = crypto.createHmac('sha1', key).update(counter).digest();
  const offset = mac[mac.length - 1] & 15;
  return String((mac.readUInt32BE(offset) & 0x7fffffff) % 10 ** digits).padStart(digits, '0');
}

const site = http.createServer((req, res) => {
  res.setHeader('content-type', 'text/html');
  // #trap hands focus to #email as it gets it: a fill must notice and type nowhere.
  res.end('<!doctype html><title>Acme sign in</title><form><input id=email><input id=pw type=password><input id=code>'
    + '<input id=trap onfocus="email.focus()"></form>');
});
const lan = process.env.TICO_LIVE_LAN || '';
await new Promise((r) => site.listen(0, lan ? '0.0.0.0' : '127.0.0.1', r));
const port = site.address().port;
const endpointPort = new URL(url).port;

// The endpoint itself.
const status = await call(url, 'GET', '/status');
check(status.status === 200 && status.json?.running === true && /^\d+\./.test(status.json?.version || ''), 'status: running with a version', status.json);
const fromPage = await call(url, 'GET', '/json/version', undefined, { Origin: 'https://evil.example' });
check(fromPage.status === 403, 'a request with an Origin is refused', fromPage.status);
const rebound = await call(url, 'GET', '/json/version', undefined, { Host: `evil.example:${endpointPort}` });
check(rebound.status === 403, 'a request for another Host is refused', rebound.status);
const wrongSecret = await call(url.replace(/[0-9a-f]{8}$/, '00000000'), 'GET', '/status');
check(wrongSecret.status !== 200, 'a wrong secret is refused', wrongSecret.status);
const version = await call(url, 'GET', '/json/version');
check(String(version.json?.webSocketDebuggerUrl || '').startsWith(`ws://127.0.0.1:${endpointPort}/`), 'json/version proxies the websocket', version.json?.webSocketDebuggerUrl?.replace(/[0-9a-f]{64}/, '<secret>'));

// Playwright over CDP.
const browser = await chromium.connectOverCDP(url, { timeout: 60000 });
const context = browser.contexts()[0];
check(!!context, 'Playwright sees the default context (the bot profile)');
const page = await context.newPage();
await page.goto(`http://127.0.0.1:${port}/login`);
const webdriver = await page.evaluate(() => navigator.webdriver);
check(webdriver === false, 'navigator.webdriver is false', webdriver);
const agent = await page.evaluate(() => navigator.userAgent);
check(!/Headless/i.test(agent), 'the user agent is not headless', agent);
const session = await context.newCDPSession(page);
const { targetInfo } = await session.send('Target.getTargetInfo');
const target = targetInfo.targetId;

// Fill.
const fill = (body, base = url, chunked = false) => call(base, 'POST', '/fill', body, {}, chunked);
let r = await fill({ credential: 'Acme', field: 'username', selector: '#email', target_id: target }, url, true);
check(r.json?.ok === true, 'fill username (a chunked body)', r.json);
r = await fill({ credential: 'c1', field: 'password', selector: '#pw', target_id: target });
check(r.json?.ok === true && !JSON.stringify(r.json).includes('correct horse'), 'fill password by id, no value returned', r.json);
r = await fill({ credential: 'acme', field: 'totp', selector: '#code', target_id: target });
check(r.json?.ok === true, 'fill totp', r.json);
const values = await page.evaluate(() => [email.value, pw.value, code.value]);
const now = Date.now() / 1000;
check(values[0] === 'ann@acme.example' && values[1] === 'correct horse 1', 'username and password are in the page');
check([totp('JBSWY3DPEHPK3PXP', now), totp('JBSWY3DPEHPK3PXP', now - 30)].includes(values[2]), 'the current TOTP code is in the page', values[2]);

await page.evaluate(() => { email.value = ''; });
await page.bringToFront();
await page.focus('#email');
r = await fill({ credential: 'Acme', field: 'username' });
check(r.json?.ok === true && (await page.inputValue('#email')) === 'ann@acme.example', 'fill the focused field of the focused tab', r.json);

await page.evaluate(() => { email.value = ''; });
r = await fill({ credential: 'Acme', field: 'password', selector: '#trap', target_id: target });
check(r.json?.ok === false && r.json?.error === 'focus_changed', 'focus moved by the page is refused', r.json);
check((await page.evaluate(() => email.value + trap.value)) === '', 'nothing typed where focus went', null);

r = await fill({ credential: 'Other', field: 'password', target_id: target });
check(r.json?.ok === false && r.json?.error === 'not_granted', 'an ungranted login is refused', r.json);
r = await fill({ credential: 'Beta', field: 'totp', selector: '#code', target_id: target });
check(r.json?.ok === false && r.json?.error === 'no_totp', 'a login without 2FA has no totp', r.json);
const other = await context.newPage();
await other.goto(`http://localhost:${port}/login`);
const otherTarget = (await (await context.newCDPSession(other)).send('Target.getTargetInfo')).targetInfo.targetId;
r = await fill({ credential: 'Acme', field: 'password', selector: '#pw', target_id: otherTarget });
check(r.json?.ok === false && r.json?.error === 'site_mismatch' && (await other.inputValue('#pw')) === '', 'another host is refused', r.json);
if (lan) {
  // Plain http off this machine: only a login whose site is written http:// fills it.
  await other.goto(`http://${lan}:${port}/login`);
  r = await fill({ credential: 'Network', field: 'password', selector: '#pw', target_id: otherTarget });
  check(r.json?.ok === false && r.json?.error === 'insecure_page' && (await other.inputValue('#pw')) === '', 'a plain-http page is refused', r.json);
  r = await fill({ credential: 'Network http', field: 'password', selector: '#pw', target_id: otherTarget });
  check(r.json?.ok === true && (await other.inputValue('#pw')) === 'plain text 4', 'a login written http:// fills it', r.json);
} else {
  check(true, 'no network address here: the plain-http checks are left out');
}
r = await fill({ credential: 'Acme', field: 'password', selector: '#pw', target_id: target }, process.env.TICO_BROWSER_URL_STALE);
check(r.json?.ok === false && r.json?.error === 'no_run', 'an ended attempt cannot fill', r.json);
const dropped = await call(process.env.TICO_BROWSER_URL_DROPPED, 'GET', '/status');
check(dropped.status !== 200, "a finished run's address is dead", dropped.status);

// A client's Browser.close ends its own connection, not the bot's Chrome.
await other.close();
await browser.close();
const still = await call(url, 'GET', '/status');
check(still.json?.running === true, 'Chrome keeps running after Playwright closes', still.json);

// chrome-devtools-mcp over stdio.
const mcp = JSON.parse(process.env.TICO_BROWSER_MCP);
const child = spawn(mcp.command, mcp.args, { env: { ...process.env, ...mcp.env }, stdio: ['pipe', 'pipe', 'inherit'] });
let buffer = '';
const waiting = new Map();
child.stdout.on('data', (chunk) => {
  buffer += chunk;
  let i;
  while ((i = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, i);
    buffer = buffer.slice(i + 1);
    try {
      const msg = JSON.parse(line);
      waiting.get(msg.id)?.(msg);
    } catch {}
  }
});
let nextId = 1;
function rpc(method, params) {
  const id = nextId++;
  child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n');
  return Promise.race([
    new Promise((resolve) => waiting.set(id, resolve)),
    new Promise((resolve) => setTimeout(() => resolve({ error: { message: `${method} timed out` } }), 90000)),
  ]);
}
const init = await rpc('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'tico-live-check', version: '1' } });
check(!!init.result, 'chrome-devtools-mcp initializes', init.error);
child.stdin.write(JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n');
const tools = await rpc('tools/list', {});
const names = (tools.result?.tools || []).map((t) => t.name);
check(names.includes('list_pages') && names.includes('evaluate_script'), 'chrome-devtools-mcp lists its tools', names.length);
const text = (reply) => (reply.result?.content || []).map((c) => c.text || '').join('\n');
const pages = await rpc('tools/call', { name: 'list_pages', arguments: {} });
check(text(pages).includes(`127.0.0.1:${port}`), 'chrome-devtools-mcp sees the bot\'s tabs through the endpoint', text(pages).slice(0, 300) || pages.error);
const select = /(\d+): http:\/\/127\.0\.0\.1:\d+\/login/.exec(text(pages));
const pageId = select ? Number(select[1]) : 1;
await rpc('tools/call', { name: 'select_page', arguments: { pageId } });
const evaluated = await rpc('tools/call', { name: 'evaluate_script', arguments: { pageId, function: '() => String(navigator.webdriver)' } });
check(/false/.test(text(evaluated)) && !/true/.test(text(evaluated)), 'navigator.webdriver is false through chrome-devtools-mcp', text(evaluated).slice(0, 300) || evaluated.error);
child.kill('SIGTERM');

const last = await call(url, 'GET', '/status');
check(last.json?.running === true, 'Chrome keeps running after chrome-devtools-mcp exits', last.json);
site.close();
console.log(failures.length ? `${failures.length} check(s) failed` : 'all checks passed');
process.exit(failures.length ? 1 : 0);
