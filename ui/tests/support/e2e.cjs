// Shared by the end-to-end journeys (ui/tests/e2e-*.cjs). Each journey runs on a real hub (support/real-hub.cjs, the scenario
// org) at a phone (390x844) and then a desktop (1440x900). A page is signed in as a teammate and watched from its first
// load: an uncaught error, a console error or an API request answered 4xx/5xx fails the journey, unless the journey says
// it expects that answer (expectFailure). Data the journey needs is made through the API as the teammate or bot that
// makes it in the product (call); what the journey checks is what the page draws.
const assert = require('node:assert/strict');
const {startHub, PHONE, DESKTOP} = require('./real-hub.cjs');
const {run, go, shot, sideways, t} = require('./redesign-e2e.cjs');

const SEEDS = ['org', 'toured', 'roster', 'finance'];   // the scenario org, the first-visit tour seen, Gina (the guest), Finance set up
const HUB_PATH = /^\/api\//;

// Watches one page: uncaught errors, console errors (a failed resource is counted once, as the API answer below), and
// each API answer 4xx/5xx. `page.watch.allowed` holds the answers a journey expects: {status, path, method?}.
function watch(page) {
  const w = page.watch = {errors: [], failed: [], allowed: [], known: []};
  page.on('pageerror', e => w.errors.push('uncaught: ' + e.message));
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const where = m.location().url || '';
    if (/Failed to load resource/.test(m.text()) && HUB_PATH.test(new URL(where || 'http://x/').pathname)) return;
    w.errors.push('console: ' + m.text().slice(0, 300));
  });
  page.on('response', r => {
    const u = new URL(r.url());
    if (!HUB_PATH.test(u.pathname) || r.status() < 400) return;
    w.failed.push({method: r.request().method(), path: u.pathname, status: r.status()});
  });
  // A request the page cancelled itself (a navigation or a reload while it was in flight) is not a failure; a request
  // that never got an answer is.
  page.on('requestfailed', r => {
    const u = new URL(r.url()), why = r.failure()?.errorText || 'failed';
    if (HUB_PATH.test(u.pathname) && why !== 'net::ERR_ABORTED') w.failed.push({method: r.method(), path: u.pathname, status: why});
  });
  return page;
}

// An answer the journey expects, e.g. a teammate's read of a private task that the hub refuses: the path it names, the
// status, and optionally the method. Each one covers any number of answers.
function expectFailure(page, status, path, method = null) {
  page.watch.allowed.push({status, path: new RegExp(path), method});
}

// A known bug the suite found and reports, not fixed here: an uncaught or console error matching `pattern` is not
// counted, and `why` says what it is (it is named in the run's report).
function expectError(page, pattern, why) {
  page.watch.known.push({pattern, why});
}

// Fails the journey if the page had an uncaught or console error, or an API answer nobody expected.
function assertClean(page, where) {
  const w = page.watch;
  const unexpected = w.failed.filter(f => !w.allowed.some(a => a.status === f.status && a.path.test(f.path)
    && (!a.method || a.method === f.method)));
  const errors = w.errors.filter(e => !w.known.some(k => k.pattern.test(e)));
  assert.deepEqual(errors, [], `${where}: no page or console errors`);
  assert.deepEqual(unexpected, [], `${where}: no failed API requests`);
}

// A page as `who` (a teammate, as hub.tokens names it), watched from its first load, with the shell drawn and the
// runner-version notice snoozed (the simulated runner reports "test", which the hub calls out of date).
async function open(browser, hub, who, {phone = false, hash = '', timezone = ''} = {}) {
  const token = hub.tokens[who];
  if (!token) throw new Error(`no teammate "${who}" on this hub: ${Object.keys(hub.tokens).join(', ')}`);
  const context = await browser.newContext({
    ...(phone ? PHONE : DESKTOP),
    ...(timezone ? {timezoneId: timezone} : {}),
    extraHTTPHeaders: {Authorization: 'Bearer ' + token},
  });
  // Playwright's serviceWorkers: 'block' puts its own registration stub in every frame, and reading navigator.serviceWorker
  // throws in a sandboxed frame (a page's preview), which the journey then reports as an uncaught error. The same stub is
  // added here with the read guarded, so a sandboxed frame has nothing to refuse.
  await context.addInitScript(() => {
    try {
      const sw = navigator.serviceWorker;
      if (sw) sw.register = async () => { console.warn('Service Worker registration blocked by the test harness'); };
    } catch {}
  });
  await context.addInitScript(() => {
    try { localStorage.setItem('tico.needsYou', JSON.stringify({snoozedUntil: Date.now() + 864e5})); } catch {}
  });
  const page = watch(await context.newPage());
  await page.goto(hub.url + '/' + (hash ? '#' + hash.replace(/^#/, '') : ''));
  await page.waitForFunction(() => {
    const me = document.querySelector('#account .account-email')?.textContent || '';
    return me !== '' && me !== 'Signing in…' && document.querySelector('#main')?.children.length > 0;
  }, null, {timeout: t(20000)});
  return page;
}

// The API as a teammate or bot calls it, from Node: `who` is a key of hub.tokens or a token. Writes carry a key.
async function call(hub, who, method, path, body) {
  const token = hub.tokens[who] || who;
  const headers = {Authorization: 'Bearer ' + token};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = globalThis.crypto.randomUUID();
  const response = await fetch(hub.url + '/api/v2/' + path, {method, headers,
    body: body === undefined ? undefined : JSON.stringify(body)});
  const text = await response.text();
  let parsed = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return {status: response.status, body: parsed};
}

// A call that must be answered 2xx; returns the body.
async function must(hub, who, method, path, body, what = `${method} ${path}`) {
  const reply = await call(hub, who, method, path, body);
  assert.ok(reply.status >= 200 && reply.status < 300, `${what}: ${reply.status} ${JSON.stringify(reply.body).slice(0, 300)}`);
  return reply.body;
}

// Polls `check` until it returns something truthy and returns it, for what the page sends after a delay (a card's undo
// toast runs out first). Fails with `what` when it does not come in time; nothing here waits on a fixed time.
async function until(check, what, ms = t(30000)) {
  const end = Date.now() + ms;
  for (;;) {
    const got = await check();
    if (got) return got;
    if (Date.now() > end) assert.fail(`timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
}

// The text of `selector` once `ready(text)` holds for it, read in the same poll: a list draws row by row and a page can
// re-render, so a check reads the text the page drew in full, not a snapshot taken when some part of it first appeared.
async function drawn(page, selector, ready, what) {
  return until(async () => {
    const text = await page.locator(selector).innerText().catch(() => '');
    return ready(text) ? text : null;
  }, what);
}

// Journey runner: `pass({hub, browser, phone, tag})` on a phone, then on a desktop, each on a fresh hub.
const journey = (name, pass, seeds = SEEDS) => run(name, seeds, pass);

module.exports = {journey, open, watch, expectFailure, expectError, assertClean, call, must, go, shot, sideways, t, until, drawn, SEEDS};
