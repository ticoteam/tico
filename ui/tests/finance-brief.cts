// Finance › What changed (ui/app/finance-brief.ts) against a fake hub: the owner's Finance page opens with the brief's
// lines under a "What changed" heading and the time they were last worked out; a brief with no lines leaves the slot
// empty; a failed read leaves the page working. Phone width: no sideways scroll. The API is faked: GET
// /api/v2/finance/brief answers with the lines the test sets. TICO_UI_SHOTS=<dir> saves screenshots.
import type {Browser, Page, Route} from 'playwright';
import type * as PageSupport from './support/page.cts';
import type * as RouteSupport from './support/routes.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as RouteSupport.Api;
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const LINES = [
  'Budget: Meta Ads · $3,000. Spent $760 (25%). Pace $3,800, over budget.',
  'Team: Marketing · $9,000. Spent $1,590. Pace $7,950.',
  'Cash $412k. Burn $61k this month.',
  'Unattributed: $1,940 (23 lines).',
];
const AS_OF = new Date(Date.now() - 40 * 60e3).toISOString();

const access = {
  teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: [{teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ['finance.book'], budgets: 0}],
  can_view_as: false, can_edit_defaults: false,
};

// `brief`: the body GET /api/v2/finance/brief answers with, or 'fail' for a 500.
interface Brief {lines: string[]; as_of: string; changed_since: string | null; version: number}
declare const S: {me?: unknown};

async function open(browser: Browser, {viewport = DESKTOP, brief = {lines: LINES, as_of: AS_OF, changed_since: null, version: 1}}: {viewport?: {width: number; height: number}; brief?: Brief | 'fail'} = {}) {
  const me = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', (e: Error) => errors.push(e.message));
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [me], org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json(access);
    if (p === '/api/v2/finance/brief') return brief === 'fail' ? json({error: {code: 'unavailable', detail: 'down'}}, 500) : json(brief);
    if (p === '/api/v2/finance/balance-sheet') return json({assets: [], liabilities: [], equity: []});
    if (p === '/api/v2/finance/pnl') return json({columns: [], rows: [], income: [], expense: [], net: [], excluded: false, view_version: 'v1', imported: [], snapshot: null, filter: {teams: [], teammates: [], who: 'both', unattributed: false}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/#/finance/pnl');
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors};
}

const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
async function shot(page: Page, name: string) {
  const dir = process.env.TICO_UI_SHOTS;
  if (dir) await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch();
  try {
    {
      const {page, errors} = await open(browser);
      const block = page.locator('#fin-brief .fin-brief');
      await block.waitFor();
      assert.equal(await block.locator('h2').innerText(), 'What changed', 'the heading');
      assert.deepEqual(await block.locator('li').allInnerTexts(), LINES, 'the lines, as the server cut them');
      assert.match(await block.locator('.fin-brief-at').innerText(), /^as of \d{1,2}:\d{2}/, 'the time they were worked out');
      assert.deepEqual(errors, [], 'no page errors');
      await shot(page, 'finance-brief');
      await page.close();
    }
    {
      // No lines: the slot stays empty and the page is unchanged.
      const {page, errors} = await open(browser, {brief: {lines: [], as_of: AS_OF, changed_since: null, version: 1}});
      await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('#fin-brief .fin-brief').count(), 0, 'hidden when empty');
      assert.equal(await page.locator('#fin-brief').innerHTML(), '', 'the slot is empty');
      assert.deepEqual(errors, [], 'no page errors when empty');
      await page.close();
    }
    {
      // A failed read: the page still works and no block is drawn.
      const {page, errors} = await open(browser, {brief: 'fail'});
      await page.locator('#fin-head [data-fin-tab]').first().waitFor();
      await page.waitForLoadState('networkidle');
      assert.equal(await page.locator('#fin-brief .fin-brief').count(), 0, 'no block when the read fails');
      assert.deepEqual(errors, [], 'no page errors when the read fails');
      await page.close();
    }
    {
      // Phone width: the block fits, no sideways scroll.
      const {page, errors} = await open(browser, {viewport: PHONE});
      await page.locator('#fin-brief .fin-brief').waitFor();
      assert.equal(await sideways(page), false, 'no sideways scroll on a phone');
      assert.deepEqual(errors, [], 'no page errors on a phone');
      await shot(page, 'finance-brief-phone');
      await page.close();
    }
    console.log('finance-brief: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
