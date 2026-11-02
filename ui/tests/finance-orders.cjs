// Finance's purchase orders, estimates, Split by and line forms on a real hub, by the operations test cases: H1, H3, H4, C1,
// C2, M2, E2. Money is read off the screen and off the API and checked against the amounts entered.
// TICO_UI_SHOTS=<dir> saves desktop and phone screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');

const SHOTS = process.env.TICO_UI_SHOTS || '';
const DAY = '2026-10-20';       // the scenario world's frozen day; the pages' clock is set to it

(async () => {
  const hub = await startHub({seeds: ['org', 'toured', 'finance']});
  let browser;
  const call = async (who, method, route, body) => {
    const r = await fetch(hub.url + '/api' + route, {method, headers: {Authorization: 'Bearer ' + hub.tokens[who],
      'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID()}, body: body ? JSON.stringify(body) : undefined});
    const data = await r.json().catch(() => ({}));
    return {status: r.status, data};
  };
  const act = async (who, kind, payload) => {
    const r = await call(who, 'POST', '/v2/finance/actions', {kind, payload});
    assert.equal(r.status, 200, `${kind}: ${JSON.stringify(r.data)}`);
    assert.equal(r.data.status, 'committed', `${kind}: ${JSON.stringify(r.data)}`);
    return r.data.result;
  };
  const read = async (who, route) => { const r = await call(who, 'GET', route); assert.equal(r.status, 200, route + JSON.stringify(r.data)); return r.data; };

  const open = async (who, hash, phone = false) => {
    const page = await pageAs(browser, hub, who, {phone});
    pages.push(page);
    await page.clock.setFixedTime(new Date(DAY + 'T12:00:00Z'));
    page.on('pageerror', e => { errors.push(`${who}: ${e.message}`); });
    await page.goto(hub.url + '/' + hash);
    return page;
  };
  const go = async (page, hash) => { await page.evaluate(h => { location.hash = h; }, hash); };
  // Past the page's cross-fade, and without the world's runner notice over the phone's page.
  const shot = async (page, name) => {
    if (!SHOTS) return;
    fs.mkdirSync(SHOTS, {recursive: true});
    await page.locator('button:text-is("Close"):near(:text("needs you"))').first().click({timeout: 300}).catch(() => {});
    await new Promise(r => setTimeout(r, 500));
    await page.screenshot({path: path.join(SHOTS, name + '.png'), fullPage: true, animations: 'disabled'});
  };
  const pick = (page, id) => page.locator(`tr[data-fin-ord="${id}"]`).click();
  const rail = page => page.locator('#fin-rail');
  const act_ = (page, label) => rail(page).getByRole('button', {name: label, exact: true});
  const dlg = page => page.locator('dialog.fin-dlg');
  // Saves a dialog, through the ask step when the rules name an approver.
  const save = async (page, label = 'Save') => { await dlg(page).getByRole('button', {name: label, exact: true}).click(); };
  const closed = page => dlg(page).waitFor({state: 'detached', timeout: t(10000)});
  const po = id => read('dana', '/v2/purchase-orders/' + id);
  const est = id => read('dana', '/v2/estimates/' + id);
  const until = async (fn, what) => {
    for (let i = 0; i < 100; i++) { if (await fn()) return; await new Promise(r => setTimeout(r, 100)); }
    assert.fail('never true: ' + what);
  };
  const budgetOf = async id => (await read('dana', '/v2/finance/budgets')).budgets.find(b => b.id === id).state;
  const errors = [];
  const pages = [];

  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL});
    await act('dana', 'right_grant', {teammate: 'human:omar', right: 'finance.ap'});
    await act('dana', 'right_grant', {teammate: 'human:omar', right: 'finance.ar'});
    for (const what of ['payables', 'receivables', 'pnl']) await act('dana', 'view_grant', {subject_kind: 'teammate', subject: 'human:omar', what, whose: 'company'});

    // The books an HVAC company starts from: two shops, a job, a class, what it buys and sells.
    const chart = await read('dana', '/v2/finance/chart?for=journal');
    const code = n => chart.accounts.find(a => a.number === n).id;
    const bank = code('1000'), revenue = code('4000');
    const costs = code('6100');
    const north = (await act('dana', 'location_create', {name: 'North shop', kind: 'store'})).location_id;
    const south = (await act('dana', 'location_create', {name: 'South shop', kind: 'store'})).location_id;
    const shut = (await act('dana', 'location_create', {name: 'Old shop', kind: 'store'})).location_id;
    await act('dana', 'location_update', {location_id: shut, active: false});
    const supplier = (await act('dana', 'contact_create', {name: 'Acme Supplies', kind: 'vendor'})).contact_id;
    const factory = (await act('dana', 'contact_create', {name: 'Acme Factory', kind: 'vendor'})).contact_id;
    const customer = (await act('dana', 'contact_create', {name: 'Acme Homes', kind: 'customer'})).contact_id;
    const rival = (await act('dana', 'contact_create', {name: 'Other Homes', kind: 'customer'})).contact_id;
    const job = (await act('dana', 'project_create', {name: 'Kitchen remodel', customer_id: customer})).project_id;
    const big = (await act('dana', 'project_create', {name: 'Warehouse install', customer_id: rival})).project_id;
    const unit = (await act('dana', 'item_create', {name: 'System replacement', kind: 'service', price: 1240000, income_account_id: revenue})).item_id;
    await act('dana', 'item_create', {name: 'Condenser', kind: 'non_inventory', price: 90000, expense_account_id: costs});
    await act('dana', 'item_create', {name: 'Parts', kind: 'category'});
    const klass = (await read('dana', '/v2/finance/chart?for=bill')).classes.find(c => c.name === 'Commercial').id;

    // ---- H1: the two shops and the commercial job side by side; the numbers match what was posted.
    const sale = async (amount, tags) => act('dana', 'journal', {date: DAY, memo: 'Sale', lines: [{account_id: bank, amount}, {account_id: revenue, amount: -amount, tags}]});
    const spend = async (amount, tags) => act('dana', 'journal', {date: DAY, memo: 'Cost', lines: [{account_id: costs, amount, tags}, {account_id: bank, amount: -amount}]});
    await sale(500000, {location: north, project: big, class: klass});
    await sale(300000, {location: south, project: big});
    await sale(120000, {location: north});
    await sale(45000, {});
    await spend(200000, {location: north, project: big});
    await act('dana', 'journal', {date: DAY, memo: 'Parts', lines: [{account_id: code('5100'), amount: 30000, tags: {location: north, project: big}}, {account_id: bank, amount: -30000}]});
    const page = await open('dana', '#/finance/pnl');
    const table = page.locator('#fin-pl table');
    await page.locator('[data-fin-split]').waitFor({timeout: t(20000)});
    await page.locator('[data-fin-split]').selectOption('location_project');
    await page.locator('.fin-split-t').waitFor({timeout: t(10000)});
    const cells = async () => table.locator('tbody tr').evaluateAll(rows => rows.map(r => [...r.children].map(c => c.textContent.trim())));
    const money = s => Math.round(Number(s.replace(/[^0-9.()-]/g, '').replace(/^\((.*)\)$/, '-$1')) * 100);
    const byLabel = Object.fromEntries((await cells()).map(c => [c[0], c.slice(1).map(money)]));
    // Income, expenses, net income, cost of goods, gross margin. Expenses include the cost of goods.
    assert.deepEqual(byLabel['North shop · Warehouse install'], [500000, 230000, 270000, 30000, 470000]);
    assert.deepEqual(byLabel['South shop · Warehouse install'], [300000, 0, 300000, 0, 300000]);
    assert.deepEqual(byLabel['North shop · Unattributed'], [120000, 0, 120000, 0, 120000], JSON.stringify(byLabel));
    const got = await cells();
    const un = got[got.length - 2];
    assert.equal(un[0], 'Unattributed', 'Unattributed is the last row before the total');
    assert.equal(money(un[1]), 45000);
    assert.deepEqual(got[got.length - 1].slice(1).map(money), [965000, 230000, 735000, 30000, 935000], 'the total is every line posted');
    await shot(page, 'pnl-split-desktop');
    for (const [by, label] of [['location', 'North shop'], ['class', 'Commercial'], ['project', 'Warehouse install']]) {
      await page.locator('[data-fin-split]').selectOption(by);
      await until(async () => (await cells()).some(c => c[0] === label), by);
    }
    // A location's row by account, with the margin in its totals; Unattributed and a rolled-up row don't open.
    await page.locator('[data-fin-split]').selectOption('location');
    await until(async () => (await cells()).some(c => c[0] === 'North shop'), 'by location');
    assert.equal(await page.locator('tr[data-fin-group="unattributed"] button').count(), 0);
    await page.getByRole('button', {name: 'North shop', exact: true}).click();
    await rail(page).getByText('Gross margin').waitFor({timeout: t(10000)});
    const drill = await rail(page).locator('.fin-kv').evaluateAll(rows => Object.fromEntries(rows.map(r => [r.firstElementChild.textContent.trim(), r.lastElementChild.textContent.trim()])));
    assert.deepEqual(['Income', 'Expenses', 'Net income', 'Cost of goods', 'Gross margin'].map(k => money(drill[k])), [620000, 230000, 390000, 30000, 590000], JSON.stringify(drill));
    await shot(page, 'pnl-drill-desktop');
    await rail(page).getByRole('button', {name: '‹ Back'}).click();
    assert.equal(await rail(page).locator('.fin-kv').count(), 0);
    const rolled = {by: 'location', rows: [{key: 'other', label: 'Other · 3', count: 3, income: 900, expense: 400, net: 500, cost_of_goods: 100, gross_margin: 800}],
      income: 900, expense: 400, net: 500, cost_of_goods: 100, gross_margin: 800, excluded: 0};
    await page.route('**/api/v2/finance/reports/pnl_by?*', r => r.fulfill({json: rolled}));
    await page.locator('[data-fin-split]').selectOption('project');
    await until(async () => (await cells()).some(c => c[0] === 'Other · 3'), 'the rolled-up row');
    assert.deepEqual((await cells())[0].slice(1).map(money), [900, 400, 500, 100, 800]);
    assert.equal(await page.locator('tr[data-fin-group="other"] button').count(), 0);
    await page.unroute('**/api/v2/finance/reports/pnl_by?*');
    await page.locator('[data-fin-split]').selectOption('');
    await page.locator('[data-fin-seg="period"]').waitFor({timeout: t(5000)});
    assert.ok(!page.url().includes('split='), 'clearing the split clears the address');
    // A link from elsewhere opens on its split, and survives a change of the select.
    await go(page, '#/finance/pnl?split=location');
    await until(async () => (await cells()).some(c => c[0] === 'North shop'), 'the address picks the split');
    assert.equal(await page.locator('[data-fin-split]').inputValue(), 'location');
    await page.locator('[data-fin-split]').selectOption('class');
    await until(async () => (await cells()).some(c => c[0] === 'Commercial'), 'the select overrides the link');
    assert.ok(page.url().includes('split=class'), page.url());
    await page.locator('[data-fin-split]').selectOption('');

    // ---- Line forms: an item fills the line and the price; categories and archived records are not offered.
    await go(page, '#/finance/receivables/estimates');
    await page.getByRole('button', {name: '+ New estimate'}).click();
    const form = dlg(page);
    await form.locator('[name=item]').waitFor({timeout: t(10000)});
    const items = await form.locator('[name=item] option').allTextContents();
    assert.ok(items.includes('System replacement') && !items.includes('Parts'), 'a category is not offered: ' + items);
    const places = await form.locator('[name=dim-location] option').allTextContents();
    assert.ok(places.includes('North shop') && !places.includes('Old shop'), 'an archived location is not offered: ' + places);
    await form.locator('[name=contact]').selectOption(customer);
    await form.locator('[name=number]').fill('E-100');
    await form.locator('[name=item]').selectOption(unit);
    assert.equal(await form.locator('[name=amt]').inputValue(), '12400', 'the item fills its price');
    assert.equal(await form.locator('[name=acct]').inputValue(), revenue, 'the item fills its line');
    await form.locator('[name=dim-location]').selectOption(north);
    await form.locator('[name=dim-project]').selectOption(job);
    await form.locator('[name=dim-class]').selectOption(klass);
    await shot(page, 'estimate-form-desktop');
    await save(page); await closed(page);

    // ---- H3: the $12,400 estimate, made by a teammate: Send asks first, the customer accepts, Convert makes one invoice.
    const est100 = (await read('dana', '/v2/estimates?q=E-100')).estimates[0];
    assert.equal(est100.total, 1240000);
    assert.deepEqual((await est(est100.id)).lines[0].tags, {location: north, project: job, class: klass}, 'the line keeps its tags');
    const omar = await open('omar', `#/finance/receivables/estimates?doc=${est100.id}`);
    await act_(omar, 'Send').waitFor({timeout: t(20000)});
    assert.equal(await omar.locator('tr[data-fin-ord] td').nth(2).innerText(), 'Kitchen remodel', 'the list names the project');
    await act_(omar, 'Send').click();
    await rail(omar).locator('[data-fin-ask-yes]').click();   // the send asks first
    await until(async () => (await est(est100.id)).status === 'draft' && (await est(est100.id)).actions.some(a => a.status === 'needs_approval'), 'the send is asked');
    await until(async () => (await rail(omar).innerText()).includes('Approval from'), 'the asker sees who decides');
    assert.equal(await act_(omar, 'Approve').count(), 0, 'not waiting on the asker');
    await go(page, `#/finance/receivables/estimates?doc=${est100.id}`);
    await act_(page, 'Approve').waitFor({timeout: t(10000)});
    await shot(page, 'estimate-panel-desktop');
    await act_(page, 'Approve').click();
    await until(async () => (await est(est100.id)).status === 'sent', 'the owner approves the send');
    await act_(page, 'Mark accepted').click();
    await act_(page, 'Convert to invoice').waitFor({timeout: t(10000)});
    await act_(page, 'Convert to invoice').click();
    await until(async () => (await est(est100.id)).status === 'invoiced', 'the convert');
    const made = (await est(est100.id)).invoice_id;
    assert.ok(made);
    assert.equal((await read('dana', `/v2/finance/invoices?customer_id=${customer}`)).invoices.filter(i => i.id === made).length, 1);
    await rail(page).getByRole('link', {name: 'Open'}).waitFor({timeout: t(10000)});
    assert.equal(await act_(page, 'Convert to invoice').count(), 0);
    const again = await call('dana', 'POST', '/v2/finance/actions', {kind: 'estimate_convert', payload: {estimate_id: est100.id}});
    assert.notEqual(again.data.status, 'committed', 'a second convert is refused');
    assert.equal((await read('dana', `/v2/finance/invoices?customer_id=${customer}`)).invoices.length, 1, 'still one invoice');
    await shot(page, 'estimates-desktop');
    // ... and a declined estimate can't convert.
    const lost = (await act('dana', 'estimate_create', {customer_id: customer, number: 'E-101', estimate_date: DAY, lines: [{account_id: revenue, amount: 90000}]})).estimate_id;
    await act('dana', 'estimate_send', {estimate_id: lost});
    await go(page, `#/finance/receivables/estimates?doc=${lost}`);
    await act_(page, 'Mark declined').waitFor({timeout: t(10000)});
    await act_(page, 'Mark declined').click();
    await until(async () => (await est(lost)).status === 'declined', 'the decline');
    await until(async () => await act_(page, 'Mark declined').count() === 0, 'the buttons follow');
    assert.equal(await act_(page, 'Convert to invoice').count(), 0, 'a declined estimate offers no convert');
    const refused = await call('dana', 'POST', '/v2/finance/actions', {kind: 'estimate_convert', payload: {estimate_id: lost}});
    assert.notEqual(refused.data.status, 'committed', 'a declined estimate can\'t convert');

    // ---- C2: a change order: a second estimate on the same job, both named on the list.
    await act('dana', 'estimate_create', {customer_id: customer, number: 'E-102', estimate_date: DAY, lines: [{account_id: revenue, amount: 150000, tags: {project: job}}]});
    await go(page, '#/finance/receivables/estimates');
    await until(async () => await page.locator('tr[data-fin-ord]').count() === 3, 'three estimates');
    const onJob = await page.locator('tr[data-fin-ord]').evaluateAll(rows => rows.filter(r => r.children[2].textContent.trim() === 'Kitchen remodel').map(r => r.children[0].textContent.trim()));
    assert.deepEqual(onJob.sort(), ['E-100', 'E-102'], 'C2: both estimates on the job');

    // ---- H4: a PO over $1,000 against a budget: the owner approves; a hold; a partial bill; Close; the budget exact.
    const budget = (await act('dana', 'budget_create', {name: 'Install materials', account_id: costs, amount: 500000, at_limit: 'warn', parent_id: null})).budget_id;
    const before = await budgetOf(budget);
    await go(omar, '#/finance/payables/orders');
    await omar.getByRole('button', {name: '+ New purchase order'}).click();
    const pf = dlg(omar);
    await pf.locator('[name=contact]').selectOption(supplier);
    await pf.locator('[name=number]').fill('PO-1');
    await pf.locator('[name=acct]').selectOption(costs);
    await pf.locator('[name=amt]').fill('2000');
    await pf.locator('[name=dim-location]').selectOption(north);
    await pf.locator('[name=dim-project]').selectOption(job);          // C1: the PO on the job
    await shot(omar, 'po-form-desktop');
    await save(omar); await closed(omar);
    const row = () => omar.locator('tr[data-fin-ord]').first();
    await row().waitFor({timeout: t(10000)});
    const id = (await read('dana', '/v2/purchase-orders?q=PO-1')).orders[0].id;
    await act_(omar, 'Approve').waitFor({timeout: t(10000)});
    await act_(omar, 'Approve').click();
    await rail(omar).locator('[data-fin-ask-yes]').click();
    await until(async () => (await rail(omar).innerText()).includes('Approval from'), 'approval is asked of the owner');
    assert.equal(await act_(omar, 'Approve').count(), 0, 'H4: Approve only when waiting on the viewer');
    assert.equal(await act_(omar, 'Convert to bill').count(), 0, 'nothing to bill before it is approved');
    await go(page, `#/finance/payables/orders?doc=${id}`);
    await act_(page, 'Approve').waitFor({timeout: t(10000)});
    await act_(page, 'Approve').click();
    await until(async () => (await po(id)).status === 'approved', 'the owner approves');
    assert.equal((await po(id)).held, 200000, 'the hold is placed');
    assert.equal((await budgetOf(budget)).remaining, before.remaining - 200000, 'held against the budget');
    await act_(page, 'Convert to bill').waitFor({timeout: t(10000)});
    await act_(page, 'Convert to bill').click();
    const billForm = dlg(page);
    assert.equal(await billForm.locator('[data-fin-po-line]').inputValue(), '2000', 'starts at what is left');
    await billForm.locator('[data-fin-po-line]').fill('800');
    await billForm.locator('[name=number]').fill('B-1');
    await save(page); await closed(page);
    await until(async () => (await po(id)).status === 'partial', 'the partial bill');
    let order = await po(id);
    assert.deepEqual([order.billed, order.held], [80000, 120000], 'the bill takes over its part of the hold');
    assert.equal((await budgetOf(budget)).remaining, before.remaining - 200000, 'a bill out of the hold leaves the budget where it was');
    await rail(page).getByRole('link', {name: '800.00'}).waitFor({timeout: t(10000)});
    assert.deepEqual(await page.locator(`tr[data-fin-ord="${id}"] td`).allInnerTexts().then(c => [c[2], c[4], c[5]]), ['North shop', '800.00', '1,200.00'], 'the list columns');
    await shot(page, 'po-panel-desktop');
    await act_(page, 'Close').click();
    await until(async () => (await po(id)).status === 'closed', 'the close');
    assert.equal((await po(id)).held, 0, 'Close releases the rest');
    assert.equal((await budgetOf(budget)).remaining, before.remaining - 80000, 'H4: the budget is exact');

    // ---- M2: billed in two parts: partial, then closed by the last bill.
    const resin = (await act('dana', 'po_create', {vendor_id: supplier, number: 'PO-2', po_date: DAY, lines: [{account_id: costs, amount: 90000, description: 'Resin'}]})).po_id;
    await act('dana', 'po_approve', {po_id: resin});
    const part = async (ref, amount, pageRef = page) => {
      await act_(pageRef, 'Convert to bill').waitFor({timeout: t(10000)});
      await act_(pageRef, 'Convert to bill').click();
      const f = dlg(pageRef);
      await f.locator('[name=number]').fill(ref);
      await f.locator('[data-fin-po-line]').first().fill(amount);
      await save(pageRef); await closed(pageRef);
    };
    await go(page, `#/finance/payables/orders?doc=${resin}`);
    await part('R-1', '400');
    await until(async () => (await po(resin)).status === 'partial', 'M2 partial');
    assert.deepEqual([(await po(resin)).billed, (await po(resin)).held], [40000, 50000]);
    await until(async () => (await rail(page).innerText()).includes('R-1'), 'M2 first bill is on the panel');
    await part('R-2', '500');
    await until(async () => (await po(resin)).status === 'closed', 'M2 closed by the last bill');
    assert.deepEqual([(await po(resin)).billed, (await po(resin)).held], [90000, 0]);

    // ---- E2: an overseas order with a deposit and balance, freight and duty lines: every bill links and shows on the panel.
    const overseas = (await act('dana', 'po_create', {vendor_id: factory, number: 'PO-3', po_date: DAY, lines: [
      {account_id: costs, amount: 1000000, description: 'Goods'}, {account_id: costs, amount: 150000, description: 'Freight'}, {account_id: costs, amount: 50000, description: 'Duty'}]})).po_id;
    await act('dana', 'po_approve', {po_id: overseas});
    await go(page, `#/finance/payables/orders?doc=${overseas}`);
    const convert = async (ref, amounts) => {
      await act_(page, 'Convert to bill').waitFor({timeout: t(10000)});
      await act_(page, 'Convert to bill').click();
      const f = dlg(page);
      await f.locator('[name=number]').fill(ref);
      const inputs = await f.locator('[data-fin-po-line]').all();
      for (let i = 0; i < inputs.length; i++) await inputs[i].fill(amounts[i] ?? '');
      await save(page); await closed(page);
      await until(async () => (await rail(page).innerText()).includes(ref), ref + ' on the panel');
    };
    await convert('Deposit', ['3000', '', '']);
    await convert('Balance', ['7000', '', '']);
    await convert('Freight', ['1500', '']);    // the goods line is billed in full, so the form no longer offers it
    await convert('Duty', ['500']);
    const landed = await po(overseas);
    assert.deepEqual(landed.bills.map(b => b.number).sort(), ['Balance', 'Deposit', 'Duty', 'Freight'], 'E2: every bill links');
    assert.deepEqual(landed.bills.map(b => b.on_order).sort((a, b) => a - b), [50000, 150000, 300000, 700000]);
    assert.equal(landed.status, 'closed');
    await shot(page, 'po-landed-desktop');
    // A linked bill opens from the order's panel.
    await rail(page).getByRole('link', {name: '500.00', exact: true}).click();
    await until(async () => (await rail(page).innerText()).includes('Duty'), 'the bill opens from the order');

    // ---- C1: a bill and an invoice on the job through the forms; POs carry it above.
    await go(page, '#/finance/payables');
    await page.getByRole('button', {name: '+ Bill'}).click();
    const bf = dlg(page);
    await bf.locator('[name=contact]').selectOption(supplier);
    await bf.locator('[name=number]').fill('C-1');
    await bf.locator('[name=line]').selectOption(costs);
    await bf.locator('[name=amount]').fill('300');
    await bf.locator('[name=dim-project]').selectOption(job);
    await save(page); await closed(page);
    const billed = (await read('dana', '/v2/finance/bills?q=C-1')).bills[0];
    assert.equal((await read('dana', '/v2/finance/bills/' + billed.id)).lines[0].tags.project, job, 'C1: a bill on the job');
    await go(page, '#/finance/receivables');
    await page.getByRole('button', {name: '+ Invoice'}).click();
    const inf = dlg(page);
    await inf.locator('[name=contact]').selectOption(customer);
    await inf.locator('[name=number]').fill('C-2');
    await inf.locator('[name=acct]').selectOption(revenue);
    await inf.locator('[name=amt]').fill('600');
    await inf.locator('[name=dim-project]').selectOption(job);
    await save(page); await closed(page);
    const sold = (await read('dana', '/v2/finance/invoices?q=C-2')).invoices[0];
    assert.equal((await read('dana', '/v2/finance/invoices/' + sold.id)).lines[0].tags.project, job, 'C1: an invoice on the job');
    assert.equal((await po(id)).lines[0].tags.project, job, 'C1: a PO on the job');

    // ---- The filters Operations calls: a line's location or project, paging right.
    const numbers = async (route, key) => {
      const out = [];
      let cursor = '';
      do {
        const d = await read('dana', `${route}&limit=1${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`);
        out.push(...d[key].map(r => r.number));
        cursor = d.next_cursor || '';
      } while (cursor);
      return out.sort();
    };
    assert.deepEqual(await numbers(`/v2/purchase-orders?project_id=${job}`, 'orders'), ['PO-1']);
    assert.deepEqual(await numbers(`/v2/purchase-orders?location_id=${north}`, 'orders'), ['PO-1']);
    assert.deepEqual(await numbers(`/v2/purchase-orders?location_id=${south}`, 'orders'), []);
    assert.deepEqual(await numbers(`/v2/estimates?project_id=${job}`, 'estimates'), ['E-100', 'E-102']);
    assert.deepEqual(await numbers(`/v2/estimates?project_id=${job}&location_id=${north}`, 'estimates'), ['E-100']);
    assert.ok((await numbers(`/v2/finance/invoices?project_id=${job}`, 'invoices')).includes('C-2'));
    assert.deepEqual(await numbers(`/v2/finance/invoices?project_id=${big}`, 'invoices'), []);

    // ---- The panels. A customer's: open estimates and invoices, another customer's left out; a vendor's: its fields.
    await act('dana', 'invoice_send', {invoice_id: sold.id});
    const site = (await act('dana', 'location_create', {name: 'Acme Homes site', kind: 'customer_site', customer_id: customer})).location_id;
    await act('dana', 'location_create', {name: 'Other Homes site', kind: 'customer_site', customer_id: rival});
    assert.ok(site);
    await go(page, `#/finance/receivables/estimates?contact=${customer}`);
    await rail(page).getByRole('heading', {name: 'Estimates'}).waitFor({timeout: t(10000)});
    const panel = await rail(page).innerText();
    assert.ok(panel.includes('E-102') && !panel.includes('E-100') && !panel.includes('E-101'), 'only open estimates: ' + panel);
    assert.ok(panel.includes('C-2') && panel.includes('$600'), 'open invoices: ' + panel);
    assert.ok(panel.includes('Acme Homes site') && !panel.includes('Other Homes site'), 'its own sites: ' + panel);
    await rail(page).getByRole('heading', {name: 'Acme Homes'}).waitFor({timeout: t(5000)});
    await shot(page, 'customer-panel-desktop');
    await go(page, `#/finance/payables/orders?contact=${supplier}`);
    await rail(page).getByText('Payment terms').waitFor({timeout: t(10000)});
    for (const label of ['Payment terms', 'Owner', 'Renews', 'Notice']) assert.ok((await rail(page).innerText()).includes(label), label);
    await rail(page).getByRole('heading', {name: 'Acme Supplies'}).waitFor({timeout: t(5000)});   // named from the address alone
    await shot(page, 'vendor-panel-desktop');
    // A vendor's owner can be anyone, as seen by a teammate who isn't a finance admin.
    await go(omar, `#/finance/payables/orders?contact=${supplier}`);
    await rail(omar).getByRole('button', {name: 'Edit'}).click();
    const owners = await dlg(omar).locator('[name=owner] option').evaluateAll(o => o.map(x => x.value));
    assert.ok(owners.includes('human:priya') && owners.includes('bot:engineering-lead'), 'no Finance access still an owner: ' + owners);
    await dlg(omar).getByRole('button', {name: 'Cancel'}).click();
    // The panel's Edit goes through the one write path; only what changed is sent.
    await rail(page).getByRole('button', {name: 'Edit'}).click();
    await dlg(page).locator('[name=renews]').fill('2027-03-01');
    await save(page); await closed(page);
    await rail(page).getByText('Payment terms').waitFor({timeout: t(10000)});

    // ---- A contact's Library files. A request that waits on approval says nothing was added or removed.
    const upload = async name => {
      const r = await call('engineering-lead', 'POST', '/v2/files/uploads', {name, text: 'Acme contract', scope: 'bot'});
      assert.ok(r.status < 300, JSON.stringify(r.data));
      return (r.data.file || r.data).id;
    };
    const contract = await upload('acme-contract.txt');
    const waits = r => r.fulfill({json: {status: 'needs_approval', approver: 'human:dana'}});
    const actions = '**/api/v2/finance/actions';
    const pickFile = async name => {
      await rail(page).getByRole('button', {name: 'Add file'}).click();
      await dlg(page).getByRole('option', {name}).click();
      await shot(page, 'file-pick-desktop');
      await dlg(page).getByRole('button', {name: 'Add', exact: true}).click();
      await closed(page);
    };
    await page.route(actions, waits);
    await pickFile('acme-contract.txt');
    await page.getByText('Sent to').first().waitFor({timeout: t(5000)});
    assert.equal(await rail(page).getByRole('link', {name: 'acme-contract.txt'}).count(), 0);
    assert.equal(await page.getByText('Added', {exact: true}).count(), 0, 'no success while it waits');
    await page.unroute(actions);
    await page.getByText('Sent to').first().waitFor({state: 'detached', timeout: t(15000)}).catch(() => {});
    await pickFile('acme-contract.txt');
    await rail(page).getByRole('link', {name: 'acme-contract.txt'}).waitFor({timeout: t(10000)});
    assert.deepEqual((await read('dana', '/v2/finance/contacts/' + supplier)).files.map(f => f.file_id), [contract]);
    await shot(page, 'vendor-files-desktop');
    // A viewer without finance.records sees the file and no way to change it.
    await omar.evaluate(id => finContactShow({id, name: '', kind: 'vendor'}), supplier);
    await rail(omar).getByRole('link', {name: 'acme-contract.txt'}).waitFor({timeout: t(10000)});
    assert.equal(await rail(omar).locator('[data-file-add],[data-file-remove]').count(), 0);
    await page.route(actions, waits);
    await rail(page).getByRole('button', {name: 'Remove acme-contract.txt'}).click();
    await page.waitForTimeout(t(300));
    assert.equal(await rail(page).getByRole('link', {name: 'acme-contract.txt'}).count(), 1, 'still linked while it waits');
    assert.equal(await page.getByText('Removed', {exact: true}).count(), 0);
    await page.unroute(actions);
    await rail(page).getByRole('button', {name: 'Remove acme-contract.txt'}).click();
    await rail(page).getByRole('link', {name: 'acme-contract.txt'}).waitFor({state: 'detached', timeout: t(10000)});
    assert.deepEqual((await read('dana', '/v2/finance/contacts/' + supplier)).files, []);
    await act('dana', 'record_file_add', {subject_type: 'contact', subject_id: supplier, file_id: contract});
    await act('dana', 'record_file_add', {subject_type: 'contact', subject_id: customer, file_id: contract});
    await go(page, `#/finance/receivables/estimates?contact=${customer}`);
    await rail(page).getByRole('link', {name: 'acme-contract.txt'}).waitFor({timeout: t(10000)});

    // ---- A provider's copy says when it last synced; Tico's own record says nothing.
    const fresh = async extra => {
      const route = `**/api/v2/finance/contacts/${supplier}*`;
      await page.route(route, async r => { const res = await r.fetch(), json = await res.json(); await r.fulfill({response: res, json: {...json, contact: {...json.contact, ...extra}}}); });
      await page.evaluate(id => finContactShow({id, name: '', kind: 'vendor'}), supplier);
      await rail(page).getByText('Payment terms').waitFor({timeout: t(10000)});
      const text = await rail(page).innerText();
      await page.unroute(route);
      return [text.includes('Last sync'), text.includes('May be out of date')];
    };
    await go(page, '#/finance/payables/orders');
    await page.locator('tr[data-fin-ord]').first().waitFor({timeout: t(10000)});
    await page.waitForTimeout(t(500));
    assert.deepEqual(await fresh({}), [false, false], 'the real read of Tico\'s own record');
    assert.deepEqual(await fresh({source: 'quickbooks', as_of: '2026-10-19T15:00:00Z', stale: false}), [true, false]);
    assert.deepEqual(await fresh({source: 'quickbooks', as_of: '2026-10-01T15:00:00Z', stale: true}), [true, true]);
    await shot(page, 'vendor-stale-desktop');
    assert.deepEqual(await fresh({source: 'quickbooks', as_of: null, stale: true}), [false, true]);
    await page.evaluate(() => finContactClose());

    // ---- A bill of any vendor links to an order: not a billed line, not in its total.
    const unlinked = await po(id);
    assert.deepEqual(unlinked.linked_bills || [], []);
    await go(page, `#/finance/payables/orders?doc=${id}`);
    await rail(page).getByText('Billed').waitFor({timeout: t(10000)});
    assert.equal(await rail(page).getByRole('heading', {name: 'Linked bills'}).count(), 0, 'no section when none is linked');
    await go(page, '#/finance/payables');
    await page.getByRole('button', {name: /^\+ /}).first().click();
    await dlg(page).locator('[name=po]').waitFor({timeout: t(10000)});
    await dlg(page).locator('[name=contact]').selectOption(factory);
    await dlg(page).locator('[name=number]').fill('L-1');
    await dlg(page).locator('[name=amount]').fill('250');
    await dlg(page).locator('[name=po]').selectOption(id);
    await save(page); await closed(page);
    let linked;
    await until(async () => { linked = (await po(id)).linked_bills?.[0]; return !!linked; }, 'the bill links');
    assert.deepEqual([linked.number, linked.vendor, linked.total], ['L-1', 'Acme Factory', 25000]);
    assert.equal((await po(id)).billed, unlinked.billed, 'a link is not billed');
    await go(page, `#/finance/payables/orders?doc=${id}`);
    await rail(page).getByRole('heading', {name: 'Linked bills'}).waitFor({timeout: t(10000)});
    await shot(page, 'po-linked-desktop');
    await rail(page).locator(`a[href*="${linked.id}"]`).click();
    await rail(page).getByRole('button', {name: 'Change', exact: true}).waitFor({timeout: t(10000)});
    assert.ok(page.url().includes(linked.id), page.url());
    await shot(page, 'bill-order-desktop');
    // A mirrored book refuses the change, and a link to an order the viewer can't read isn't shown.
    const billRead = async extra => {
      const route = `**/api/v2/finance/bills/${linked.id}*`;
      await page.route(route, async r => { const res = await r.fetch(), json = await res.json(); await r.fulfill({response: res, json: {...json, ...extra}}); });
      await page.evaluate(() => finDocOne());
      await rail(page).getByText('Status').waitFor({timeout: t(5000)});
      await page.waitForTimeout(t(500));
      const text = await rail(page).innerText();
      await page.unroute(route);
      return [text.includes('Open'), text.includes('Change')];
    };
    assert.deepEqual(await billRead({book_mode: 'mirror'}), [true, false]);
    assert.deepEqual(await billRead({book_mode: 'mirror', purchase_order_id: null}), [false, false]);
    await page.evaluate(() => finDocOne());
    await rail(page).getByRole('button', {name: 'Change', exact: true}).click();
    await dlg(page).locator('[name=po]').selectOption('');
    await save(page); await closed(page);
    await until(async () => !(await po(id)).linked_bills?.length, 'an empty choice clears the link');
    assert.equal((await read('dana', '/v2/finance/bills/' + linked.id)).purchase_order_id, null);
    await act('dana', 'bill_update', {bill_id: linked.id, purchase_order_id: id});

    // ---- A teammate without the rights: no Purchase orders or Estimates switch, and the routes fall back.
    const priya = await open('priya', '#/finance');
    await priya.waitForTimeout(t(800));
    await go(priya, '#/finance/payables/orders');
    await priya.waitForTimeout(t(500));
    assert.equal(await priya.getByRole('button', {name: 'Purchase orders'}).count(), 0);
    await priya.context().close();

    // ---- Phone: the same pages, no sideways scroll.
    const phone = await open('dana', '#/finance/pnl', true);
    const fits = async what => {
      const w = await phone.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
      assert.ok(w[0] <= w[1], `${what}: ${w[0]} wide on a ${w[1]} phone`);
    };
    await phone.locator('[data-fin-split]').waitFor({timeout: t(20000)});
    await phone.locator('[data-fin-split]').selectOption('location_project');
    await phone.locator('.fin-split-t').waitFor({timeout: t(10000)});
    await fits('split'); await shot(phone, 'pnl-split-phone');
    await phone.locator('[data-fin-split]').selectOption('location');
    await phone.getByRole('button', {name: 'North shop', exact: true}).click();
    await rail(phone).getByText('Gross margin').waitFor({timeout: t(10000)});
    await fits('drill'); await shot(phone, 'pnl-drill-phone');
    await go(phone, `#/finance/payables/orders?doc=${id}`);
    await rail(phone).getByText('Billed').waitFor({timeout: t(10000)});
    await fits('orders'); await shot(phone, 'po-panel-phone');
    await go(phone, `#/finance/receivables/estimates?doc=${est100.id}`);
    await rail(phone).getByText('Kitchen remodel').first().waitFor({timeout: t(10000)});
    await fits('estimates'); await shot(phone, 'estimates-phone');
    await phone.getByRole('button', {name: '+ New estimate'}).click();
    await dlg(phone).locator('[name=item]').waitFor({timeout: t(10000)});
    await fits('form'); await shot(phone, 'estimate-form-phone');
    await dlg(phone).getByRole('button', {name: 'Cancel'}).click();
    await go(phone, `#/finance/receivables/estimates?contact=${customer}`);
    await rail(phone).getByRole('heading', {name: 'Estimates'}).waitFor({timeout: t(10000)});
    await fits('customer panel'); await shot(phone, 'customer-panel-phone');
    await go(phone, `#/finance/payables/orders?contact=${supplier}`);
    await rail(phone).getByText('Payment terms').waitFor({timeout: t(10000)});
    await fits('vendor panel'); await shot(phone, 'vendor-panel-phone');

    assert.deepEqual(errors, [], 'no page errors');
    console.log('ok - finance orders: H1 H3 H4 C1 C2 M2 E2');
  } catch (e) {
    // The screen each teammate was on, when SHOTS is set: a failure here is usually read off them.
    for (const [i, p] of pages.entries()) await shot(p, 'failed-' + i).catch(() => {});
    throw e;
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(e => { console.error(e); process.exit(1); });
