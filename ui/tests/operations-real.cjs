// Operations (ui/app/operations-*.js) against a real hub: the app on a temporary database, seeded with Acme's shared records
// through the API (evals/redesign/seeds.py "operations"). Nothing here is faked: every shape is what the routes return.
// Server filters on every tab; forms that read back what they wrote; due dates add, complete, reopen, stop.
// H7  a bot's equipment_create with a photo waits for the owner, the approval shows the photo, and approving saves it.
// H7a a customer's panel from a location or equipment panel, for a teammate with no Finance view, and for the owner.
// TICO_UI_SHOTS=<dir> saves screenshots.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {startHub, pageAs} = require('./support/real-hub.cjs');
const {t} = require('./support/load.cjs');
const SHOTS = process.env.TICO_UI_SHOTS || '';
// A drawn data plate, a real PNG: the approval's image must decode, not merely be asked for.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAPAAAAB4CAIAAABD1OhwAAABSUlEQVR42u3boQ0AIAxFwQ6GRrM4BsE8WCQSyiU3AXnulyi1QRrhCRA0CBoEDYJG0Js+JjxB0AgaBA2CBkEjaEEjaBA0CBoEjaAFjaBB0HBr0A4UbyNoQQta0IIWtKARtKARtKAFLWhBC1rQgha0oBG0oBG06RtBCxpBg6BB0CBoBC1oBA2CBkGDoBG0oBE0CBqcj+LkVdAIWtAIGgSNoAWNoAWNoAVN9qDBUgiCBkEjaEEjaBA0CBoEjaAFjR8rGKgFjaAFjaAFjaAFjaARtKARtKARtGEFwwoIGgQNggZBI2gQNAgaBI2gTd9mZEELWtCCRtCCFjSCFrSgBS1oQQta0IJG0IYVEDSCFjSCBkGDoEHQCFrQCBoEDYIGQSNoQSNoEDQIGgSNoL0UggZBg6BB0AgaBA2CBkHDWdDwLkEjaBA0CBoEzccWLR2RxPPNFDsAAAAASUVORK5CYII=', 'base64');

const shot = async (page, name) => {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, {recursive: true});
  await page.waitForTimeout(150);
  await page.screenshot({path: path.join(SHOTS, name + '.png')});
};
const go = (page, hash) => page.evaluate(h => { location.hash = h; }, hash);
const names = page => page.locator('#ops-table tr[data-ops-row] .ops-name').allTextContents();
// A filter's list comes from the server: wait for the read carrying the param, then for the table it drew.
async function pick(page, key, value, param, want) {
  const read = page.waitForResponse(r => r.url().includes(param) && r.ok(), {timeout: t(15000)});
  await page.locator(`[data-ops-f="${key}"]`).selectOption(value);
  await read;
  await page.waitForFunction(w => JSON.stringify([...document.querySelectorAll('#ops-table tr[data-ops-row] .ops-name')].map(a => a.textContent)) === w,
    JSON.stringify(want), {timeout: t(15000)});
}
async function clear(page, key) { await page.locator(`[data-ops-f="${key}"]`).selectOption(''); }
const tabReady = (page, tab) => page.locator(`.ops-t-${tab} tr[data-ops-row]`).first().waitFor({timeout: t(20000)});

(async () => {
  const hub = await startHub({seeds: ['org', 'toured', 'operations']});
  let browser;
  const api = async (who, method, route, body) => {
    const res = await fetch(hub.url + '/api' + route, {method, headers: {Authorization: 'Bearer ' + hub.tokens[who], 'Content-Type': 'application/json',
      ...(method === 'GET' ? {} : {'Idempotency-Key': `ops-${Date.now()}-${Math.random()}`})}, body: body ? JSON.stringify(body) : undefined});
    const out = await res.json().catch(() => ({}));
    return {status: res.status, body: out};
  };
  try {
    browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
    const customer = (await api('dana', 'GET', '/v2/customers?q=Beta')).body.customers[0];
    assert.equal(customer.name, 'Beta Foods');
    // An estimate tagged to the customer's site, for the panels' document sections (which filter by the lines' tags).
    const betaSite = (await api('dana', 'GET', '/v2/locations?q=Beta')).body.locations[0];
    const income = (await api('dana', 'GET', '/v2/finance/chart?for=journal')).body.accounts.find(a => a.number === '4000');
    const est = await api('dana', 'POST', '/v2/finance/actions', {kind: 'estimate_create', payload: {customer_id: customer.id, estimate_date: '2026-10-06', number: 'EST-77',
      lines: [{account_id: income.id, amount: 12000, description: 'Coil swap', tags: {location: betaSite.id}}]}});
    assert.equal(est.body.status, 'committed', JSON.stringify(est.body));
    const finAct = async (kind, payload) => {
      const out = await api('dana', 'POST', '/v2/finance/actions', {kind, payload});
      assert.equal(out.body.status, 'committed', kind + ' ' + JSON.stringify(out.body));
      return out.body.result;
    };
    const chart = (await api('dana', 'GET', '/v2/finance/chart?for=journal')).body.accounts;
    const bank = chart.find(a => a.number === '1000'), costs = chart.find(a => a.type === 'expense' && a.parent_id);
    const vendor = (await finAct('contact_create', {name: 'Gamma Supply', kind: 'vendor'})).contact_id;
    await finAct('po_create', {vendor_id: vendor, po_date: '2026-10-06', number: 'PO-77', lines: [{account_id: costs.id, amount: 5000, tags: {location: betaSite.id}}]});
    const inv = await finAct('invoice_create', {customer_id: customer.id, invoice_date: '2026-10-06', number: 'INV-77', lines: [{account_id: income.id, amount: 9000, tags: {location: betaSite.id}}]});
    await finAct('invoice_send', {invoice_id: inv.invoice_id});
    // Two lines on the project, for its P&L by account.
    const job = (await api('dana', 'GET', '/v2/projects?q=North')).body.projects[0];
    await finAct('journal', {date: '2026-10-06', memo: 'Job sale', lines: [{account_id: income.id, amount: -30000, tags: {project: job.id}}, {account_id: bank.id, amount: 30000}]});
    await finAct('journal', {date: '2026-10-06', memo: 'Job cost', lines: [{account_id: costs.id, amount: 7000, tags: {project: job.id}}, {account_id: bank.id, amount: -7000}]});

    // The owner: each tab's server filters, and the panels' links.
    {
      const page = await pageAs(browser, hub, 'dana');
      const errors = []; page.on('pageerror', e => errors.push(String(e)));
      await go(page, '#/operations/locations');
      await tabReady(page, 'locations');
      assert.deepEqual(await names(page), ['Acme North Store', 'Beta Foods Site']);
      await pick(page, 'customer', customer.id, 'customer_id=', ['Beta Foods Site']);
      await clear(page, 'customer');
      await pick(page, 'kind', 'store', 'kind=store', ['Acme North Store']);
      await clear(page, 'kind');
      await shot(page, 'real-desktop-locations');

      await page.locator('#ops-table').getByText('Acme North Store').click();
      await page.locator('#ops-panel h2').waitFor();
      assert.match(await page.locator('#ops-panel .ops-facts').innerText(), /1 Main St/);
      await page.locator('[data-ops-pnl]').waitFor();
      assert.equal(await page.locator('[data-ops-pnl]').getAttribute('href'), '#/finance/pnl?split=location');
      await shot(page, 'real-desktop-location-panel');
      for (const doc of ['EST-77', 'PO-77', 'INV-77']) assert.equal(await page.locator('#ops-panel').getByText(doc).count(), 0, doc + ': only the site it was tagged to');
      assert.equal(await page.locator('th', {hasText: 'In books'}).count(), 0, 'a native book has no In books column');
      assert.doesNotMatch(await page.locator('#ops-panel .ops-facts').innerText(), /In books/);
      await page.locator('#ops-table').getByText('Beta Foods Site').click();
      for (const doc of ['EST-77', 'PO-77', 'INV-77']) await page.locator('#ops-panel').getByText(doc).waitFor({timeout: t(15000)});
      await shot(page, 'real-desktop-location-docs');

      await go(page, '#/operations/items');
      await tabReady(page, 'items');
      assert.match(await page.locator('tr[data-ops-row]', {hasText: 'Filter change'}).innerText(), /\$89/);
      await pick(page, 'kind', 'service', 'kind=service', ['Filter change']);
      await clear(page, 'kind');
      await page.locator('#ops-table').getByText('Filter change').click();
      await page.locator('#ops-panel h2').waitFor();
      const item = await page.locator('#ops-panel .ops-facts').innerText();
      assert.match(item, /Swap and dispose/);
      assert.match(item, /Taxable\s*Yes/);
      await shot(page, 'real-desktop-item-panel');

      await go(page, '#/operations/projects');
      await tabReady(page, 'projects');
      await pick(page, 'status', 'closed', 'status=closed', ['Old refit']);
      await clear(page, 'status');
      await pick(page, 'customer', customer.id, 'customer_id=', ['North Store refit']);
      await clear(page, 'customer');
      await page.locator('#ops-table').getByText('North Store refit').click();
      await page.locator('[data-ops-pnl]').waitFor();
      assert.equal(await page.locator('[data-ops-pnl]').getAttribute('href'), '#/finance/pnl?split=project');
      const accounts = await page.locator('[data-ops-pnl-accounts]').innerText();
      assert.ok(accounts.includes(income.name) && accounts.includes(costs.name), accounts);
      assert.match(accounts, /\$300/);
      await shot(page, 'real-desktop-project-panel');

      await go(page, '#/operations/equipment');
      await tabReady(page, 'equipment');
      assert.deepEqual(await names(page), ['Rooftop unit 3', 'Walk-in cooler']);
      await pick(page, 'customer', customer.id, 'customer_id=', ['Rooftop unit 3']);
      await clear(page, 'customer');
      const site = (await api('dana', 'GET', '/v2/locations?q=Beta')).body.locations[0];
      await pick(page, 'location', site.id, 'location_id=', ['Rooftop unit 3']);
      await clear(page, 'location');
      await pick(page, 'due', 'overdue', 'due=overdue', ['Rooftop unit 3']);
      await pick(page, 'due', 'warranty', 'warranty=ending', ['Rooftop unit 3']);
      await clear(page, 'due');
      await page.waitForFunction(() => document.querySelectorAll('#ops-table tr[data-ops-row]').length === 2);
      assert.match(await page.locator('tr[data-ops-row]', {hasText: 'Rooftop unit 3'}).innerText(), /Beta Foods/);
      await shot(page, 'real-desktop-equipment');

      // Due dates: the history comes from the detail read, and each write is the action's real payload.
      await page.locator('#ops-table').getByText('Rooftop unit 3').click();
      const row = page.locator('.ops-due', {hasText: 'Filter change'});
      await row.waitFor();
      assert.match(await row.innerText(), /Overdue/);
      const first = await row.locator('[data-due-done]').getAttribute('data-on');
      const nextDue = () => page.locator('tr[data-ops-row]', {hasText: 'Rooftop unit 3'}).locator('td[data-l="Next due"]').innerText();
      const before = await nextDue();
      await row.locator('[data-due-done]').click();
      await page.waitForFunction(() => document.querySelectorAll('[data-due-reopen]').length === 1, null, {timeout: t(15000)});
      await page.waitForFunction(was => document.querySelector('tr.ops-on td[data-l="Next due"]')?.innerText !== was, before, {timeout: t(15000)});
      await page.locator('.ops-due summary').click();
      assert.equal(await page.locator('[data-due-reopen]').getAttribute('data-on'), first, 'history lists the day just done');
      await shot(page, 'real-desktop-equipment-panel');
      await page.locator('[data-due-reopen]').click();
      await page.waitForFunction(() => !document.querySelector('[data-due-reopen]'), null, {timeout: t(15000)});
      await page.locator('[data-due-add]').click();
      await page.locator('dialog.fin-dlg [name=name]').fill('Coil cleaning');
      await page.locator('dialog.fin-dlg [type=submit]').click();
      await page.locator('.ops-due', {hasText: 'Coil cleaning'}).waitFor({timeout: t(15000)});
      const coil = page.locator('.ops-due', {hasText: 'Coil cleaning'});
      await coil.locator('summary').click();
      await coil.locator('[data-due-stop]').click();
      await coil.waitFor({state: 'detached', timeout: t(15000)});
      assert.doesNotMatch(await page.locator('#ops-panel').innerText(), /\brule/i);

      // A form writes every field it shows, and the panel reads them back.
      await go(page, '#/operations/locations');
      await tabReady(page, 'locations');
      await page.locator('[data-ops-add]').click();
      const form = page.locator('dialog.fin-dlg');
      await form.locator('[name=name]').fill('Acme South Yard');
      await form.locator('[name=kind]').selectOption('warehouse');
      await form.locator('[name=customer_id]').selectOption(customer.id);
      await form.locator('[name=street]').fill('9 Dock Rd');
      await form.locator('[name=city]').fill('Salem');
      await form.locator('[type=submit]').click();
      await page.locator('#ops-table').getByText('Acme South Yard').click();
      await page.locator('#ops-panel h2', {hasText: 'Acme South Yard'}).waitFor();
      await page.locator('#ops-panel').getByText('9 Dock Rd').waitFor();

      // The customer's panel, from a location's panel, as the owner.
      await page.locator('#ops-panel [data-ops-customer]').click();
      await page.locator('[data-ops-customer-panel]').waitFor();
      assert.equal(await page.locator('#ops-panel h2').innerText(), 'Beta Foods');
      await page.locator('#ops-panel').getByText('EST-77').waitFor({timeout: t(15000)});
      await shot(page, 'real-desktop-customer-panel-owner');
      assert.deepEqual(errors, [], 'owner page errors');
      await page.context().close();
    }

    // H7a: a teammate with no Finance view opens a customer's panel: sites, equipment and due dates, no documents.
    {
      assert.equal((await api('priya', 'GET', '/v2/finance/invoices')).status, 403, 'Priya has no receivables view');
      const page = await pageAs(browser, hub, 'priya');
      const errors = []; page.on('pageerror', e => errors.push(String(e)));
      await go(page, '#/operations/equipment');
      await tabReady(page, 'equipment');
      assert.equal(await page.locator('[data-ops-add]').count(), 0, 'no write controls without the records right');
      await page.locator('#ops-table').getByText('Rooftop unit 3').click();
      await page.locator('#ops-panel [data-ops-customer]').click();
      await page.locator('[data-ops-customer-panel]').waitFor();
      const panel = page.locator('#ops-panel');
      assert.equal(await panel.locator('h2').innerText(), 'Beta Foods');
      const heads = await panel.locator('.ops-sec-h h3').allTextContents();
      assert.deepEqual(heads, ['Locations', 'Equipment', 'Due dates'], 'H7a: no estimates or invoices');
      const text = await panel.innerText();
      for (const want of ['Beta Foods Site', 'Rooftop unit 3', 'Filter change', 'Fire inspection']) assert.ok(text.includes(want), want);
      assert.ok(!text.includes('EST-77'), 'H7a: the estimate stays out');
      await shot(page, 'real-desktop-customer-panel-teammate');
      await go(page, '#/operations/projects');
      await tabReady(page, 'projects');
      await page.locator('#ops-table').getByText('North Store refit').click();
      await page.locator('#ops-panel h2', {hasText: 'North Store refit'}).waitFor({timeout: t(20000)});
      await page.waitForTimeout(800);
      assert.equal(await page.locator('[data-ops-pnl-accounts]').count(), 0, 'no accounts for a viewer with no data');
      // Back to the location it names, then the customer's name again, on a phone.
      await page.context().close();
      const phone = await pageAs(browser, hub, 'priya', {phone: true});
      await go(phone, `#/operations/locations?customer=${encodeURIComponent(customer.id)}`);
      await phone.locator('[data-ops-customer-panel]').waitFor({timeout: t(20000)});
      assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth), 0, 'no sideways scroll');
      await shot(phone, 'real-phone-customer-panel-teammate');
      await phone.context().close();
      assert.deepEqual(errors, [], 'teammate page errors');
    }

    // H7: the bot saves the plate as a Library file and files equipment_create with it; the owner sees the photo on the approval.
    {
      // No finance.records: a real bot's equipment_create asks the owner (K.authority). The plate is a Library file it can read.
      const up = await fetch(hub.url + '/api/v2/files/uploads?name=plate.png&title=Data%20plate&scope=bot', {method: 'POST', body: PNG,
        headers: {Authorization: 'Bearer ' + hub.tokens['engineering-lead'], 'Content-Type': 'image/png', 'Idempotency-Key': 'ops-photo-file'}});
      const file = await up.json();
      assert.ok(up.ok, JSON.stringify(file));
      const attachment = file.file || file;
      assert.ok(attachment.id, JSON.stringify(file).slice(0, 300));
      const asked = await api('engineering-lead', 'POST', '/v2/finance/actions', {kind: 'equipment_create', payload: {name: 'Rooftop unit 9', kind: 'owned', make: 'Trane', serial: 'SN-9', photo_file_id: attachment.id}});
      assert.equal(asked.status < 300, true, JSON.stringify(asked.body));
      assert.equal(asked.body.status, 'needs_approval', 'a bot photo waits for a person: ' + JSON.stringify(asked.body));
      const none = (await api('dana', 'GET', '/v2/equipment?q=Rooftop%20unit%209')).body.equipment;
      assert.deepEqual(none, [], 'not saved before a person confirms');

      const page = await pageAs(browser, hub, 'dana');
      const errors = []; page.on('pageerror', e => errors.push(String(e)));
      await go(page, '#/tasks');
      await page.locator('#task-view [data-view="foryou"]').click();
      await page.locator('[data-need-card^="approval:"]').first().click({timeout: t(20000)});
      const img = page.locator('.approval-photo img').first();
      await img.waitFor({timeout: t(20000)});
      await img.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => { const i = document.querySelector('.approval-photo img'); return i?.complete && i.naturalWidth > 0; }, null, {timeout: t(15000)});
      await page.waitForTimeout(400);   // the viewer's link thumbnails come on a timer
      assert.equal(await page.locator('#need-card .approval-payload img').count(), 1, 'the photo shows once');
      await shot(page, 'real-desktop-approval-photo');
      assert.match(await page.locator('#need-card .dc-card').innerText(), /Rooftop unit 9/);
      await page.locator('#need-card button', {hasText: 'Approve'}).first().click();
      await page.locator('#need-card .dc-card .approval-photo').waitFor({state: 'detached', timeout: t(15000)});
      let saved = [];
      for (let i = 0; i < 40 && !saved.length; i++) {   // the approved action runs just after the decision answers
        saved = (await api('dana', 'GET', '/v2/equipment?q=Rooftop%20unit%209')).body.equipment;
        if (!saved.length) await new Promise(r => setTimeout(r, 250));
      }
      assert.equal(saved.length, 1, 'approving saves it: ' + JSON.stringify((await api('dana', 'GET', '/v2/finance/actions/' + asked.body.action_id)).body).slice(0, 400));
      assert.equal(saved[0].photo_file_id, attachment.id);
      assert.deepEqual(errors, [], 'approval page errors');
      await page.context().close();
    }
    console.log('operations-real: ok');
  } finally {
    await browser?.close();
    await hub.stop();
  }
})().catch(error => { console.error(error); process.exit(1); });
