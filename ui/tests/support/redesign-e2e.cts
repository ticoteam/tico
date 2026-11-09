// Shared by the redesign's real-hub journeys (ui/tests/redesign-e2e-*.cjs): a browser, a fresh hub per pass (phone
// 390x844 first, then a desktop at 1440), a page signed in as an Acme teammate, the API as that page sees it, and
// screenshots. TICO_UI_SHOTS=<dir> saves a screenshot at each step worth looking at.
import type {Browser, Page} from 'playwright';
import type {Hub} from './real-hub.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const path: typeof import('node:path') = require('node:path');
const {startHub, pageAs} = require('./real-hub.cts') as import('./real-hub.cts').Api;
const {t} = require('./load.cts') as import('./load.cts').Api;

export type {Hub};

// A journey's one pass: a fresh hub, the browser, the viewport it runs at.
export interface PassContext {
  hub: Hub;
  browser: Browser;
  phone: boolean;
  tag: 'phone' | 'desktop';
}
export type Pass = (ctx: PassContext) => Promise<void>;

// A page opened by open(): page.errors holds its uncaught errors.
export type RedesignPage = Page & {errors: string[]};

// The API's answer: the status and the parsed body (null when there is none).
export interface ApiReply<T = unknown> {
  status: number;
  body: T;
}

export interface AutomationRule {
  when: {field: string; op: string; value: string}[];
}
export interface Automation {
  id: string;
  name: string;
  version: number;
  steps: {rules: AutomationRule[]}[];
  [key: string]: unknown;
}

const launch = (): Promise<Browser> => {
  const channel = process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined;
  return chromium.launch({headless: true, ...(channel === undefined ? {} : {channel})});
};

// A page as `who`, once the app has drawn its shell; page errors are kept in page.errors. `time` starts the page's clock
// there (it runs on from it), such as the hub's frozen one, so "in 3d" and "Overdue" read as they would on the hub.
async function open(browser: Browser, hub: Hub, who: string,
  {phone = false, hash = '', time = null, timezone = ''}: {phone?: boolean; hash?: string; time?: Date | number | string | null; timezone?: string} = {}): Promise<RedesignPage> {
  const page = await pageAs(browser, hub, who, {phone, timezone});
  if (time) await page.clock.install({time});
  await page.reload();
  const redesign = Object.assign(page, {errors: [] as string[]});
  // A page's preview is a sandboxed frame: Chrome refuses its navigator.serviceWorker read (Playwright's serviceWorkers
  // 'block' stub makes that read), and the app already guards its own read (boot.js). Not an app error.
  redesign.on('pageerror', e => { if (!/serviceWorker.*sandboxed/.test(e.message)) redesign.errors.push(e.message); });
  await page.waitForFunction(() => {
    const me = document.querySelector('#account .account-email')?.textContent || '';
    return me !== '' && me !== 'Signing in…' && (document.querySelector('#main')?.children.length ?? 0) > 0;
  }, null, {timeout: t(20000)});
  if (hash) await go(page, hash);
  return redesign;
}
const go = (page: Page, hash: string): Promise<void> => page.evaluate(h => { location.hash = h; }, hash);

// The API as the page's teammate calls it (its Bearer rides on every request); a write carries an Idempotency-Key.
async function api<T = unknown>(page: Page, method: string, p: string, body?: unknown): Promise<ApiReply<T>> {
  const out = await page.evaluate(async ([method, p, body]: [string, string, unknown]) => {
    const r = await fetch('/api/v2/' + p, {method, cache: 'no-store', signal: AbortSignal.timeout(15000), headers: {'Content-Type': 'application/json',
      ...(method === 'GET' ? {} : {'Idempotency-Key': crypto.randomUUID()})}, body: body === undefined ? null : JSON.stringify(body)});
    return {status: r.status, body: (await r.json().catch(() => null)) as unknown};
  }, [method, p, body] as [string, string, unknown]);
  return out as ApiReply<T>;
}
async function ok<T = unknown>(page: Page, method: string, p: string, body?: unknown): Promise<T> {
  const r = await api<T>(page, method, p, body);
  assert.ok(r.status >= 200 && r.status < 300, `${method} ${p}: ${r.status} ${JSON.stringify(r.body).slice(0, 300)}`);
  return r.body;
}
async function automationNamed<T = Automation>(page: Page, name: string): Promise<T | undefined> {
  return (await ok<{automations: (T & {name: string})[]}>(page, 'GET', 'automations?limit=500')).automations.find(a => a.name === name);
}

// No sideways scroll on the page itself (a path strip scrolls inside its own box).
const sideways = (page: Page): Promise<boolean> => page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  // Finite animations end first, but not past a second: a paused one never finishes, and its `finished` would hold the shot.
  await page.evaluate(() => Promise.race([
    Promise.all(document.getAnimations().filter(a => a.effect?.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))),
    new Promise<void>(r => setTimeout(r, 1000))]));
  await page.screenshot({path: path.join(dir, name + '.png')});
}

// Runs `pass({hub, browser, phone, tag})` on a phone, then on a desktop, each on a fresh hub with these seeds.
// `only: 'phone' | 'desktop'` runs one viewport: for a journey whose two viewports draw the same thing, or whose other
// viewport another script covers (say which, where it is set).
async function journey(name: string, seeds: string[], pass: Pass, {only = null}: {only?: 'phone' | 'desktop' | null} = {}): Promise<void> {
  const browser = await launch();
  try {
    for (const phone of only ? [only === 'phone'] : [true, false]) {
      const hub = await startHub({seeds});
      try {
        assert.deepEqual(hub.skipped, [], 'every seed ran');
        await pass({hub, browser, phone, tag: phone ? 'phone' : 'desktop'});
      } finally {
        // A page a pass left open keeps its live stream to the hub, and the hub's shutdown would wait for it.
        for (const context of browser.contexts()) await context.close().catch(() => {});
        await hub.stop();
      }
    }
  } finally { await browser.close(); }
  console.log(name + ': ok');
}
const run = (name: string, seeds: string[], pass: Pass, options?: {only?: 'phone' | 'desktop' | null}): Promise<void> =>
  journey(name, seeds, pass, options).catch(e => { console.error(e); process.exit(1); });

const shared = {run, open, go, api, ok, automationNamed, sideways, shot, t};
export type Api = typeof shared;
module.exports = shared;
