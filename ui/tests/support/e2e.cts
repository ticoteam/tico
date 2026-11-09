// Shared by the end-to-end journeys (ui/tests/e2e-*.cjs). Each journey runs on a real hub (support/real-hub.cts, the scenario
// org) at a phone (390x844) and then a desktop (1440x900). A page is signed in as a teammate and watched from its first
// load: an uncaught error, a console error or an API request answered 4xx/5xx fails the journey, unless the journey says
// it expects that answer (expectFailure). Data the journey needs is made through the API as the teammate or bot that
// makes it in the product (call); what the journey checks is what the page draws.
import type {Browser, Page} from 'playwright';
import type {ApiReply, Hub, Pass, PassContext} from './redesign-e2e.cts';
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {PHONE, DESKTOP} = require('./real-hub.cts') as import('./real-hub.cts').Api;
const {run, go, shot, sideways, t} = require('./redesign-e2e.cts') as import('./redesign-e2e.cts').Api;

export type {Hub, PassContext};

// What watch() keeps on a page: its errors, its failed API answers, and the ones the journey expects.
export interface Watch {
  errors: string[];
  failed: {method: string; path: string; status: number | string}[];
  allowed: {status: number; path: RegExp; method: string | null}[];
  known: {pattern: RegExp; why: string}[];
}

export type WatchedPage = Page & {watch: Watch};

const SEEDS = ['org', 'toured', 'roster', 'finance'];   // the scenario org, the first-visit tour seen, Gina (a reader), Finance set up
const HUB_PATH = /^\/api\//;

// Watches one page: uncaught errors, console errors (a failed resource is counted once, as the API answer below), and
// each API answer 4xx/5xx. `page.watch.allowed` holds the answers a journey expects: {status, path, method?}.
function watch(page: Page): WatchedPage {
  const w: Watch = {errors: [], failed: [], allowed: [], known: []};
  const watched = Object.assign(page, {watch: w});
  watched.on('pageerror', e => w.errors.push('uncaught: ' + e.message));
  watched.on('console', m => {
    if (m.type() !== 'error') return;
    const where = m.location().url || '';
    if (/Failed to load resource/.test(m.text()) && HUB_PATH.test(new URL(where || 'http://x/').pathname)) return;
    w.errors.push('console: ' + m.text().slice(0, 300));
  });
  watched.on('response', r => {
    const u = new URL(r.url());
    if (!HUB_PATH.test(u.pathname) || r.status() < 400) return;
    w.failed.push({method: r.request().method(), path: u.pathname, status: r.status()});
  });
  // A request the page canceled itself (a navigation or a reload while it was in flight) is not a failure; a request
  // that never got an answer is.
  watched.on('requestfailed', r => {
    const u = new URL(r.url()), why = r.failure()?.errorText || 'failed';
    if (HUB_PATH.test(u.pathname) && why !== 'net::ERR_ABORTED') w.failed.push({method: r.method(), path: u.pathname, status: why});
  });
  return watched;
}

// An answer the journey expects, e.g. a teammate's read of a private task that the hub refuses: the path it names, the
// status, and optionally the method. Each one covers any number of answers.
function expectFailure(page: Page, status: number, path: string | RegExp, method: string | null = null): void {
  (page as WatchedPage).watch.allowed.push({status, path: new RegExp(path), method});
}

// A known bug the suite found and reports, not fixed here: an uncaught or console error matching `pattern` is not
// counted, and `why` says what it is (it is named in the run's report).
function expectError(page: Page, pattern: RegExp, why: string): void {
  (page as WatchedPage).watch.known.push({pattern, why});
}

// Fails the journey if the page had an uncaught or console error, or an API answer nobody expected.
function assertClean(page: Page, where: string): void {
  const w = (page as WatchedPage).watch;
  const unexpected = w.failed.filter(f => !w.allowed.some(a => a.status === f.status && a.path.test(f.path)
    && (!a.method || a.method === f.method)));
  const errors = w.errors.filter(e => !w.known.some(k => k.pattern.test(e)));
  assert.deepEqual(errors, [], `${where}: no page or console errors`);
  assert.deepEqual(unexpected, [], `${where}: no failed API requests`);
}

// A page as `who` (a teammate, as hub.tokens names it), watched from its first load, with the shell drawn and the
// runner-version notice snoozed (the simulated runner reports "test", which the hub calls out of date).
async function open(browser: Browser, hub: Hub, who: string,
  {phone = false, hash = '', timezone = ''}: {phone?: boolean; hash?: string; timezone?: string} = {}): Promise<WatchedPage> {
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
      if (sw) sw.register = (async () => { console.warn('Service Worker registration blocked by the test harness'); }) as unknown as ServiceWorkerContainer['register'];
    } catch {}
  });
  const page = watch(await context.newPage());
  await page.goto(hub.url + '/' + (hash ? '#' + hash.replace(/^#/, '') : ''));
  await page.waitForFunction(() => {
    const me = document.querySelector('#account .account-email')?.textContent || '';
    return me !== '' && me !== 'Signing in…' && (document.querySelector('#main')?.children.length ?? 0) > 0;
  }, null, {timeout: t(20000)});
  return page;
}

// The API as a teammate or bot calls it, from Node: `who` is a key of hub.tokens or a token. Writes carry a key.
async function call<T = unknown>(hub: Hub, who: string, method: string, path: string, body?: unknown): Promise<ApiReply<T>> {
  const token = hub.tokens[who] || who;
  const headers: Record<string, string> = {Authorization: 'Bearer ' + token};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (method !== 'GET') headers['Idempotency-Key'] = globalThis.crypto.randomUUID();
  const response = await fetch(hub.url + '/api/v2/' + path, {method, headers,
    body: body === undefined ? null : JSON.stringify(body)});
  const text = await response.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = text; }
  return {status: response.status, body: parsed as T};
}

// A call that must be answered 2xx; returns the body.
async function must<T = unknown>(hub: Hub, who: string, method: string, path: string, body?: unknown,
  what = `${method} ${path}`): Promise<T> {
  const reply = await call<T>(hub, who, method, path, body);
  assert.ok(reply.status >= 200 && reply.status < 300, `${what}: ${reply.status} ${JSON.stringify(reply.body).slice(0, 300)}`);
  return reply.body;
}

// Polls `check` until it returns something truthy and returns it, for what the page sends after a delay (a card's undo
// toast runs out first). Fails with `what` when it does not come in time; nothing here waits on a fixed time.
async function until<T>(check: () => T | Promise<T>, what: string, ms: number = t(30000)): Promise<NonNullable<T>> {
  const end = Date.now() + ms;
  for (;;) {
    const got = await check();
    if (got) return got;
    if (Date.now() > end) assert.fail(`timed out waiting for ${what}`);
    await new Promise<void>(resolve => setTimeout(resolve, 250));
  }
}

// The text of `selector` once `ready(text)` holds for it, read in the same poll: a list draws row by row and a page can
// re-render, so a check reads the text the page drew in full, not a snapshot taken when some part of it first appeared.
async function drawn(page: Page, selector: string, ready: (text: string) => boolean, what: string): Promise<string> {
  return until(async () => {
    const text = await page.locator(selector).innerText().catch(() => '');
    return ready(text) ? text : null;
  }, what);
}

// Journey runner: `pass({hub, browser, phone, tag})` on a phone, then on a desktop, each on a fresh hub (`{only: 'phone'}`
// or `{only: 'desktop'}` as the last argument runs one viewport; see support/redesign-e2e.cts).
const journey = (name: string, pass: Pass, seeds: string[] = SEEDS, options?: {only?: 'phone' | 'desktop' | null}): Promise<void> =>
  run(name, seeds, pass, options);

const shared = {journey, open, watch, expectFailure, expectError, assertClean, call, must, go, shot, sideways, t, until, drawn, SEEDS};
export type Api = typeof shared;
module.exports = shared;
