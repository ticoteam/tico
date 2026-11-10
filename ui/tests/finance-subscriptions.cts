// Subscriptions under the P&L table (ui/app/finance-subscriptions.ts) against a fake hub: a suggested, an active and an
// ignored subscription with their monthly cost, cadence, next charge and seats; the monthly total counts the active ones
// only; the owner (who holds finance.book) sees Confirm and Ignore on the suggestion, and Confirm posts subscription_confirm
// then the section reads again with the new total; with no subscriptions the section is hidden. Desktop and 390px, with no
// sideways scroll. TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
declare const S: {me: unknown; emps: unknown[]};
interface Sub {id: string; vendor: {id: string; name: string}; status: string; cadence: string; amount: number; monthly: number; seats: number | null; next_charge_on: string | null; card: {id: string; name: string} | null; attribution: unknown[]; source: string; as_of: string}
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const iso = '2026-11-05T12:00:00Z';
const ALL_RIGHTS = ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards', 'finance.budgets', 'finance.rules', 'finance.admin'];
const access = {teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: [{teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ALL_RIGHTS}],
  can_view_as: false, can_edit_defaults: true};
const SUBS: Sub[] = [
  {id: 'sub-1', vendor: {id: 'figma', name: 'Figma'}, status: 'suggested', cadence: 'monthly', amount: 1500, monthly: 1500, seats: null,
    next_charge_on: '2026-12-05', card: {id: 'card_a', name: 'Card ending 4242'}, attribution: [], source: 'detected', as_of: iso},
  {id: 'sub-2', vendor: {id: 'adobe', name: 'Adobe'}, status: 'active', cadence: 'annual', amount: 12000, monthly: 1000, seats: 5,
    next_charge_on: '2027-01-10', card: null, attribution: [], source: 'detected · confirmed by human:ana', as_of: iso},
  {id: 'sub-3', vendor: {id: 'old-tool', name: 'Old tool'}, status: 'ignored', cadence: 'monthly', amount: 900, monthly: 900, seats: null,
    next_charge_on: null, card: null, attribution: [], source: 'detected · confirmed by human:ana', as_of: iso},
];

// The read as GET /api/v2/finance/subscriptions sends it (backend/finance/subscriptions.py read): rows, and the monthly
// total of the active ones.
const readSubs = (rows: Sub[]) => ({rows: structuredClone(rows), monthly_total: rows.filter((r: Sub) => r.status === 'active').reduce((a: number, r: Sub) => a + r.monthly, 0), as_of: iso});

// The P&L read for one month, as the P&L page reads it (one expense line).
function pnl(url: URL) {
  const q = url.searchParams, start = q.get('start') || '2026-11-01', end = q.get('end') || '2026-11-30';
  const cols = [{start, end, label: start.slice(0, 7), state: 'current'}];
  const rows = [{account_id: 'a-6400', code: '6400', name: 'Software', type: 'expense', section: 'operating', parent_id: null, private: false,
    actual: [30000], total: 0, budget: null, budget_to_date: null, projection: [null], projection_method: 'trend'}];
  return {columns: cols, rows, income: [0], expense: [30000], net: [-30000], excluded: false, start, end, view_version: 'v1',
    imported: [], snapshot: null, filter: {teams: [], teammates: [], who: 'both', unattributed: false}};
}

async function open(browser: Browser, {viewport = DESKTOP, subs = SUBS, hash = '#/finance/pnl'} = {}) {
  const me = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const writes: unknown[] = [], reads: string[] = [], state = {subs: structuredClone(subs)};
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method === 'GET' && p.startsWith('/api/v2/')) reads.push(p + url.search);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', role: 'Sales', team: '', status: 'active', host: 'keeper', can_chat: true}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', role: 'owner'}], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json(access);
    if (p === '/api/v2/finance/pnl') return json(pnl(url));
    if (p === '/api/v2/finance/budgets') return json({budgets: [], more: false, on: url.searchParams.get('at'), excluded: false});
    if (p === '/api/v2/finance/sources') return json({groups: []});
    if (p === '/api/v2/finance/balance-sheet') return json({assets: [], liabilities: [], equity: []});
    if (p === '/api/v2/finance/subscriptions' && method === 'GET') return json(readSubs(state.subs));
    if (p === '/api/v2/finance/actions' && method === 'POST') {
      writes.push(body);
      if (body.kind === 'subscription_confirm') {
        const row = state.subs.find(r => r.id === body.payload.subscription_id);
        if (row) row.status = body.payload.status;
      }
      return json({action_id: 'act-' + writes.length, kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}});
    }
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes, reads};
}

const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
const cell = (page: Page, id: string, n: number) => page.locator(`#fin-subs tr[data-fin-sub="${id}"] td`).nth(n);
async function settled(done: () => boolean) {
  for (let t = Date.now(); !done() && Date.now() - t < 5000;) await new Promise(r => setTimeout(r, 20));
  assert.ok(done(), 'timed out waiting');
  await new Promise(r => setTimeout(r, 100));
}
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({animations: 'disabled', path: path.join(dir, `finance-subscriptions-${name}.png`), fullPage: true});
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const width of [1440, 390]) {
      const tag = width < 500 ? 'phone' : 'desktop';
      const {page, errors, writes} = await open(browser, {viewport: width < 500 ? PHONE : DESKTOP});
      const sec = page.locator('#fin-subs');
      await sec.locator('tr[data-fin-sub]').first().waitFor();
      assert.equal(await sec.locator('tr[data-fin-sub]').count(), 3, `${tag}: one row per subscription`);
      // The heading's total counts the active subscriptions only ($10 a month: Adobe's twelfth). finUsd drops the cents on whole dollars.
      assert.match(await sec.locator('h2').textContent(), /Subscriptions\s*\$10 \/ mo/, `${tag}: monthly total`);
      assert.deepEqual([await cell(page, 'sub-1', 0).innerText(), await cell(page, 'sub-1', 1).innerText(), await cell(page, 'sub-1', 2).innerText(), await cell(page, 'sub-1', 4).innerText()],
        ['Figma', '$15', 'Monthly', '—'], `${tag}: suggested row figures`);
      assert.deepEqual([await cell(page, 'sub-2', 1).innerText(), await cell(page, 'sub-2', 2).innerText(), await cell(page, 'sub-2', 4).innerText()],
        ['$10', 'Annual', '5'], `${tag}: annual row figures`);
      // The owner may confirm: the suggestion has Confirm and Ignore; the active one has its pill and no buttons.
      assert.equal(await page.locator('#fin-subs tr[data-fin-sub="sub-1"] button[data-fin-sub-status="active"]').count(), 1, `${tag}: Confirm on the suggestion`);
      assert.equal(await page.locator('#fin-subs tr[data-fin-sub="sub-1"] button[data-fin-sub-status="ignored"]').count(), 1, `${tag}: Ignore on the suggestion`);
      assert.equal(await page.locator('#fin-subs tr[data-fin-sub="sub-2"] button').count(), 0, `${tag}: no buttons on an active row`);
      assert.equal((await page.locator('#fin-subs tr[data-fin-sub="sub-2"] .pill').textContent())!.trim(), 'Active', `${tag}: active pill`);
      await shot(page, `${tag}-before`);
      // Confirm posts subscription_confirm, and the section reads again: Figma is active and the total counts it.
      await page.locator('#fin-subs tr[data-fin-sub="sub-1"] button[data-fin-sub-status="active"]').click();
      await page.waitForFunction(() => document.querySelector('#fin-subs tr[data-fin-sub="sub-1"] .pill')?.textContent?.trim() === 'Active');
      assert.deepEqual(writes.at(-1), {kind: 'subscription_confirm', payload: {subscription_id: 'sub-1', status: 'active'}}, `${tag}: confirm write`);
      assert.match(await sec.locator('h2').textContent(), /\$25 \/ mo/, `${tag}: total after confirm`);
      await shot(page, `${tag}-after`);
      assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
      assert.deepEqual(errors, [], `${tag}: page errors`);
      await page.close();
    }
    // With no subscriptions the section stays hidden, and nothing is drawn into it.
    {
      const {page, errors, reads} = await open(browser, {subs: []});
      await settled(() => reads.some(r => r.startsWith('/api/v2/finance/subscriptions')));
      assert.equal(await page.locator('#fin-subs').isHidden(), true, 'empty: section hidden');
      assert.equal(await page.locator('#fin-subs tr').count(), 0, 'empty: no rows');
      assert.deepEqual(errors, [], 'empty: page errors');
      await page.close();
    }
    console.log('Finance subscriptions: rows under the P&L table, the total counts the active ones, Confirm posts and reloads, hidden when empty, phone width');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
