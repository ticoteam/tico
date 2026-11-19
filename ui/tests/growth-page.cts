// The Growth page (ui/app/growth-page.ts) on the one read, against a fake server that answers GET /api/v2/growth/read
// and /api/v2/growth/segments with the real read of three company shapes (fixtures/growth-read-*.json, written by
// backend/tests/test_growth_page_fixtures.py): TIDY (two revenue segments, paid channels over budget, a no-spend
// referral channel, Unknown), Tico (no segments and no spend: words, never 0) and HotMic (a retention segment: cost per
// retained in place of LTV). Each draws its tiles, channels, charts, retention and KPIs; a segment tab and a month
// read again and the address keeps them; the page chat carries the scope and month; a failed read retries; a phone
// never scrolls sideways. TICO_UI_SHOTS=<dir> saves screenshots of each shape, desktop and phone.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
declare const S: {me: unknown; emps?: unknown[]};
declare function gpChatItem(): {kind: string; id: string; name: string} | null;
interface Fig {value: number | null; status?: string; window?: string; verdict?: string}
interface Read {period: string; headline: Record<string, Fig>; channels: Record<string, unknown>[]; metrics: {name: string}[]; series: {cac: number | null}[]}
interface Shape {segments: {slug: string; name: string}[]; reads: Record<string, Read>}
const uiDir = path.join(__dirname, '..');
const roster = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'redesign-roster.json'), 'utf8'));
const shape = (name: string): Shape => JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', `growth-read-${name}.json`), 'utf8'));
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const OWNER = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true, bot_admin: true};
const GM = {name: 'growth-manager', display_name: 'Growth Manager', org_parent: '', host: 'hub', status: 'active', can_chat: true};
const REVENUE = ['signups', 'activated', 'paid', 'new', 'cost', 'cac', 'kept', 'ltv', 'ltv-cac', 'payback'];

async function open(browser: Browser, name: string, {viewport = DESKTOP, hash = '#/growth', theme = 'dark', fail = 0, edit = (_: Shape) => {}} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE, timezoneId: 'UTC'});
  await page.clock.setFixedTime(new Date('2026-11-18T12:00:00Z'));   // the fixtures' month, 2026-10, is the last full one
  const errors: string[] = [], reads: string[] = [], data = shape(name);
  edit(data);
  let failing = fail;
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch { /* none */ } }, theme);
  const config = {company_name: 'Acme'};
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events' || p.endsWith('/watch')) return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...OWNER, config});
    if (p === '/api/employees') return json([...roster.employees, GM]);
    if (p === '/api/humans') return json(roster.humans);
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/groups') return json([]);
    if (p === '/api/v2/growth/segments') return json({segments: data.segments, may_edit: true});
    if (p === '/api/v2/growth/read') {
      reads.push(url.searchParams.toString());
      if (failing > 0) { failing--; return json({error: {code: 'internal', detail: 'Did not load'}}, 500); }
      const read = data.reads[url.searchParams.get('scope') || 'all'];
      const period = url.searchParams.get('period');
      return read ? json(period ? {...read, period} : read) : json({error: {code: 'not_found', detail: 'No such segment'}}, 404);
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  return {page, errors, reads};
}
const drawn = (page: Page) => page.locator('#gr-body.gp .gp-tile').first().waitFor();
const keys = (page: Page) => page.locator('#gr-body .gp-tile').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.k));
const rows = (page: Page) => page.locator('#gr-body .gp-ch tbody tr').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.channel));
const word = (page: Page, sel: string) => page.locator(`#gr-body ${sel} .gp-word`).count();
const tabs = (page: Page) => page.locator('#main header.ph .ph-tabs a').evaluateAll(els => els.map(el => [el.textContent, el.getAttribute('aria-current')]));
// A figure the read has no value for says why; nothing reads as a bare zero.
const zeros = (page: Page) => page.locator('#gr-body .gp-v').evaluateAll(els => els.map(el => el.textContent?.trim()).filter(t => /^\$?0(\.0+)?%?$/.test(t || '')));
const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      // TIDY: All and a tab per segment; paid channels with budgets, a referral channel with no spend, Unknown last.
      const {page, errors, reads} = await open(browser, 'tidy');
      await drawn(page);
      assert.equal(await page.locator('#side a[data-nav="growth"].cur').count(), 1, 'Growth is current');
      assert.deepEqual(await tabs(page), [['All', 'page'], ['Consumer', null], ['Rental', null]]);
      assert.deepEqual(await keys(page), REVENUE);
      assert.deepEqual(await rows(page), ['search-consumer', 'referrals', 'search-rental', 'outbound', 'unknown']);
      assert.equal(await page.locator('#gr-body tr[data-channel="search-consumer"] .gp-over').count(), 1, 'over budget');
      assert.equal(await word(page, 'tr[data-channel="referrals"]'), 1, 'no spend says so');
      assert.equal(await page.locator('#gr-body tr[data-channel="search-consumer"] .gr-d').count(), 1, 'a verdict on the CAC');
      assert.equal(await word(page, 'tr[data-channel="search-rental"]'), 1, 'too few to judge its CAC');
      assert.equal(await page.locator('#gr-body .gp-chart svg').count(), 4, 'four trends');
      assert.equal(await page.locator('#gr-body .grc-ret-t tbody tr').count(), 6, 'six cohorts');
      assert.equal(await page.locator('#gr-body .gp-kpis a').getAttribute('href'), '#/strategy/goals?kpi=kpi-1');
      assert.equal(await page.locator('#gr-body .gp-health').count(), 1);
      assert.deepEqual(await zeros(page), []);
      assert.deepEqual(await page.evaluate(() => { const i = gpChatItem(); return i && [i.kind, i.id]; }), ['growth', 'all@2026-10'], 'the chat carries the scope and month');
      await shot(page, 'desktop-growth-tidy');
      // A month picked reads again for it, and the address keeps it; the months are the trends', newest first.
      const month = page.locator('#main select[data-gp-month]');
      const options = await month.locator('option').evaluateAll(els => els.map(el => (el as HTMLOptionElement).value));
      assert.equal(options[0], '2026-10');
      assert.deepEqual(options, [...options].sort().reverse());
      await month.selectOption('2026-09');
      await page.waitForFunction(() => location.hash === '#/growth?m=2026-09');
      await page.waitForFunction(() => (document.querySelector('#main select[data-gp-month]') as HTMLSelectElement | null)?.value === '2026-09');
      assert.deepEqual(await month.locator('option').evaluateAll(els => els.map(el => (el as HTMLOptionElement).value)), options, 'an older month keeps the list');
      assert.match(reads.at(-1)!, /(^|&)period=2026-09(&|$)/);
      // Rental: its own read, the month kept; too few customers to say what they keep or are worth.
      await page.locator('#main header.ph .ph-tabs a', {hasText: 'Rental'}).click();
      await page.waitForFunction(() => location.hash === '#/growth?scope=segment%3Arental&m=2026-09');
      await page.waitForFunction(() => document.querySelector('#main .ph-tabs a[aria-current]')?.textContent === 'Rental');
      assert.equal(new URLSearchParams(reads.at(-1)).get('scope'), 'segment:rental');
      assert.deepEqual(await rows(page), ['search-rental', 'outbound']);
      for (const k of ['kept', 'ltv', 'ltv-cac', 'payback']) assert.equal(await word(page, `[data-k="${k}"]`), 1, `${k} says too few`);
      assert.equal((await page.evaluate(() => gpChatItem()))?.id, 'segment:rental@2026-09');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Tico: no segments, no tabs; no spend anywhere, so cost and CAC say so (CAC with effort beside it), and the
      // charts with nothing to draw say why.
      const {page, errors} = await open(browser, 'tico');
      await drawn(page);
      assert.equal(await page.locator('#main header.ph .ph-tabs').count(), 0, 'no tabs');
      assert.deepEqual(await keys(page), REVENUE);
      for (const k of ['activated', 'cost', 'cac', 'kept', 'ltv']) assert.equal(await word(page, `[data-k="${k}"]`), 1, `${k} says why`);
      assert.equal(await page.locator('#gr-body [data-k="cac"] .gp-s').count(), 1, 'with effort beside it');
      assert.deepEqual(await rows(page), ['content', 'outbound', 'community', 'unknown']);
      assert.equal(await word(page, '.gp-ch tbody'), 3, 'each channel: no spend');
      assert.equal(await page.locator('#gr-body .gp-chart .gp-none').count(), 2, 'CAC and Kept have nothing to draw');
      assert.deepEqual(await zeros(page), []);
      await shot(page, 'desktop-growth-tico');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // HotMic: a retention segment, so cost per retained in place of LTV; promotions paid, organic not.
      // The read's Kept may be another window than cost per retained's day 30: the tile shows day 30's.
      const {page, errors} = await open(browser, 'hotmic', {edit: s => { s.reads.all!.headline.kept = {value: 0.9, status: 'ok', window: 'd90'}; }});
      await drawn(page);
      assert.deepEqual(await tabs(page), [['All', 'page'], ['Viewers', null]]);
      assert.deepEqual(await keys(page), ['signups', 'activated', 'paid', 'new', 'cost', 'cac', 'kept', 'per-retained']);
      assert.equal(await page.locator('#gr-body [data-k="cost"] .gp-s').count(), 1, 'retention spend beside the cost');
      assert.match(await page.locator('#gr-body [data-k="kept"] .gp-v').innerText(), /^43%/, "day 30's kept");
      assert.deepEqual(await rows(page), ['organic', 'promotions']);
      assert.equal(await word(page, 'tr[data-channel="organic"]'), 1);
      assert.equal(await word(page, 'tr[data-channel="promotions"]'), 0);
      assert.match(await page.locator('#gr-body .gp-kpis li').innerText(), /%/, 'the KPI in its unit');
      assert.deepEqual(await zeros(page), []);
      await shot(page, 'desktop-growth-hotmic');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A read that fails says so with Try again, which reads once more.
      const {page, errors, reads} = await open(browser, 'tico', {fail: 1});
      await page.locator('#gr-body [data-gr-retry]').click();
      await drawn(page);
      assert.equal(reads.length, 2);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A shared channel's cost is not split to a segment, a paid channel with no customers has no CAC, one month of CAC
      // is a point, and a long KPI name wraps on a phone.
      const edit = (s: Shape) => {
        const all = s.reads.all!;
        all.channels.splice(0, 0, {slug: 'shared', name: 'Shared', new: 3, new_share: 0.1, cost: null, cac: null, status: 'not_split'},
          {slug: 'idle', name: 'Idle', new: 0, new_share: 0, cost: 200, cac: null, cost_share: 0.04, status: 'no_customers'});
        all.series.forEach((p, i) => { if (i < all.series.length - 1) p.cac = null; });
        all.metrics[0]!.name = 'Bookings'.repeat(12);
      };
      const {page, errors} = await open(browser, 'tidy', {viewport: PHONE, edit});
      await drawn(page);
      assert.equal(await word(page, 'tr[data-channel="shared"]'), 2, 'cost and CAC say not split');
      assert.equal(await word(page, 'tr[data-channel="idle"]'), 1, 'CAC says no customers');
      assert.equal(await page.locator('#gr-body .gp-chart svg').count(), 4, 'one month of CAC is a point');
      assert.equal(await sideways(page), false, 'a long KPI name wraps');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const name of ['tidy', 'tico', 'hotmic']) {
      // A phone: nothing past the screen's edge (the channels table scrolls in its own box), in the light theme.
      const {page, errors} = await open(browser, name, {viewport: PHONE, theme: 'light'});
      await drawn(page);
      assert.equal(await sideways(page), false, `${name}: no sideways scroll`);
      const past = await page.evaluate(() => [...(document.querySelectorAll('#main *') as unknown as Iterable<Element>)].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && r.right > innerWidth + 1 && !el.closest('.gp-scroll, .grc-ret');
      }).map(el => el.className || el.tagName).slice(0, 5));
      assert.deepEqual(past, [], `${name}: nothing past the edge`);
      await shot(page, `phone-growth-${name}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Growth page: TIDY (segment tabs, budgets, no spend, month and segment read again, chat item), Tico (no tabs, no spend words, empty charts), HotMic (cost per retained), retry, phone for each');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
