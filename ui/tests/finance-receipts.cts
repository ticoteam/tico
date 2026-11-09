// Receipts above the Transactions list (ui/app/finance-receipts.ts) over a fake hub in backend/finance_routes.py's
// shape for GET /api/v2/finance/receipts and POST /api/v2/finance/actions. Covers: a suggested receipt reads its
// amount, date and vendor and names the bank row it is suggested on, and offers Attach and Refuse; an attached one
// names the row it is filed to and offers Undo; a receipt not read yet says so and offers nothing. Attach posts
// receipt_match with decision accept, Undo posts receipt_match_undo, Refuse posts receipt_match with decision reject,
// and each row takes its new status from the reload. A hub with no receipts draws no section. The receipts sit above
// the transactions table; desktop and 390px with no sideways scroll, and no page errors.
// TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cjs';
import type * as RouteSupport from './support/routes.cjs';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs') as typeof PageSupport;
const {unrouted} = require('./support/routes.cjs') as typeof RouteSupport;
type Row = Record<string, unknown>;
interface ActionBody {kind: string; payload: {receipt_id: string; decision?: string}}
declare const S: {me: {id: string} | null; emps: unknown[] | null};
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const iso = '2026-11-08T12:00:00Z';

const MATE = {teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], attribute: false,
  rights: ['finance.book', 'finance.budgets', 'finance.attribution', 'finance.admin']};
const ACCESS = {teammates: [MATE], teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'},
  view_version: 'v1', can_view_as: false, can_edit_defaults: false};
const feed = (id: string, date: string, amount: number, description: string) => ({id, date, amount, description});
const RECEIPTS = () => [
  {id: 'rc-1', file: {id: 'file-1', name: 'Canva invoice.pdf'}, read: {amount: 1200, date: '2026-11-01', vendor: {id: 'c-canva', name: 'Canva'}},
    match: {feed: feed('feed-1', '2026-11-01', -1200, 'CANVA'), confidence: 0.92}, status: 'suggested', as_of: iso},
  {id: 'rc-2', file: {id: 'file-2', name: 'Meta receipt.pdf'}, read: {amount: 900, date: '2026-10-30', vendor: {id: 'c-meta', name: 'Meta Ads'}},
    match: {feed: feed('feed-2', '2026-10-30', -900, 'META'), confidence: 0.97}, status: 'attached', as_of: iso},
  {id: 'rc-3', file: {id: 'file-3', name: 'Scan 3.png'}, read: {amount: null, date: null, vendor: null}, match: null, status: 'unread', as_of: iso}];

// The hub's answers for one viewer. Accept files the suggestion (attached); Undo returns the attached receipt to its
// suggestion; Refuse marks the receipt refused with no match, as backend/finance/receipts.py does.
function fake(rows: Row[]) {
  const state: {rows: Row[]; writes: {kind: string; payload: unknown}[]} = {rows, writes: []};
  const answer = (p: string, method: string, body: ActionBody | null) => {
    if (p === '/api/v2/finance/receipts' && method === 'GET') return {rows: structuredClone(state.rows), next: null, as_of: iso};
    if (p === '/api/v2/finance/actions' && method === 'POST') {
      state.writes.push({kind: body!.kind, payload: body!.payload});
      const r = state.rows.find(x => x.id === body!.payload.receipt_id);
      if (r && body!.kind === 'receipt_match' && body!.payload.decision === 'accept') r.status = 'attached';
      if (r && body!.kind === 'receipt_match' && body!.payload.decision === 'reject') { r.status = 'rejected'; r.match = null; }
      if (r && body!.kind === 'receipt_match_undo') r.status = 'suggested';
      return {action_id: 'act-' + state.writes.length, kind: body!.kind, status: 'committed', code: null, reason: null, approver: null, result: {}};
    }
    if (p === '/api/v2/finance/access') return structuredClone(ACCESS);
    if (p === '/api/v2/finance/pnl') return {rows: [], columns: [], income: [], expense: [], net: [], excluded: false};
    if (p === '/api/v2/finance/chart') return {accounts: [{id: 'a-6400', name: 'Marketing'}]};
    if (p === '/api/v2/finance/pay-from') return {accounts: [{id: 'm1', name: 'Checking', last4: '0001'}]};
    if (p === '/api/v2/finance/contacts') return {contacts: [{id: 'c-canva', name: 'Canva', kind: 'vendor'}, {id: 'c-meta', name: 'Meta Ads', kind: 'vendor'}], next_cursor: null};
    if (p === '/api/v2/finance/transactions') return {rows: [], next: null};
    return undefined;
  };
  return {state, answer};
}

const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}

// The owner on Transactions, answered by the fake above; the reads list records each GET so a test can wait for one.
async function open(browser: Browser, {viewport, rows}: {viewport: {width: number; height: number}; rows: Row[]}) {
  const f = fake(rows);
  const me = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const reads: string[] = [], errors: string[] = [];
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
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
    if (p === '/api/employees') return json([{name: 'sales', display_name: 'Sales', status: 'active'}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana Silva', role: 'owner'}], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    const answered = f.answer(p, method, body);
    if (answered !== undefined) return json(answered);
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/finance/transactions');
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, reads, f};
}

const buttons = (page: Page, id: string) => page.locator(`[data-fin-receipt="${id}"] button`).allInnerTexts().then(t => t.map(x => x.trim()));
const pill = (page: Page, id: string) => page.locator(`[data-fin-receipt="${id}"] .pill`).innerText();
const lines = (page: Page, id: string) => page.locator(`[data-fin-receipt="${id}"] .muted`).allInnerTexts();
async function waitPill(page: Page, id: string, text: string) {
  await page.waitForFunction(([i, t]) => document.querySelector(`[data-fin-receipt="${i}"] .pill`)?.textContent.trim() === t, [id, text]);
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const [tag, viewport] of [['desktop', DESKTOP], ['phone', PHONE]] as const) {
      const {page, errors, f} = await open(browser, {viewport, rows: RECEIPTS()});
      const rows = page.locator('#fin-receipts .fin-receipt');
      await rows.first().waitFor();
      assert.equal(await rows.count(), 3, `${tag}: three receipts`);
      assert.equal(await page.evaluate(() => document.querySelector('#fin-receipts')!.nextElementSibling?.id), 'fin-tx', `${tag}: the list follows the receipts`);
      assert.equal(await page.locator('[data-fin-receipt="rc-1"] strong').innerText(), 'Canva invoice.pdf', `${tag}: file name`);
      // A suggestion: its read, the bank row it is suggested on, and Attach or Refuse.
      assert.equal(await pill(page, 'rc-1'), 'Suggested match', `${tag}: suggested pill`);
      const r1 = await lines(page, 'rc-1');
      assert.match(r1[0], /^12\.00 · Nov 1(, 2026)? · Canva$/, `${tag}: read line`);
      assert.match(r1[1], /^Suggested on: CANVA · 12\.00 · Nov 1(, 2026)?$/, `${tag}: suggested row line`);
      assert.deepEqual(await buttons(page, 'rc-1'), ['Attach', 'Refuse'], `${tag}: suggestion buttons`);
      // Filed: the row it is filed to, and Undo.
      assert.equal(await pill(page, 'rc-2'), 'Matched', `${tag}: attached pill`);
      assert.match((await lines(page, 'rc-2'))[1], /^Filed to: META · 9\.00 · Oct 30(, 2026)?$/, `${tag}: filed line`);
      assert.deepEqual(await buttons(page, 'rc-2'), ['Undo'], `${tag}: attached buttons`);
      // Not read yet: says so and offers nothing.
      assert.equal(await pill(page, 'rc-3'), 'To read', `${tag}: unread pill`);
      assert.equal((await lines(page, 'rc-3'))[0], 'Not read yet', `${tag}: unread line`);
      assert.deepEqual(await buttons(page, 'rc-3'), [], `${tag}: unread offers nothing`);
      await shot(page, `finance-receipts-${tag}-before`);

      // Attach posts accept with no feed (the suggested row), and the list reloads to Matched.
      await page.locator('[data-fin-receipt="rc-1"] button', {hasText: 'Attach'}).click();
      await waitPill(page, 'rc-1', 'Matched');
      assert.deepEqual(f.state.writes.at(-1), {kind: 'receipt_match', payload: {receipt_id: 'rc-1', decision: 'accept'}}, `${tag}: accept write`);
      assert.deepEqual(await buttons(page, 'rc-1'), ['Undo'], `${tag}: attached after accept`);

      // Undo posts receipt_match_undo; the receipt is suggested again and offers Attach and Refuse.
      await page.locator('[data-fin-receipt="rc-2"] button', {hasText: 'Undo'}).click();
      await waitPill(page, 'rc-2', 'Suggested match');
      assert.deepEqual(f.state.writes.at(-1), {kind: 'receipt_match_undo', payload: {receipt_id: 'rc-2'}}, `${tag}: undo write`);
      assert.deepEqual(await buttons(page, 'rc-2'), ['Attach', 'Refuse'], `${tag}: suggested again after undo`);

      // Refuse posts reject; the receipt reads Refused and offers nothing.
      await page.locator('[data-fin-receipt="rc-2"] button', {hasText: 'Refuse'}).click();
      await waitPill(page, 'rc-2', 'Refused');
      assert.deepEqual(f.state.writes.at(-1), {kind: 'receipt_match', payload: {receipt_id: 'rc-2', decision: 'reject'}}, `${tag}: refuse write`);
      assert.deepEqual(await buttons(page, 'rc-2'), [], `${tag}: refused offers nothing`);

      await shot(page, `finance-receipts-${tag}-after`);
      assert.equal(await sideways(page), false, `${tag}: no sideways scroll`);
      assert.deepEqual(errors, [], `${tag}: page errors`);
      await page.close();
    }

    // A hub with no receipts: the read is made, and no section is drawn above the list.
    {
      const {page, errors, reads} = await open(browser, {viewport: DESKTOP, rows: []});
      await page.waitForFunction(() => document.querySelector('#fin-tx') !== null);
      for (let t = Date.now(); !reads.some(p => p.startsWith('/api/v2/finance/receipts')) && Date.now() - t < 3000;) await new Promise(r => setTimeout(r, 20));
      assert.ok(reads.some(p => p.startsWith('/api/v2/finance/receipts')), 'no receipts: the list is read');
      await new Promise(r => setTimeout(r, 300));
      assert.equal(await page.locator('#fin-receipts').innerHTML(), '', 'no receipts: nothing drawn');
      assert.deepEqual(errors, [], 'no receipts: page errors');
      await page.close();
    }
    console.log('Finance receipts: suggested, filed and unread rows; attach, undo and refuse post and reload; no receipts draws nothing; desktop and phone');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
