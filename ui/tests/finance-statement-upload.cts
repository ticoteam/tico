// Finance › Sources, Upload statement (ui/app/finance-statement-upload.ts) against a fake hub: a bank account whose books are
// $60.00 short of its statement shows "Off by $60.00" on its row; Upload statement on its rail picks a file, previews it
// (the tie-out, the rows with New and Already in Tico, the warning) and Post sends statement_post with the preview's token;
// Cancel closes the review without a write; a card account offers the same; the "Charges show positive" box previews the
// file again read the other way and the post sends that choice; no sideways scroll on a phone.
import type {Browser, Page} from 'playwright';
// The request bodies this spec's fake hub reads and checks.
interface Body {kind?: string; signs?: string; money_account_id?: string; payload?: {money_account_id: string; preview_token: string; file: string; signs: string}}
type Write = [string, string, Body];
// Page global the open-page wait reads (declared here: each spec is its own module).
declare var S: {me?: unknown; emps?: unknown[]};
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts');
const {unrouted} = require('./support/routes.cts');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};
const ALL_RIGHTS = ['finance.book', 'finance.ap', 'finance.ar', 'finance.pay', 'finance.cards', 'finance.budgets', 'finance.rules', 'finance.admin'];

const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}];
const emps = [{name: 'rental-mgr', display_name: 'Rental Marketing Manager', role: 'Rental Marketing Manager', team: '', status: 'active', host: 'keeper', can_chat: true}];
const access = {teams: [], private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: [{teammate: 'human:ana', kind: 'person', team: '', reports_to: '', default: 'everything', grants: [], rights: ALL_RIGHTS}],
  can_view_as: false, can_edit_defaults: false};
const BANK = {kind: 'bank', id: 'm-chase', name: 'Chase Checking', provider: '', last4: '2210', account: {id: 'a-1000', name: 'Cash', code: '1000'},
  balance: 1360777, balance_as_of: '2026-10-31', sync: null, access: [], off_by: -6000};
const CARD = {kind: 'card_program', id: 'm-amex', name: 'Amex Gold', provider: '', last4: '5555', account: {id: 'a-2100', name: 'Credit card', code: '2100'},
  balance: 123456, balance_as_of: '2026-10-31', sync: null, access: [], off_by: null};
const SOURCES = {groups: [{group: 'banks', name: 'Banks', count: 1, rows: [BANK]}, {group: 'cards', name: 'Cards', count: 1, rows: [CARD]}]};
const FILE_TEXT = 'OFXHEADER:100\n<OFX>chase-oct</OFX>\n';
// The preview a post shows: the books $60.00 short of the statement (ends at $13,607.77, the statement says $13,667.77).
const REVIEW = {token: 'tok-1', expires_at: '2026-10-15T12:30:00.000000Z', money_account_id: 'm-chase', file_name: 'chase-oct.ofx', file_format: 'ofx',
  start: '2026-10-01', end: '2026-10-31', card: false, last4: '2210', signs: {flipped: false, by: 'file'}, new: 2, duplicate: 1,
  tie: {opening: 1248022, added: 810000, removed: 697245, ends: 1360777, statement_says: 1366777, off_by: -6000},
  rows: [
    {id: '20261002001', date: '2026-10-02', description: 'ADOBE *CREATIVE CLD', amount: -5999, vendor: {id: '', name: 'Adobe'}, attribution: {account: 'Design', rule: 'Adobe'}, status: 'new'},
    {id: '20261003001', date: '2026-10-03', description: 'STRIPE TRANSFER', amount: 421280, vendor: {id: '', name: 'Stripe payout'}, attribution: null, status: 'duplicate'},
    {id: '20261009001', date: '2026-10-09', description: 'HOME DEPOT #4410', amount: -31840, vendor: {id: '', name: 'Home Depot'}, attribution: null, status: 'new'},
  ]};

async function open(browser: Browser, {viewport = DESKTOP, hash = '#/finance/sources'}: {viewport?: {width: number; height: number}; hash?: string} = {}) {
  const me = {id: 'ana', name: 'Ana Silva', email: 'ana@acme.example', role: 'owner', company_role: 'owner', cloud: true};
  const config = {company_name: 'Acme Lights'};
  const writes: Write[] = [];
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: /\.(?:js|ts)$/.test(file) ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = (method === 'GET' ? null : req.postDataJSON?.() ?? null) as Body;
    if (method !== 'GET') writes.push([method, p + url.search, body]);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json(access);
    if (p === '/api/v2/finance/sources') return json(SOURCES);
    if (p === '/api/v2/finance/balance-sheet') return json({assets: [], liabilities: [], equity: []});
    if (p === '/api/v2/finance/statements/preview' && method === 'POST') {
      const flipped = body.signs === 'flipped', card = body.money_account_id === 'm-amex';
      return json({...REVIEW, money_account_id: body.money_account_id, card, signs: {flipped, by: body.signs === 'auto' ? 'file' : 'chosen'}});
    }
    if (p === '/api/v2/finance/actions' && method === 'POST' && body.kind === 'statement_post') {
      return json({action_id: 'act-post', kind: 'statement_post', status: 'committed', code: null, reason: null, approver: null,
        result: {upload_id: 'up-1', new: 2, duplicate: 1, skipped: 0, off_by: -6000, statement_id: null}});
    }
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-x', kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes};
}

// Opens the row's group if it is shut, selects the row, and opens its Upload statement dialog from the rail.
async function openUpload(page: Page, row = 'bank:m-chase', group = 'banks') {
  if (!(await page.locator(`tr[data-fin-src="${row}"]`).count())) await page.locator(`[data-fin-group="${group}"]`).click();
  await page.locator(`tr[data-fin-src="${row}"] .fin-src-name`).click();
  await page.locator('[data-fin-statement]').click();
  await page.locator('dialog.fin-dlg input[type=file]').waitFor();
}

async function previewFile(page: Page) {
  await page.locator('dialog.fin-dlg input[type=file]').setInputFiles({name: 'chase-oct.ofx', mimeType: 'application/x-ofx', buffer: Buffer.from(FILE_TEXT)});
  await page.locator('dialog.fin-dlg [type=submit]').click();
  await page.locator('dialog.fin-dlg [data-fin-stmt-review]:not([hidden])').waitFor();
}

const sideways = (page: Page) => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));

(async () => {
  const browser = await chromium.launch();
  try {
    {
      const {page, errors, writes} = await open(browser);
      const tag = 'desktop';
      await page.locator('[data-fin-group="banks"]').click();
      const offPill = page.locator('tr[data-fin-src="bank:m-chase"] .pill.needs');
      assert.equal((await offPill.innerText()).trim(), 'Off by $60.00', tag + ': the row shows the gap');
      assert.equal(await page.locator('tr[data-fin-src="bank:m-chase"] .pill.needs').count(), 1, tag + ': one warning pill');
      await openUpload(page);
      assert.equal(await page.locator('dialog.fin-dlg').getAttribute('aria-label'), 'Upload statement', tag + ': titled');
      await previewFile(page);
      const head = (await page.locator('dialog.fin-dlg .fin-stmt-head').innerText()).trim();
      assert.equal(head, 'Chase Checking ··2210 · October 2026 · chase-oct.ofx', tag + ': header line');
      const tie = (await page.locator('dialog.fin-dlg .fin-stmt-tie').innerText()).replace(/\s+/g, ' ');
      for (const part of ['Opening $12,480.22', '+ In $8,100.00', '− Out $6,972.45', 'Ends at $13,607.77', 'Statement says $13,667.77', '$60.00 off']) {
        assert.ok(tie.includes(part), `${tag}: tie-out shows ${part}: ${tie}`);
      }
      assert.equal(await page.locator('dialog.fin-dlg .fin-stmt-t tbody tr').count(), 3, tag + ': every row listed');
      assert.equal(await page.locator('dialog.fin-dlg .fin-stmt-t .pill.ok').count(), 2, tag + ': two New rows');
      assert.equal((await page.locator('dialog.fin-dlg .fin-stmt-t tbody tr').nth(1).innerText()).includes('Already in Tico'), true, tag + ': duplicate pill');
      assert.equal((await page.locator('dialog.fin-dlg [type=submit]').innerText()).trim(), 'Post 2 rows', tag + ': post count is the New rows');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('dialog.fin-dlg').waitFor({state: 'detached'});
      const post = writes.find(w => w[1] === '/api/v2/finance/actions' && w[2]?.kind === 'statement_post');
      assert.ok(post, tag + ': statement_post sent');
      assert.equal(post![2].payload!.money_account_id, 'm-chase', tag + ': to the account');
      assert.equal(post![2].payload!.preview_token, 'tok-1', tag + ': with the preview token');
      assert.equal(post![2].payload!.file, Buffer.from(FILE_TEXT).toString('base64'), tag + ': with the file');
      assert.equal(writes.filter(w => w[1] === '/api/v2/finance/statements/preview').length, 1, tag + ': previewed once');
      assert.deepEqual(errors, [], tag + ': no page errors');
      await page.close();
    }
    {
      const {page, errors, writes} = await open(browser);
      await openUpload(page);
      await previewFile(page);
      await page.locator('dialog.fin-dlg .fin-foot [data-close]').click();
      await page.locator('dialog.fin-dlg').waitFor({state: 'detached'});
      assert.equal(writes.some(w => w[2]?.kind === 'statement_post'), false, 'cancel: nothing posted');
      assert.deepEqual(errors, [], 'cancel: no page errors');
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {viewport: PHONE});
      await openUpload(page);
      await previewFile(page);
      assert.equal(await sideways(page), false, 'phone: no sideways scroll on the review');
      assert.deepEqual(errors, [], 'phone: no page errors');
      await page.close();
    }
    console.log('finance-statement-upload: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
