#!/usr/bin/env node
// The bot's side of scripts/browser-journey.sh: one phase per turn. The journey's fake harness (e2e/browser/fake-codex.py)
// runs it inside the bot's own turn, so HUB_TOKEN, HUB_API_URL, HUB_BOT and TICO_BROWSER_URL are the runner's, as for a
// real bot. It drives Chrome over the DevTools protocol, types secrets only through `hub browser fill`, and prints one
// JSON result. It never prints a password, a 2FA secret, an authenticator code or an email code.
//
//   node bot-steps.mjs <phase>    with PRACTICE, HOST, CREDENTIAL, LABEL, OTHER in the environment (set by the harness)
//
// Node 22 only: the WebSocket and fetch globals, no packages.
import {execFileSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';

const phase = process.argv[2] || '';
const env = process.env;
const practice = env.PRACTICE || '';                   // e.g. http://login.practice.test:8080
const host = env.HOST || 'login.practice.test';
const other = env.OTHER || '';                         // another page on another host, for the refused fill
const label = env.LABEL || 'Practice-login';
// A login saved from a card is named after the card's label, so the bot fills it by that name.
const credential = label;
const checks = [];
const data = {};
const check = (name, ok, detail = '') => {
  checks.push({name, ok: Boolean(ok), detail: String(detail).slice(0, 200)});
  return Boolean(ok);
};
const sleep = ms => new Promise(done => setTimeout(done, ms));
// Phases that only call the hub, or connect to Chrome themselves once it is up again (after-crash): they run whether or
// not the bot's Chrome answers at their start.
const NO_BROWSER = new Set(['request-card', 'needs-you', 'after-crash']);

// hub: the CLI's own JSON, whatever its exit code (a refusal prints its reason on stdout).
function hub(args) {
  try {
    return {ok: true, json: JSON.parse(execFileSync('hub', args, {env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 150000}))};
  } catch (error) {
    let json = null;
    try { json = JSON.parse(String(error.stdout || '')); } catch { /* not JSON: keep the exit status only */ }
    return {ok: false, json, err: String(error.stderr || '').trim().split('\n').slice(-2).join(' ')};
  }
}
const why = r => (r.json && (r.json.error || r.json.problem)) || r.err || '';

// The DevTools endpoint the runner serves for this bot's Chrome (TICO_BROWSER_URL = http://127.0.0.1:<port>/<secret>).
class Browser {
  async open() {
    const base = (env.TICO_BROWSER_URL || '').replace(/\/$/, '');
    if (!base) throw new Error('TICO_BROWSER_URL is not set in this turn');
    // A first start includes Chrome's install, which can outlast one json/version wait (503 until it is up).
    const end = Date.now() + 300000;
    let version = null;
    for (;;) {
      const reply = await fetch(base + '/json/version');
      version = await reply.json().catch(() => ({}));
      if (reply.ok) break;
      if (reply.status !== 503 || Date.now() > end) throw new Error(`json/version ${reply.status}: ${version.problem || version.error || ''}`);
      await sleep(5000);
    }
    this.ws = new WebSocket(version.webSocketDebuggerUrl);
    await new Promise((ok, fail) => { this.ws.onopen = ok; this.ws.onerror = () => fail(new Error('no DevTools socket')); });
    this.id = 0;
    this.waiting = new Map();
    this.ws.onmessage = event => {
      const message = JSON.parse(event.data);
      const call = this.waiting.get(message.id);
      if (!call) return;
      this.waiting.delete(message.id);
      if (message.error) call.reject(new Error(message.error.message)); else call.resolve(message.result);
    };
    return this;
  }
  send(method, params = {}, sessionId) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      this.waiting.set(id, {resolve, reject});
      this.ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
  async newTab() {
    const {targetId} = await this.send('Target.createTarget', {url: 'about:blank'});
    const {sessionId} = await this.send('Target.attachToTarget', {targetId, flatten: true});
    await this.send('Page.enable', {}, sessionId);
    await this.send('Runtime.enable', {}, sessionId);
    return new Tab(this, sessionId, targetId);
  }
  close() { this.ws.close(); }
}

class Tab {
  constructor(browser, sessionId, targetId) { Object.assign(this, {browser, sessionId, targetId}); }
  async eval(expression) {
    const result = await this.browser.send('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true}, this.sessionId);
    if (result.exceptionDetails) throw new Error('page script failed');
    return result.result.value;
  }
  async waitFor(expression, ms = 20000) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (await this.eval(expression).catch(() => false)) return true;
      await sleep(250);
    }
    return false;
  }
  async go(url) {
    await this.browser.send('Page.navigate', {url}, this.sessionId);
    await sleep(300);
    await this.waitFor('document.readyState === "complete"');
  }
  async type(selector, text) {   // typed as a person would, for what is not a secret (the email address, the code)
    const found = await this.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false; e.focus(); return true; })()`);
    if (!found) return false;
    await this.browser.send('Input.insertText', {text}, this.sessionId);
    return true;
  }
  async click(selector) {
    return this.eval(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); if (!e) return false; e.click(); return true; })()`);
  }
  path() { return this.eval('location.pathname'); }
  text() { return this.eval('document.body ? document.body.innerText : ""'); }
  status(path) { return this.eval(`fetch(${JSON.stringify(path)}, {credentials: "include"}).then(r => r.status)`); }
}

// A secret goes from Tico to the page through the runner's `hub browser fill`, never through this script.
const fill = (tab, field, selector) => {
  const args = ['browser', 'fill', credential, field, '--target', tab.targetId];
  if (selector) args.push('--selector', selector);
  const r = hub(args);
  return r.json || {ok: false, error: r.err};
};
// The name the step sent, exactly (JSON-quoted, so stray spaces show), with LABEL as this run received it. Names only.
const sent = r => `sent ${JSON.stringify(credential)} (LABEL ${JSON.stringify(env.LABEL)}) ${r.error || ''}`.trim();

async function logIn(tab) {
  await tab.go(practice + '/login');
  const user = fill(tab, 'username', 'input[name=username]');
  check('login: username filled from Tico', user.ok === true, sent(user));
  const pass = fill(tab, 'password', 'input[name=password]');
  check('login: password filled from Tico', pass.ok === true, sent(pass));
  await tab.click('button[type=submit]');
  await tab.waitFor('["/login/2fa", "/me"].includes(location.pathname) || document.body.innerText.includes("Wrong")');
  if ((await tab.path()) === '/login/2fa') {
    const code = fill(tab, 'totp', 'input[name=code]');
    check('login: authenticator code filled from Tico', code.ok === true, sent(code));
    await tab.click('button[type=submit]');
    await tab.waitFor('location.pathname === "/me" || document.body.innerText.includes("That code is wrong")');
  }
  return (await tab.path()) === '/me';
}

async function status(browser) {
  const r = hub(['browser', 'status']);
  check('chrome is running for this bot', r.json && r.json.running === true,
    JSON.stringify(r.json ? {running: r.json.running, version: r.json.version, problem: r.json.problem, error: r.json.error} : r.err));
}

const phases = {
  async status(browser) { await status(browser); },

  // The bot asks the person for the practice login: a card in the chat, saved by the person (the journey, here).
  async 'request-card'(browser, data) {
    const r = hub(['credential', 'request', '--kind', 'login', '--site', host, '--label', label]);
    data.card = (r.json && r.json.id) || '';
    check('login card requested', Boolean(data.card), why(r));
  },

  // The bot signs up on the practice site: its own inbox takes the emailed code, which the bot reads and types.
  async signup(browser, data) {
    await status(browser);
    const inbox = hub(['inbound', 'create-email', 'Practice signup']);
    data.inbox = (inbox.json && inbox.json.inbox_id) || '';
    const address = (inbox.json && inbox.json.address) || '';
    check('inbox created for the signup', Boolean(address));
    const tab = await browser.newTab();
    await tab.go(practice + '/signup');
    await tab.type('input[name=email]', address);
    check('signup: username filled from Tico', fill(tab, 'username', 'input[name=username]').ok === true);
    check('signup: password filled from Tico', fill(tab, 'password', 'input[name=password]').ok === true);
    if (other) {
      const elsewhere = await browser.newTab();
      await elsewhere.go(other + '/signup');
      const refused = fill(elsewhere, 'password', 'input[name=password]');
      check('fill refused on another site', refused.ok === false && refused.error === 'site_mismatch', refused.error || '');
    }
    const before = hub(['inbound', 'cursor', '--inbox-id', data.inbox]).json;
    const after = (before && before.cursor) || 0;
    await tab.click('button[type=submit]');
    const asked = await tab.waitFor('!!document.querySelector("input[name=code]")', 30000);
    check('signup: the page asks for the emailed code', asked);
    const wait = hub(['inbound', 'wait', '--inbox-id', data.inbox, '--after', String(after), '--has-code', '--timeout', '90']);
    const message = wait.json && wait.json.messages && wait.json.messages[0];
    check('the emailed code reached the inbox', Boolean(message));
    const code = message && hub(['inbound', 'code', message.message_id]).json;
    check('the code was readable by the bot', Boolean(code && /^[0-9]{6}$/.test(code.code || '')));
    await tab.type('input[name=code]', (code && code.code) || '');
    await tab.click('button[type=submit]');
    check('account created', await tab.waitFor('location.pathname === "/login"'));
  },

  // A password login before two-step sign-in is on, then the settings page: the code Tico computes from the saved
  // setup key switches it on.
  async 'enable-2fa'(browser) {
    const tab = await browser.newTab();
    check('signed in before two-step is on', await logIn(tab));
    await tab.go(practice + '/settings');
    check('settings: authenticator code filled from Tico', fill(tab, 'totp', 'input[name=code]').ok === true);
    await tab.click('button[type=submit]');
    check('two-step sign-in is on', await tab.waitFor('document.body.innerText.includes("Two-step sign-in is on")'));
  },

  async 'login-2fa'(browser, data) {
    const tab = await browser.newTab();
    check('signed in with the authenticator code', await logIn(tab));
    data.me = await tab.status('/me');
    check('/me is 200 after the second step', data.me === 200, data.me);
  },

  async 'restart-check'(browser, data) {
    const tab = await browser.newTab();
    await tab.go(practice + '/me');
    data.me = await tab.status('/me');
    check('still signed in after the runner restarted', data.me === 200, data.me);
  },

  async relogin(browser, data) {
    const tab = await browser.newTab();
    await tab.go(practice + '/me');
    data.me = await tab.status('/me');
    check('signed out after the sessions ended', data.me === 401, data.me);
    check('signed in again without a person', await logIn(tab));
  },

  async 'needs-you'(browser, data) {
    const r = hub(['browser', 'needs-you', host, '--reason', 'The practice site wants a person to confirm a sign-in']);
    data.needs = (r.json && r.json.id) || '';
    check('the site needs a person is raised', r.ok === true, why(r));
  },

  // Scenario: the phone rental the owner accepted is an SMS inbox. The bot types its number on the practice site and
  // reads the texted code from that inbox, the way the signup reads an emailed one.
  async phone(browser) {
    const tab = await browser.newTab();
    check('signed in for the phone check', await logIn(tab));
    const listed = hub(['inbound', 'inboxes']).json || {};
    const rows = Array.isArray(listed) ? listed : (listed.inboxes || []);
    const sms = rows.find(row => row.kind === 'sms' && row.state === 'active');
    check('the company has an SMS inbox for the rental', Boolean(sms));
    if (!sms) return;
    const before = hub(['inbound', 'cursor', '--inbox-id', sms.inbox_id]).json;
    const after = (before && before.cursor) || 0;
    await tab.go(practice + '/verify-phone');
    await tab.type('input[name=phone]', sms.address);
    await tab.click('button[type=submit]');
    check('the page asks for the texted code', await tab.waitFor('!!document.querySelector("input[name=code]")', 30000));
    const wait = hub(['inbound', 'wait', '--inbox-id', sms.inbox_id, '--kind', 'sms', '--has-code', '--after', String(after), '--timeout', '90']);
    const message = wait.json && wait.json.messages && wait.json.messages[0];
    check('the texted code reached the SMS inbox', Boolean(message));
    const code = message && hub(['inbound', 'code', message.message_id]).json;
    check('the code was readable by the bot', Boolean(code && /^[0-9]{6}$/.test(code.code || '')));
    await tab.type('input[name=code]', (code && code.code) || '');
    await tab.click('button[type=submit]');
    await tab.waitFor('document.body.innerText.includes("Phone verified")', 20000);
    const me = await tab.eval('fetch("/me", {credentials: "include"}).then(r => r.json())');
    check('/me shows the phone as verified', me.phone_verified === true);
  },

  // Scenario: the page sees a browser a person would use: the time zone and language the runner was set to.
  async proxy(browser, data) {
    const tab = await browser.newTab();
    await tab.go(practice + '/');
    check('the practice page loads through the proxy', (await tab.status('/')) === 200);
    const seen = await tab.eval('({zone: Intl.DateTimeFormat().resolvedOptions().timeZone, language: navigator.language})');
    data.zone = seen.zone;
    data.language = seen.language;
    check('time zone is the runner setting', seen.zone === env.EXPECT_TZ, seen.zone);
    check('language is the runner setting', seen.language === env.EXPECT_LANG, seen.language);
  },

  // Scenario: the browser does not announce itself as automation. Headless Chrome shows its name in the user agent,
  // and a driven page reports navigator.webdriver as true.
  async fingerprint(browser) {
    const tab = await browser.newTab();
    await tab.go(practice + '/');
    const f = await tab.eval(`({
      agent: navigator.userAgent, plugins: navigator.plugins.length, languages: navigator.languages.length,
      webdriver: navigator.webdriver, outer: window.outerWidth, inner: window.innerWidth, height: window.innerHeight})`);
    check('the user agent does not say HeadlessChrome', !/HeadlessChrome/.test(f.agent));
    check('plugins are listed', f.plugins > 0, f.plugins);
    check('languages are listed', f.languages > 0, f.languages);
    check('navigator.webdriver is false', f.webdriver === false, f.webdriver);
    check('the window has a size', f.outer > 0 && f.inner > 0 && f.height > 0, `${f.inner}x${f.height}`);
  },

  // Scenario, bot B: its Chrome is the one the second bot must not reach. The port is all that is reported, never the
  // secret in the URL.
  async 'peer-setup'(browser, data) {
    await status(browser);
    const own = hub(['browser', 'status']).json || {};
    data.port = new URL(env.TICO_BROWSER_URL).port;
    data.profile = own.profile || '';
    check('the peer reports its Chrome port', Boolean(data.port));
  },

  // Scenario, bot A on the same computer: the endpoint refuses secrets no run holds, and bot A cannot fill a login it was not granted.
  async 'peer-probe'(browser, data) {
    await status(browser);
    const own = hub(['browser', 'status']).json || {};
    data.profile = own.profile || '';
    check('separate profile from the peer', Boolean(own.profile) && own.profile !== env.PEER_PROFILE, data.profile);
    check('its own endpoint answers its own secret', await fetchStatus(env.TICO_BROWSER_URL + '/json/version') === 200);
    const peer = `http://127.0.0.1:${env.PEER_PORT}`;
    const bare = await fetchStatus(peer + '/json/version');
    check('the peer endpoint refuses a request without a secret', bare !== 200, bare);
    // One endpoint serves every bot on the computer: a secret picks its bot. A secret no run holds is refused.
    const unknown = await fetchStatus(`${peer}/${randomBytes(32).toString('hex')}/json/version`);
    check('the endpoint refuses a secret no live run holds', unknown !== 200, unknown);
    const tab = await browser.newTab();
    // A login this bot was not granted; the runner refuses it before it looks at any page.
    const refusedRun = hub(['browser', 'fill', 'Not-granted-login', 'username', '--target', tab.targetId]);
    const refused = refusedRun.json || {ok: false, error: refusedRun.err};
    check('a login this bot was not granted is refused', refused.ok === false && refused.error === 'not_granted', refused.error || '');
  },

  // Scenario: Chrome was killed; the runner starts it again and the same profile still holds the session. The browser
  // is opened only once Chrome answers, so this phase does not connect at its start.
  async 'after-crash'(browser, data) {
    const deadline = Date.now() + 90000;
    let own = {};
    while (Date.now() < deadline) {
      own = hub(['browser', 'status']).json || {};
      if (own.running === true) break;
      await sleep(2000);
    }
    check('chrome runs again after it was killed', own.running === true);
    data.profile = own.profile || '';
    const reopened = await new Browser().open();
    try {
      const tab = await reopened.newTab();
      await tab.go(practice + '/me');
      data.me = await tab.status('/me');
      // The answer's text says why the practice site refused the request (no secret: the page shows none).
      data.me_body = String(await tab.eval('fetch("/me", {credentials: "include"}).then(async r => (await r.text()).slice(0, 120))')).replace(/\s+/g, ' ');
      check('the same profile is still signed in', data.me === 200, `${data.me} ${data.me_body}`);
    } finally {
      reopened.close();
    }
  },

  // Scenario: Playwright and chrome-devtools-mcp drive the bot's Chrome through its endpoint. Playwright is the copy of
  // playwright-core the image carries under /opt/e2e; the MCP is the runner's own launch configuration.
  async attach(browser) {
    const playwrightPath = env.E2E_PLAYWRIGHT_CORE || '/opt/e2e/node_modules/playwright-core/index.mjs';
    const {chromium} = await import(playwrightPath);
    const cdp = await chromium.connectOverCDP(env.TICO_BROWSER_URL, {timeout: 60000}).catch(() => null);
    check('Playwright attaches over CDP', Boolean(cdp));
    if (cdp) {
      const pages = cdp.contexts().flatMap(context => context.pages());
      check('Playwright sees the bot\'s pages', pages.length > 0, pages.length);
      const sum = pages.length ? await pages[0].evaluate(() => 1 + 1).catch(() => null) : null;
      check('Playwright runs script in a page', sum === 2, sum);
    }
    const mcp = await runMcp();
    check('chrome-devtools-mcp initializes', mcp.initialized);
    check('chrome-devtools-mcp sees the bot\'s pages', mcp.listed, mcp.detail);
  },

  // Scenario: a captcha hand-off. The bot signs in, opens the check page, and asks for a person; the person holds the
  // check through the screen, and the next turn reads it as passed.
  async challenge(browser, data) {
    const tab = await browser.newTab();
    check('signed in for the check', await logIn(tab));
    await tab.go(practice + '/challenge');
    const before = await tab.eval('fetch("/challenge/state", {credentials: "include"}).then(r => r.json()).then(d => d.passed)');
    check('the check is not passed yet', before === false, before);
    const r = hub(['browser', 'needs-you', host, '--reason', 'Hold the screen for three seconds to pass the check']);
    data.needs = (r.json && r.json.id) || '';
    check('the check asks a person', r.ok === true);
  },

  async 'challenge-check'(browser) {
    const tab = await browser.newTab();
    await tab.go(practice + '/challenge');
    const passed = await tab.eval('fetch("/challenge/state", {credentials: "include"}).then(r => r.json()).then(d => d.passed)');
    check('the check is passed after the person held it', passed === true, passed);
  },

  // Scenario: two runs of the same bot at once share its Chrome. Each records when it held its tab, so the host can see
  // that the two runs overlapped.
  async concurrent(browser, data) {
    data.start = Date.now();
    await status(browser);
    const own = hub(['browser', 'status']).json || {};
    data.profile = own.profile || '';
    const tab = await browser.newTab();
    await tab.go(practice + '/');
    check('this run has a tab of its own', Boolean(tab.targetId));
    await sleep(20000);
    data.end = Date.now();
    check('the shared Chrome still answers', (await tab.status('/')) === 200);
  },
};

// The status of one request, without an Origin header (the endpoint refuses one on purpose); 0 when nothing answered.
async function fetchStatus(url) {
  return fetch(url).then(r => r.status).catch(() => 0);
}

// chrome-devtools-mcp over stdio, launched the way the runner launches it for the bot.
async function runMcp() {
  const out = {initialized: false, listed: false, detail: ''};
  const {spawn} = await import('node:child_process');
  let config = null;
  try { config = JSON.parse(env.TICO_BROWSER_MCP || 'null'); } catch { config = null; }
  if (!config) {
    const entry = execFileSync('find', [env.TICO_TOOLS_DIR || '/home/runner/tools', '-path', '*build/src/bin/chrome-devtools-mcp.js', '-print', '-quit'], {encoding: 'utf8'}).trim();
    config = {command: 'node', args: [entry, '--browserUrl', env.TICO_BROWSER_URL], env: {}};
  }
  const child = spawn(config.command, config.args, {env: {...env, ...(config.env || {})}, stdio: ['pipe', 'pipe', 'ignore']});
  let buffer = '';
  const waiting = new Map();
  child.stdout.on('data', chunk => {
    buffer += chunk;
    let i;
    while ((i = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, i);
      buffer = buffer.slice(i + 1);
      try { const msg = JSON.parse(line); if (waiting.has(msg.id)) waiting.get(msg.id)(msg); } catch { /* not a message */ }
    }
  });
  let nextId = 1;
  const rpc = (method, params) => {
    const id = nextId++;
    child.stdin.write(JSON.stringify({jsonrpc: '2.0', id, method, params}) + '\n');
    return Promise.race([
      new Promise(resolve => waiting.set(id, resolve)),
      sleep(90000).then(() => ({error: {message: `${method} timed out`}})),
    ]);
  };
  try {
    const init = await rpc('initialize', {protocolVersion: '2025-06-18', capabilities: {}, clientInfo: {name: 'bot-steps', version: '1'}});
    out.initialized = Boolean(init.result);
    child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
    const listed = await rpc('tools/call', {name: 'list_pages', arguments: {}});
    const text = JSON.stringify((listed.result && listed.result.content) || listed.error || '');
    out.listed = /about:|http/.test(text);
    out.detail = text.slice(0, 120);
  } finally {
    child.kill();
  }
  return out;
}

async function main() {
  const data = {};
  if (!phases[phase]) {
    check('known phase', false, phase);
  } else {
    let browser = null;
    try {
      if (!NO_BROWSER.has(phase)) browser = await new Browser().open();
      await phases[phase](browser, data);
    } catch (error) {
      check('phase finished', false, error.message);
    } finally {
      if (browser) browser.close();
    }
  }
  const ok = checks.length > 0 && checks.every(c => c.ok);
  console.log(JSON.stringify({phase, ok, data, checks}));
  return ok ? 0 : 1;
}

main().then(code => process.exit(code), error => {
  console.log(JSON.stringify({phase, ok: false, data: {}, checks: [...checks, {name: 'harness error', ok: false, detail: error.message}]}));
  process.exit(1);
});
