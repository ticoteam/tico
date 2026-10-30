#!/usr/bin/env node
// The browser harness's probe, run inside one bot turn by e2e/browser/harness/fake_cli.py: node probe.mjs <dump> <phase> <results>.
// It starts each MCP server the harness's config named (chrome-devtools, hub) with the command and environment written
// there, and talks to Chrome through TICO_BROWSER_URL the way a bot would. Node 22, no packages. It writes one JSON result
// with a check per line and never prints a value it typed: a login's password, its 2FA secret and codes never reach here.
import {spawn, execFileSync} from 'node:child_process';
import {readFileSync, writeFileSync} from 'node:fs';

const [dumpPath, phase = 'probe', resultsPath] = process.argv.slice(2);
const env = process.env;
const dump = JSON.parse(readFileSync(dumpPath, 'utf8'));
const servers = dump.servers || {};
const base = (env.TICO_BROWSER_URL || '').replace(/\/$/, '');
const practice = env.PRACTICE || '';
const host = env.HOST || 'login.practice.test';
// The practice site is plain http, which a login fills only when its site is saved as http://host.
const site = (env.PRACTICE || '').startsWith('http://') ? 'http://' + host : host;
const label = env.LABEL || 'Harness-login';
const checks = [];
const data = {};
const sleep = ms => new Promise(done => setTimeout(done, ms));

function check(name, ok, detail = '') {
  checks.push({name, ok: Boolean(ok), detail: String(detail).slice(0, 240)});
  return Boolean(ok);
}
// The URL's secret is the one thing a page or a log must never see: it is shown as <secret> everywhere.
const shown = text => String(text).replace(/\/[0-9a-f]{16,}/g, '/<secret>');
const expand = text => String(text).replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, key) => env[key] ?? '');
const serverEnv = server => {
  const out = {};
  for (const [key, value] of Object.entries(server.env || {})) out[key] = expand(value);
  return {...env, ...out};
};

// The hub CLI's own JSON, whatever its exit code (a refusal prints its reason on stdout).
function hub(args) {
  try {
    return {ok: true, json: JSON.parse(execFileSync('hub', args, {env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 150000}))};
  } catch (error) {
    let json = null;
    try { json = JSON.parse(String(error.stdout || '')); } catch { /* not JSON: the exit status is all there is */ }
    return {ok: false, json, err: String(error.stderr || '').trim().split('\n').slice(-2).join(' ').slice(0, 200)};
  }
}

// One MCP server over stdio, as a host starts it: the command, arguments and environment from the config.
async function openMcp(server) {
  const child = spawn(server.command, server.args || [], {env: serverEnv(server), stdio: ['pipe', 'pipe', 'pipe']});
  let buffer = '';
  let nextId = 1;
  const waiting = new Map();
  child.stderr.on('data', () => {});
  child.stdout.on('data', chunk => {
    buffer += chunk;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i);
      buffer = buffer.slice(i + 1);
      try {
        const msg = JSON.parse(line);
        waiting.get(msg.id)?.(msg);
        waiting.delete(msg.id);
      } catch { /* a line that is not JSON-RPC */ }
    }
  });
  child.on('error', () => {});
  const rpc = (method, params, ms = 120000) => {
    const id = nextId++;
    child.stdin.write(JSON.stringify({jsonrpc: '2.0', id, method, params}) + '\n');
    return Promise.race([
      new Promise(resolve => waiting.set(id, resolve)),
      sleep(ms).then(() => ({error: {message: method + ' timed out'}})),
    ]);
  };
  const init = await rpc('initialize', {protocolVersion: '2025-06-18', capabilities: {}, clientInfo: {name: 'tico-harness-probe', version: '1'}});
  child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
  const listed = await rpc('tools/list', {});
  const names = (listed.result?.tools || []).map(tool => tool.name);
  const call = async (name, args = {}) => {
    const reply = await rpc('tools/call', {name, arguments: args});
    return {error: reply.error, isError: Boolean(reply.result?.isError), text: (reply.result?.content || []).map(c => c.text || '').join('\n')};
  };
  return {init, names, call, close: () => child.kill('SIGTERM')};
}

// The run's Chrome over its DevTools socket: the endpoint's json/version proxies the websocket.
async function openCdp() {
  let version = null;
  const end = Date.now() + 300000;
  while (!version && Date.now() < end) {
    const reply = await fetch(base + '/json/version').catch(() => null);
    if (reply && reply.ok) version = await reply.json();
    else await sleep(3000);
  }
  if (!version) return null;
  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((ok, fail) => { ws.onopen = ok; ws.onerror = () => fail(new Error('no DevTools socket')); });
  let nextId = 1;
  const pending = new Map();
  ws.onmessage = event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  };
  const send = (method, params = {}, sessionId) => new Promise(resolve => {
    const id = nextId++;
    pending.set(id, resolve);
    ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
  });
  return {send, close: () => ws.close()};
}

// The practice tab's value check, read in the page: whether a field holds text, never the text itself.
async function fieldFilled(cdp, name) {
  // The bot's Chrome may hold several tabs on the practice host (earlier rows leave theirs open); the fill may have
  // typed into any of them, so the field counts as filled when one page holds it.
  const {result: {targetInfos}} = await cdp.send('Target.getTargets');
  for (const target of targetInfos.filter(info => info.type === 'page' && info.url.includes(host))) {
    const {result: {sessionId}} = await cdp.send('Target.attachToTarget', {targetId: target.targetId, flatten: true});
    const {result} = await cdp.send('Runtime.evaluate', {
      expression: `!!(document.querySelector('input[name=${name}]') || {}).value`, returnByValue: true}, sessionId);
    if (result?.result?.value === true) return true;
  }
  return false;
}

async function main() {
  check('TICO_BROWSER_URL is set for this run', /^http:\/\/127\.0\.0\.1:\d+\/[^/]+$/.test(base), shown(base));
  const cdp = base ? await openCdp() : null;
  check('the endpoint answers json/version with a DevTools socket', cdp !== null, shown(base));

  if (phase === 'status') {
    // Chrome for a bot starts on its first lease, so the status may be asked before it is up: wait for it, up to a minute.
    let status = hub(['browser', 'status']);
    for (let tries = 0; tries < 30 && status.json?.running !== true; tries++) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      status = hub(['browser', 'status']);
    }
    check('hub browser status (CLI) reports Chrome running', status.json?.running === true, status.json?.problem || status.err);
    cdp?.close();
    return;
  }

  if (phase === 'request') {
    const hubServer = servers.hub;
    check('the hub MCP server is in the written config', Boolean(hubServer));
    if (hubServer) {
      const hubMcp = await openMcp(hubServer);
      const card = await hubMcp.call('hub_credential_request', {kind: 'login', label, sites: [site]});
      let parsed = {};
      try { parsed = JSON.parse(card.text); } catch { /* the reply is text: no card id to report */ }
      data.card = parsed.id || parsed.request_id || parsed.card?.id || '';
      check('hub_credential_request opens a login card for the site', Boolean(data.card) && !card.isError, card.text.slice(0, 200));
      hubMcp.close();
    }
    cdp?.close();
    return;
  }

  // The full probe: the endpoint, a page opened on the practice site, each MCP server the config names, then the CLI.
  if (cdp) {
    const {result} = await cdp.send('Target.createTarget', {url: practice + '/login'});
    check('a tab opens on the practice login page', Boolean(result?.targetId));
    await sleep(2000);
  }
  const devtools = servers['chrome-devtools'];
  const hubServer = servers.hub;
  data.devtools = devtools ? 'configured' : 'not configured for this harness';
  data.hub_mcp = hubServer ? 'configured' : 'not configured for this harness';

  if (devtools) {
    const script = (devtools.args || []).join(' ');
    check('chrome-devtools-mcp is pointed at TICO_BROWSER_URL (--browserUrl)',
      script.includes('--browserUrl') && serverEnv(devtools).TICO_BROWSER_URL === base, shown(devtools.command));
    const dt = await openMcp(devtools);
    const need = ['list_pages', 'navigate_page', 'take_snapshot'];
    check('chrome-devtools-mcp starts from the written config and lists its page tools',
      !dt.init.error && need.every(name => dt.names.includes(name)), need.filter(name => !dt.names.includes(name)).join(', ') || dt.names.length);
    const pages = await dt.call('list_pages');
    check('list_pages sees the bot\'s Chrome tabs', !pages.error && !pages.isError && pages.text.length > 0, pages.text.slice(0, 160));
    // list_pages numbers each tab ("1: https://..."); the page tools take that number as pageId.
    const numbered = /(?:^|\n)\s*(\d+):/.exec(pages.text || '');
    const pageId = numbered ? Number(numbered[1]) : null;
    check('list_pages numbers the tabs (pageId for the page tools)', pageId !== null, (pages.text || '').slice(0, 160));
    const nav = await dt.call('navigate_page', {pageId, type: 'url', url: practice + '/login'});
    check('navigate_page opens the practice login page', !nav.error && !nav.isError, nav.text.slice(0, 160) || nav.error?.message);
    const snap = await dt.call('take_snapshot', {pageId});
    check('take_snapshot reads the practice login page', /Log in/.test(snap.text) && /[Pp]assword/.test(snap.text), snap.text.slice(0, 160) || snap.error?.message);
    dt.close();
  }

  if (hubServer) {
    const hubMcp = await openMcp(hubServer);
    const need = ['hub_browser_fill', 'hub_browser_status', 'hub_browser_needs_you'];
    check('the hub MCP server lists the browser tools', !hubMcp.init.error && need.every(name => hubMcp.names.includes(name)),
      need.filter(name => !hubMcp.names.includes(name)).join(', ') || hubMcp.names.length);
    const status = await hubMcp.call('hub_browser_status');
    let parsed = {};
    try { parsed = JSON.parse(status.text); } catch { /* not JSON */ }
    check('hub_browser_status over MCP reports Chrome running', parsed.running === true, status.text.slice(0, 160));
    for (const field of ['username', 'password']) {
      const filled = await hubMcp.call('hub_browser_fill', {credential: label, field, selector: `input[name=${field}]`});
      let reply = {};
      try { reply = JSON.parse(filled.text); } catch { /* not JSON */ }
      check(`hub_browser_fill over MCP types the ${field} into the practice form`, reply.ok === true && !filled.isError, reply.error || filled.text.slice(0, 160));
    }
    if (cdp) {
      check('the MCP fill reached the page (username and password fields hold text)',
        await fieldFilled(cdp, 'username') && await fieldFilled(cdp, 'password'));
    }
    hubMcp.close();
  }

  // The same fills through the hub CLI, which every harness reaches over bash.
  const status = hub(['browser', 'status']);
  check('hub browser status (CLI) reports Chrome running', status.json?.running === true, status.json?.problem || status.err);
  for (const field of ['username', 'password']) {
    const filled = hub(['browser', 'fill', label, field, '--selector', `input[name=${field}]`]);
    check(`hub browser fill (CLI) types the ${field}`, filled.json?.ok === true, filled.json?.error || filled.err);
  }
  cdp?.close();
}

try {
  await main();
} catch (error) {
  check('the probe ran to the end', false, String(error && error.message || error));
}
data.checks = checks.length;
writeFileSync(resultsPath, JSON.stringify({phase, ok: checks.every(item => item.ok), checks, data}));
process.exit(0);
