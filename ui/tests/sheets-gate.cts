// The Sheets look's gate (ui/theme.ts, ui/styles/tokens.css): off unless this browser saved it on, on <html> before the
// first paint when it is, and with it the tokens every page reads. Also the kit the pages build with
// (ui/app/parts-kit.ts, the page part sheets-kit): its formatter and one part's box. TICO_SCREENSHOT_DIR=<dir> saves the two looks.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const path: typeof import('node:path') = require('node:path');
const {html, look, isUiPath, serveUi} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;

declare const ticoTheme: {set(v: string): void};
declare const ticoSheets: {get(): boolean; set(on: boolean): void};
declare function snapVersion(): string;
declare function uiPartLoad(part: string): Promise<void>;
declare const PK: {
  age(when: unknown): string;
  duration(seconds: number): string;
  money(amount: number, o?: {cents?: boolean}): string;
  btn(o: {label: string; fill?: boolean}): string;
  fact(label: string, value: string): string;
};

const URL0 = 'https://tico-ui.test/';
// What the app draws on today, and what Sheets draws on, as the browser computes them.
const TODAY = {dark: 'rgb(16, 16, 16)', light: 'rgb(255, 255, 255)'};
const GROUND = {dark: 'rgb(11, 11, 11)', light: 'rgb(233, 233, 233)'};
const shots = process.env.TICO_SCREENSHOT_DIR;

// A token as a colour: var() chains resolved, the way a page that uses it sees it.
const colour = (page: Page, token: string): Promise<string> => page.evaluate(name => {
  const probe = document.createElement('i');
  probe.style.color = `var(${name})`;
  document.body.append(probe);
  const seen = getComputedStyle(probe).color;
  probe.remove();
  return seen;
}, token);
const isSheets = (page: Page): Promise<boolean> => page.evaluate(() => document.documentElement.classList.contains('sheets'));
const barColour = (page: Page): Promise<string> => page.evaluate(() => document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')!.content.toLowerCase());

async function open(browser: Browser, {sheets = false, query = '', holdStyles = false} = {}) {
  const page = await browser.newPage({serviceWorkers: 'block', viewport: {width: 1280, height: 800}});
  const errors: string[] = [], held: (() => void)[] = [];
  let hold = holdStyles;
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(() => { (window as unknown as {EventSource?: unknown}).EventSource = class { readyState = 0; onerror: unknown = null; addEventListener() {} removeEventListener() {} close() {} } as unknown as typeof EventSource; });
  if (sheets) await look(page, {sheets: true});
  await page.route('**/*', async (route: Route) => {
    const p = new URL(route.request().url()).pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (isUiPath(p)) {
      if (hold && p.endsWith('.css')) await new Promise<void>(resolve => held.push(resolve));
      return serveUi(route, p);
    }
    if (p === '/api/me') return json({id: 'ana', email: 'ana@acme.example', role: 'owner', cloud: true, config: {company_name: 'Acme'}});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', status: 'active', host: 'keeper', can_chat: true}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], groups: []});
    if (p === '/api/status') return json({cloud: true, keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: [{bot: 'ops', state: 'idle', open_tasks: 0}]});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (p === '/api/v2/task-types') return json({types: []});
    return json(unrouted(p));
  });
  const release = (): void => { hold = false; held.splice(0).forEach(go => go()); };
  await page.goto(`${URL0}${query}#/tasks`, {waitUntil: holdStyles ? 'commit' : 'domcontentloaded'});
  return {page, errors, held, release};
}
const drawn = (page: Page): Promise<unknown> => page.getByRole('heading', {name: 'Tasks', exact: true}).waitFor();

(async () => {
  const browser = await chromium.launch({headless: true});

  // Off unless saved on: no class, today's ground, the browser's bar as the page ships it.
  const plain = await open(browser);
  await drawn(plain.page);
  assert.equal(await isSheets(plain.page), false, 'the look is off by default');
  assert.equal(await colour(plain.page, '--bg'), TODAY.dark);
  assert.equal(await barColour(plain.page), '#131413');
  assert.equal(await plain.page.evaluate(() => ticoSheets.get()), false);
  const plainSnap = await plain.page.evaluate(() => snapVersion());
  if (shots) await plain.page.screenshot({animations: 'disabled', path: path.join(shots, 'sheets-gate-off.png')});
  await plain.page.evaluate(() => ticoTheme.set('light'));
  assert.equal(await colour(plain.page, '--bg'), TODAY.light);
  // Another tab turns it on: this one follows, without a reload.
  await plain.page.evaluate(() => { localStorage.setItem('tico.sheets', '1'); dispatchEvent(new StorageEvent('storage', {key: 'tico.sheets'})); });
  assert.equal(await isSheets(plain.page), true, 'a change in another tab is followed');
  assert.deepEqual(plain.errors, []);
  await plain.page.close();

  // Saved on: the class is there while the stylesheets are still on their way, so before anything is painted.
  const on = await open(browser, {sheets: true, holdStyles: true});
  await on.page.waitForFunction(() => document.documentElement.classList.contains('sheets'), null, {polling: 50});
  assert.ok(on.held.length > 0, 'a stylesheet is still held');
  assert.equal(await on.page.evaluate(() => performance.getEntriesByType('paint').length), 0, 'nothing is painted before the class is on');
  assert.equal(await barColour(on.page), '#0b0b0b');
  on.release();
  await drawn(on.page);
  assert.equal(await colour(on.page, '--bg'), GROUND.dark);
  assert.equal(await on.page.evaluate(() => ticoSheets.get()), true);
  // A screen kept in one look is never one the other look restores (ui/app/boot-snap.ts).
  assert.notEqual(await on.page.evaluate(() => snapVersion()), plainSnap);
  // The names a bot's page reads (ui/app/task-docs.ts docLook) still resolve, to the Sheets values.
  assert.equal(await colour(on.page, '--ink'), 'rgb(242, 242, 242)');
  assert.equal(await colour(on.page, '--surface'), 'rgb(26, 26, 26)');
  assert.equal(await colour(on.page, '--muted'), 'rgb(156, 156, 156)');
  if (shots) await on.page.screenshot({animations: 'disabled', path: path.join(shots, 'sheets-gate-on.png')});

  // Light, chosen and as the computer's own (System): one set of values.
  await on.page.evaluate(() => ticoTheme.set('light'));
  assert.equal(await colour(on.page, '--bg'), GROUND.light);
  assert.equal(await colour(on.page, '--ink'), 'rgb(17, 17, 17)');
  assert.equal(await colour(on.page, '--surface'), 'rgb(255, 255, 255)');
  assert.equal(await colour(on.page, '--muted'), 'rgb(94, 94, 94)');
  assert.equal(await barColour(on.page), '#e9e9e9');
  await on.page.emulateMedia({colorScheme: 'light'});
  await on.page.evaluate(() => ticoTheme.set('system'));
  assert.equal(await colour(on.page, '--bg'), GROUND.light);
  await on.page.emulateMedia({colorScheme: 'dark'});
  await on.page.waitForFunction(() => document.documentElement.dataset.scheme === 'dark');
  assert.equal(await colour(on.page, '--bg'), GROUND.dark);
  assert.equal(await barColour(on.page), '#0b0b0b');

  // The zero is plain: the feature is set once on <html>, text inherits it, and it changes the glyph drawn.
  assert.match(await on.page.evaluate(() => getComputedStyle(document.documentElement).fontFeatureSettings), /"ss09"/);
  assert.match(await on.page.evaluate(() => getComputedStyle(document.body).fontFeatureSettings), /"ss09"/);
  await on.page.evaluate(async () => {
    const box = document.createElement('div');
    box.id = 'zero-probe';
    box.style.cssText = 'position:fixed;left:0;top:0;z-index:99999;display:flex;gap:8px;padding:8px;background:#fff;color:#000;font:400 64px/1 var(--sans)';
    box.innerHTML = '<span id="zero-set" style="font-feature-settings:&quot;ss09&quot; 1">0</span><span id="zero-unset" style="font-feature-settings:&quot;ss09&quot; 0">0</span>';
    document.body.append(box);
    await document.fonts.load('400 64px "Atkinson Hyperlegible Next"', '0');
  });
  const zeroSet = await on.page.locator('#zero-set').screenshot({animations: 'disabled'});
  const zeroUnset = await on.page.locator('#zero-unset').screenshot({animations: 'disabled'});
  assert.ok(!zeroSet.equals(zeroUnset), 'the feature draws another zero');
  await on.page.evaluate(() => document.getElementById('zero-probe')!.remove());

  // The kit: ages, durations and money in one way of writing, and a part's box from the tokens.
  await on.page.evaluate(() => uiPartLoad('sheets-kit'));
  const words = await on.page.evaluate(() => {
    const before = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();
    return {now: PK.age(before(20)), minutes: PK.age(before(6 * 60)), hours: PK.age(before(3 * 3600)), days: PK.age(before(3 * 86400)),
      weeks: PK.age(before(40 * 86400)), long: PK.duration(3840), short: PK.duration(125), whole: PK.money(1240.5), cents: PK.money(1240.5, {cents: true})};
  });
  assert.equal(words.now, 'now');
  assert.equal(words.minutes, '6m');
  assert.equal(words.hours, '3h');
  assert.match(words.days, /^(Sun|Mon|Tue|Wed|Thu|Fri|Sat)$/);
  assert.match(words.weeks, /^[A-Z][a-z]{2} \d{1,2}(, \d{4})?$/);
  assert.equal(words.long, '1h 4m');
  assert.equal(words.short, '2m 5s');
  assert.equal(words.whole, '$1,241');
  assert.equal(words.cents, '$1,240.50');
  const box = await on.page.evaluate(() => {
    const host = document.createElement('div');
    host.innerHTML = PK.btn({label: 'Approve', fill: true}) + PK.fact('Owner', 'Ana');
    document.body.append(host);
    const btn = getComputedStyle(host.querySelector('.s-btn')!), fact = host.querySelector<HTMLElement>('.s-fact-k')!;
    const seen = {height: btn.height, radius: btn.borderTopLeftRadius, fill: btn.backgroundColor, label: fact.getBoundingClientRect().width};
    host.remove();
    return seen;
  });
  assert.deepEqual(box, {height: '32px', radius: '8px', fill: 'rgb(240, 240, 240)', label: 176});
  assert.deepEqual(on.errors, []);
  await on.page.close();

  // The address turns it on and off, and the choice is saved; ticoSheets does the same without a load.
  const asked = await open(browser, {query: '?sheets=1'});
  await drawn(asked.page);
  assert.equal(await isSheets(asked.page), true, '?sheets=1 turns it on');
  assert.equal(await asked.page.evaluate(() => localStorage.getItem('tico.sheets')), '1');
  await asked.page.evaluate(() => ticoSheets.set(false));
  assert.equal(await isSheets(asked.page), false);
  assert.equal(await asked.page.evaluate(() => localStorage.getItem('tico.sheets')), null);
  assert.equal(await colour(asked.page, '--bg'), TODAY.dark);
  assert.equal(await barColour(asked.page), '#131413');
  await asked.page.evaluate(() => ticoSheets.set(true));
  await asked.page.goto(`${URL0}?sheets=0#/tasks`, {waitUntil: 'domcontentloaded'});
  await drawn(asked.page);
  assert.equal(await isSheets(asked.page), false, '?sheets=0 turns it off');
  assert.equal(await asked.page.evaluate(() => localStorage.getItem('tico.sheets')), null);
  assert.deepEqual(asked.errors, []);
  await browser.close();
  console.log('sheets-gate: ok');
})().catch(error => { console.error(error); process.exit(1); });
