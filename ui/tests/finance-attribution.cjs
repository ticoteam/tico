// Finance attribution in the UI (ui/app/finance-attribution.ts, finance-books.js) against a fake hub: Acme Lights, with
// a split on a bank row (chips, + for the rest, the unattributed share), the rail's "Attributed to" with Edit, Undo and
// History, and the Attribution tab with its rules. Edit posts attribute with share_bps (50% → 5000) and refuses a total
// over 100% before anything is sent; Clear posts splits []; Undo posts attribute_undo; the rules' On switch posts
// attribution_rule_set with enabled false; Delete asks in the menu first, then posts attribution_rule_delete. The tab
// is hidden (and its address redirects) from a teammate without finance.attribution. Phone width: no sideways scroll.
// TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const uiDir = path.join(__dirname, '..');
const PHONE = {width: 390, height: 844}, DESKTOP = {width: 1440, height: 900};

const people = [{id: 'ana', name: 'Ana Silva', role: 'owner'}, {id: 'mia', name: 'Mia Park', role: 'member', team: 'mkt'}, {id: 'raj', name: 'Raj Rao', role: 'member', team: 'mkt'}];
const emps = [['rental-mgr', 'Rental Marketing Manager', 'rental'], ['rental-google', 'Rental Google Ad Owner', 'rental'],
  ['consumer-google', 'Consumer Google Ad Owner', 'consumer']].map(([name, display_name, team]) =>
  ({name, display_name, role: display_name, team, status: 'active', host: 'keeper', can_chat: true}));
const teams = [{id: 'mkt', name: 'Marketing', parent: '', lead: 'human:ana'}, {id: 'rental', name: 'Rental', parent: 'mkt', lead: 'bot:rental-mgr'},
  {id: 'consumer', name: 'Consumer', parent: 'mkt', lead: 'bot:rental-mgr'}];
const mate = (teammate, kind, team, def, extra = {}) => ({teammate, kind, team, reports_to: '', default: def, grants: [], rights: [], ...extra});
const MATES = [mate('human:ana', 'person', '', 'everything', {rights: ['finance.book', 'finance.budgets', 'finance.attribution', 'finance.admin']}),
  mate('human:mia', 'person', 'mkt', 'own_expenses', {rights: ['finance.budgets']}),
  mate('human:raj', 'person', 'mkt', 'none', {rights: ['finance.attribution'], attribute: true}),
  ...emps.map(e => mate('bot:' + e.name, 'bot', e.team, 'own_expenses'))];
const access = viewer => ({teams, private_lines: [], defaults: {manager: 'team_expenses', member: 'own_expenses', manager_payroll: 'totals'}, view_version: 'v1',
  teammates: viewer === 'ana' ? MATES : MATES.filter(m => m.teammate === 'human:' + viewer), can_view_as: viewer === 'ana', can_edit_defaults: viewer === 'ana'});

// Bank rows: f1 split 50% Rental Google, 30% Consumer, the rest unattributed; f3 split four ways (two shown, +2);
// f2 has no split, only its teammate tag, so its cell reads as before.
const TX = [
  {kind: 'feed', id: 'f1', date: '2026-10-29', amount: -300000, currency: 'USD', counterparty: 'Google Ads', description: 'GOOGLE ADS', status: 'posted', pending: false,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, split: [{teammate: 'bot:rental-google', share_bps: 5000}, {team: 'consumer', share_bps: 3000}],
    lines: [{line_id: 'l1', account_id: 'a-6400', account: 'Marketing', amount: 300000, vendor: 'Google Ads', tags: {}}]},
  {kind: 'feed', id: 'f3', date: '2026-10-28', amount: -100000, currency: 'USD', counterparty: 'Meta Ads', description: 'META', status: 'posted', pending: false,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'},
    split: [{teammate: 'bot:rental-google', share_bps: 2500}, {teammate: 'bot:consumer-google', share_bps: 2500}, {team: 'rental', share_bps: 2500}, {team: 'consumer', share_bps: 2500}],
    lines: [{line_id: 'l3', account_id: 'a-6400', account: 'Marketing', amount: 100000, vendor: 'Meta Ads', tags: {}}]},
  {kind: 'feed', id: 'f2', date: '2026-10-27', amount: -9900, currency: 'USD', counterparty: 'Figma', description: 'FIGMA', status: 'posted', pending: false,
    money_account: {id: 'm1', name: 'Checking', last4: '0001'}, lines: [{line_id: 'l2', account_id: 'a-6400', account: 'Marketing', amount: 9900, vendor: 'Figma', tags: {teammate: 'bot:rental-mgr'}}]}];

const ATTR = {
  l1: {splits: [{teammate: 'bot:rental-google', share_bps: 5000, amount: 150000}, {team: 'consumer', share_bps: 3000, amount: 90000}], source: 'person',
    set_by: 'human:ana', set_at: '2026-11-05T10:00:00Z', can_change: true,
    history: [{splits: [{teammate: 'bot:rental-google', share_bps: 5000}, {team: 'consumer', share_bps: 3000}], source: 'person', set_by: 'human:ana', set_at: '2026-11-05T10:00:00Z'},
      {splits: [{teammate: 'bot:rental-google', share_bps: 10000}], source: 'rule', set_by: 'human:ana', set_at: '2026-11-04T09:00:00Z'}]},
  l3: {splits: [{teammate: 'bot:rental-google', share_bps: 2500}, {teammate: 'bot:consumer-google', share_bps: 2500}, {team: 'rental', share_bps: 2500}, {team: 'consumer', share_bps: 2500}],
    source: 'rule', rule_id: 'r1', set_at: '2026-11-04T09:00:00Z', can_change: false, history: []}};
const RULES = [{id: 'r1', name: 'Google Ads to Rental', match: {kind: 'vendor', id: 'c-google', label: 'Google Ads'}, splits: [{teammate: 'bot:rental-google', share_bps: 10000, team: ''}],
  enabled: true, apply_from: null, created_by: 'human:ana', updated_at: '2026-11-04T09:00:00Z'},
{id: 'r2', name: 'Card 0001 split', match: {kind: 'money_account', id: 'm1', label: 'Checking ••0001'}, splits: [{team: 'rental', share_bps: 6000}, {team: 'consumer', share_bps: 4000}],
  enabled: false, apply_from: '2026-11-01', created_by: 'human:ana', updated_at: '2026-11-03T09:00:00Z'}];

async function open(browser, {viewport = DESKTOP, viewer = 'ana', hash = '#/finance/transactions'} = {}) {
  const person = people.find(p => p.id === viewer);
  const me = {id: viewer, name: person.name, email: viewer + '@acme.example', role: person.role, company_role: person.role, cloud: true};
  const config = {company_name: 'Acme Lights'};
  const writes = [], reads = [];
  const page = await browser.newPage({viewport, serviceWorkers: 'block', hasTouch: viewport === PHONE});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const file = p.startsWith('/tico/ui/') ? uiFile(p.slice(9)) : p.startsWith('/vendor/') ? path.join(uiDir, p) : '';
    if (file && fs.existsSync(file)) return route.fulfill({contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.woff2') ? 'font/woff2' : 'application/octet-stream', body: fs.readFileSync(file)});
    if (p === '/api/v2/events') return;
    const body = method === 'GET' ? null : req.postDataJSON?.() ?? null;
    if (method !== 'GET') writes.push([method, p + url.search, body]);
    else if (p.startsWith('/api/v2/')) reads.push(p + url.search);
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/employees') return json(emps);
    if (p === '/api/humans') return json({people, org_groups: []});
    if (p === '/api/status') return json({active: [], employees: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: [], version: 'v0', count: 0, next: null});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/finance/access') return json(access(viewer));
    if (p === '/api/v2/finance/pnl') return json({rows: [], columns: [], income: [], expense: [], net: [], excluded: false});
    if (p === '/api/v2/finance/chart') return json({accounts: [{id: 'a-6400', name: 'Marketing'}]});
    if (p === '/api/v2/finance/pay-from') return json({accounts: [{id: 'm1', name: 'Checking', last4: '0001'}]});
    if (p === '/api/v2/finance/contacts') return json({contacts: [{id: 'c-google', name: 'Google Ads', kind: 'vendor'}, {id: 'c-meta', name: 'Meta Ads', kind: 'vendor'}], next_cursor: null});
    if (p === '/api/v2/finance/transactions') return json({rows: TX, next: null});
    if (p.startsWith('/api/v2/finance/lines/') && p.endsWith('/attribution') && method === 'GET') { const a = ATTR[decodeURIComponent(p.split('/').slice(-2)[0])]; return a ? json(a) : json({detail: 'not found'}, 404); }
    if (p === '/api/v2/finance/attribution-rules' && method === 'GET') return json({rules: viewer === 'ana' ? RULES : [], can_change: viewer === 'ana'});
    if (p === '/api/v2/finance/actions' && method === 'POST') return json({action_id: 'act-' + writes.length, kind: body.kind, status: 'committed', code: null, reason: null, approver: null, result: {}});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  await page.waitForFunction(() => typeof S !== 'undefined' && S.me && (S.emps || []).length);
  await page.locator('#fin-head [data-fin-tab]').first().waitFor();
  return {page, errors, writes, reads};
}

const tabs = page => page.locator('#fin-head [data-fin-tab]').allInnerTexts();
const sideways = page => page.evaluate(() => [document.documentElement, document.querySelector('#main')].some(el => el && el.scrollWidth > el.clientWidth + 1));
// The actions a page sent (not dry runs): {kind, payload} in order.
const sent = writes => writes.filter(w => w[1] === '/api/v2/finance/actions').map(w => w[2]).filter(b => b && !b.dry_run).map(b => ({kind: b.kind, payload: b.payload}));
const cellOf = (page, id, n) => page.locator(`tr[data-fin-tx="${id}"] td`).nth(n);
async function settled(done) {
  for (let t = Date.now(); !done() && Date.now() - t < 3000;) await new Promise(r => setTimeout(r, 20));
  assert.ok(done(), 'timed out waiting');
  await new Promise(r => setTimeout(r, 100));
}
async function shot(page, name) {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await page.screenshot({path: path.join(dir, name + '.png'), fullPage: true});
}

(async () => {
  const browser = await chromium.launch();
  try {
    // The owner on Transactions: chips on the split rows, the rail's section, Edit, Undo and Clear, and the refusal.
    {
      const tag = 'transactions';
      const {page, errors, writes} = await open(browser);
      await page.locator('tr[data-fin-tx="feed:f1"]').waitFor();
      assert.equal((await cellOf(page, 'feed:f1', 5).locator('.fin-chip').allInnerTexts()).map(t => t.trim()).join(' '),
        'Rental Google Ad Owner 50% Consumer 30% 20% unattributed', tag + ': split chips, the unattributed share last');
      assert.equal((await cellOf(page, 'feed:f3', 5).locator('.fin-chip').allInnerTexts()).map(t => t.trim()).join(' '), 'Rental Google Ad Owner 25% Consumer Google Ad Owner 25% +2',
        tag + ': two parts, then a +n chip for the rest');
      assert.equal(await cellOf(page, 'feed:f3', 5).locator('.fin-chip-muted').count(), 0, tag + ': a whole split shows no unattributed chip');
      assert.equal((await cellOf(page, 'feed:f2', 5).innerText()).trim(), 'Rental Marketing Manager', tag + ': a row without a split reads its tag');
      assert.equal(await cellOf(page, 'feed:f3', 5).getAttribute('title'), 'Rental Google Ad Owner 25% · Consumer Google Ad Owner 25% · Rental 25% · Consumer 25%',
        tag + ': the full split is in the title');

      await page.locator('tr[data-fin-tx="feed:f1"] td').first().click();
      await page.locator('[data-share-line="l1"]').waitFor();
      const sec = page.locator('[data-share-line="l1"]');
      assert.equal(await sec.locator('h3').innerText(), 'Attributed to', tag + ': the rail section');
      assert.deepEqual(await sec.locator('.fin-kv').evaluateAll(els => els.map(e => [...e.children].map(c => c.textContent.trim()).join(' '))),
        ['Rental Google Ad Owner 50% · $1,500', 'Consumer 30% · $900', 'Unattributed 20%'], tag + ': each part with its share and amount');
      assert.match(await sec.locator('p.muted').innerText(), /^Set by Ana Silva · Nov 5/, tag + ': the source and when');
      assert.equal(await sec.locator('[data-share-edit="l1"]').count(), 1, tag + ': Edit when the viewer can change it');
      assert.equal(await sec.locator('[data-share-undo="l1"]').count(), 1, tag + ': Undo with more than one history entry');
      assert.equal(await sec.locator('details summary').innerText(), 'History', tag + ': History is collapsed');
      await page.locator('[data-share-line="l1"] details summary').click();
      assert.match(await sec.locator('details').innerText(), /Rental Google Ad Owner 100%/, tag + ': the earlier entry in History');

      // Edit: rows prefilled, a third row to a total of 100%, Save sends share_bps.
      await sec.locator('[data-share-edit="l1"]').click();
      const dlg = page.locator('dialog.fin-dlg');
      await dlg.waitFor();
      assert.equal(await dlg.locator('[data-share-row]').count(), 2, tag + ': one row per part');
      await dlg.locator('[data-share-add]').click();
      await dlg.locator('[data-share-row] select').nth(2).selectOption('team:rental');
      await dlg.locator('[data-share-row] input').nth(2).fill('20');
      assert.equal((await dlg.locator('[data-share-total]').innerText()).trim(), 'Unattributed 0%', tag + ': the running total');
      await dlg.locator('[type=submit]').click();
      await settled(() => sent(writes).length === 1);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribute', payload: {line_id: 'l1', splits: [{teammate: 'bot:rental-google', share_bps: 5000},
        {team: 'consumer', share_bps: 3000}, {team: 'rental', share_bps: 2000}]}}, tag + ': 50 / 30 / 20 sent as basis points');

      // A total over 100% is refused in the dialog, and nothing is sent.
      await sec.locator('[data-share-edit="l1"]').click();
      await dlg.waitFor();
      await dlg.locator('[data-share-row] input').nth(1).fill('80');
      await dlg.locator('[type=submit]').click();
      await dlg.locator('[data-error]').filter({hasText: 'over 100%'}).waitFor();
      assert.equal(sent(writes).length, 1, tag + ': an over-100% split sends nothing');
      await dlg.locator('[data-close]').first().click();

      // Clear removes the override.
      await sec.locator('[data-share-edit="l1"]').click();
      await dlg.waitFor();
      await dlg.locator('[data-share-clear]').click();
      await settled(() => sent(writes).length === 2);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribute', payload: {line_id: 'l1', splits: []}}, tag + ': Clear sends no splits');

      await sec.locator('[data-share-undo="l1"]').click();
      await settled(() => sent(writes).length === 3);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribute_undo', payload: {line_id: 'l1'}}, tag + ': Undo');
      assert.deepEqual(errors, [], tag + ': no page errors');
    }

    // A teammate without finance.attribution: no Attribution tab, and its address goes elsewhere.
    {
      const tag = 'mia';
      const {page, errors} = await open(browser, {viewer: 'mia', hash: '#/finance/budgets'});
      await page.waitForTimeout(200);
      assert.ok(!(await tabs(page)).includes('Attribution'), tag + ': no Attribution tab');
      await page.evaluate(() => { location.hash = '#/finance/attribution'; });
      await page.waitForFunction(() => !/attribution/.test(location.hash));
      assert.deepEqual(errors, [], tag + ': no page errors');
    }

    // A teammate whose only Finance right is finance.attribution: Finance shows, and lands on Attribution.
    {
      const tag = 'raj';
      const {page, errors} = await open(browser, {viewer: 'raj', hash: '#/finance'});
      await page.waitForFunction(() => /\/finance\/attribution/.test(location.hash));
      assert.deepEqual(await tabs(page), ['Attribution'], tag + ': only the Attribution tab');
      assert.deepEqual(errors, [], tag + ': no page errors');
    }

    // The owner's Attribution tab: after Budgets, the rules, On (disabled), + Rule, and Delete with a confirm in the menu.
    {
      const tag = 'attribution';
      const {page, errors, writes} = await open(browser, {hash: '#/finance/attribution'});
      await page.locator('[data-fin-rule-row="0"]').waitFor();
      const names = await tabs(page);
      assert.deepEqual(names.slice(names.indexOf('Budgets'), names.indexOf('Budgets') + 2), ['Budgets', 'Attribution'], tag + ': after Budgets');
      assert.deepEqual(await page.locator('tr[data-fin-rule-row] td:first-child').allInnerTexts(), ['Google Ads to Rental', 'Card 0001 split'], tag + ': the rules');
      assert.equal((await page.locator('[data-fin-rule-row="1"] td').nth(1).innerText()).replace(/\s+/g, ' ').trim(), 'Card Checking ••0001', tag + ': the kind word and the match');
      assert.equal((await page.locator('[data-fin-rule-row="0"] td').nth(1).innerText()).replace(/\s+/g, ' ').trim(), 'Vendor Google Ads', tag + ': a vendor rule');
      assert.equal(await page.locator('[data-fin-rule-row="1"] input[role=switch]').isChecked(), false, tag + ': an off rule is unchecked');

      // On: off posts enabled false.
      await page.locator('[data-fin-rule-row="0"] input[role=switch]').uncheck();
      await settled(() => sent(writes).length === 1);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribution_rule_set', payload: {id: 'r1', name: 'Google Ads to Rental', match: {kind: 'vendor', id: 'c-google'},
        splits: [{teammate: 'bot:rental-google', share_bps: 10000}], enabled: false}}, tag + ': On off posts enabled false');

      // Delete asks in the menu, then posts.
      await page.locator('[aria-label="Google Ads to Rental: actions"]').click();
      await page.locator('[data-fin-rule-del="0"]').click();
      await page.locator('[data-fin-rule-yes="0"]').waitFor();
      assert.equal(sent(writes).length, 1, tag + ': Delete asks first');
      await page.locator('[data-fin-rule-yes="0"]').click();
      await settled(() => sent(writes).length === 2);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribution_rule_delete', payload: {id: 'r1'}}, tag + ': Delete');

      // + Rule: a vendor, one teammate at 100%, from no date.
      await page.locator('[data-fin-rule-add]').click();
      const dlg = page.locator('dialog.fin-dlg');
      await dlg.waitFor();
      await dlg.locator('input[name=name]').fill('Rental ads');
      await page.waitForFunction(() => document.querySelector('dialog.fin-dlg select[name=match] option[value="c-google"]'));
      await dlg.locator('select[name=match]').selectOption('c-google');
      await dlg.locator('[data-share-row] select').selectOption('teammate:bot:rental-google');
      await dlg.locator('[data-share-row] input').fill('100');
      await dlg.locator('[type=submit]').click();
      await settled(() => sent(writes).length === 3);
      assert.deepEqual(sent(writes).at(-1), {kind: 'attribution_rule_set', payload: {name: 'Rental ads', match: {kind: 'vendor', id: 'c-google'},
        splits: [{teammate: 'bot:rental-google', share_bps: 10000}], enabled: true}}, tag + ': + Rule');

      await shot(page, 'attribution-desktop');
      assert.deepEqual(errors, [], tag + ': no page errors');
    }

    // Phone: the rules table scrolls in its box, the Transactions rail stacks, nothing runs sideways.
    {
      const tag = 'phone';
      const {page, errors} = await open(browser, {viewport: PHONE, hash: '#/finance/attribution'});
      await page.locator('[data-fin-rule-row="0"]').waitFor();
      assert.equal(await sideways(page), false, tag + ': the Attribution tab does not scroll sideways');
      await page.evaluate(() => { location.hash = '#/finance/transactions'; });
      await page.locator('tr[data-fin-tx="feed:f1"]').waitFor();
      await page.locator('tr[data-fin-tx="feed:f1"] td').first().click();
      await page.locator('[data-share-line="l1"]').waitFor();
      assert.equal(await sideways(page), false, tag + ': Transactions with a rail does not scroll sideways');
      await shot(page, 'attribution-phone');
      assert.deepEqual(errors, [], tag + ': no page errors');
    }
    console.log('finance-attribution: ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
