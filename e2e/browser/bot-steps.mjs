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

const phase = process.argv[2] || '';
const env = process.env;
const practice = env.PRACTICE || '';                   // e.g. http://login.practice.test:8080
const host = env.HOST || 'login.practice.test';
// The practice site is plain http, which a login fills only when its site is written http://.
const site = practice.startsWith('http://') ? 'http://' + host : host;
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

// hub: the CLI's own JSON, whatever its exit code (a refusal prints its reason on stdout). `err` is the end of stderr
// (a usage error, say), which never holds a value the CLI typed.
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

const filled = (name, r) => check(name, r.ok === true, r.error || '');

async function logIn(tab) {
  await tab.go(practice + '/login');
  filled('login: username filled from Tico', fill(tab, 'username', 'input[name=username]'));
  filled('login: password filled from Tico', fill(tab, 'password', 'input[name=password]'));
  await tab.click('button[type=submit]');
  await tab.waitFor('["/login/2fa", "/me"].includes(location.pathname) || document.body.innerText.includes("Wrong")');
  if ((await tab.path()) === '/login/2fa') {
    filled('login: authenticator code filled from Tico', fill(tab, 'totp', 'input[name=code]'));
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

// Phases that only call the hub: they run whether or not the bot's Chrome answers.
const NO_BROWSER = new Set(['request-card', 'needs-you']);

const phases = {
  async status(browser) { await status(browser); },

  // The bot asks the person for the practice login: a card in the chat, saved by the person (the journey, here).
  async 'request-card'(browser, data) {
    const r = hub(['credential', 'request', '--kind', 'login', '--site', site, '--label', label]);
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
    filled('signup: username filled from Tico', fill(tab, 'username', 'input[name=username]'));
    filled('signup: password filled from Tico', fill(tab, 'password', 'input[name=password]'));
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
    filled('settings: authenticator code filled from Tico', fill(tab, 'totp', 'input[name=code]'));
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
};

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
