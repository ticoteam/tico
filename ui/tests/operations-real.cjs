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
// One real 1x1 PNG, scaled up on screen: the approval's image must decode, not merely be asked for.
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

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
  const hub = await startHub({seeds: ['org', 'operations']});
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
      await row.locator('[data-due-done]').click();
      await page.waitForFunction(() => document.querySelectorAll('[data-due-reopen]').length === 1, null, {timeout: t(15000)});
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
      await shot(page, 'real-desktop-customer-panel-teammate');
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

    // H7: the photo comes in on a task, as + Add from photo sends it; the bot files equipment_create; the owner sees the photo.
    {
      const form = new FormData();
      form.append('title', 'Draft equipment from a photo'); form.append('body', 'Read the plate.'); form.append('owner', 'engineering-lead');
      form.append('files', new Blob([PNG], {type: 'image/png'}), 'plate.png');
      const up = await fetch(hub.url + '/api/v2/uploads/tasks', {method: 'POST', headers: {Authorization: 'Bearer ' + hub.tokens.dana, 'Idempotency-Key': 'ops-photo-task'}, body: form});
      const task = await up.json();
      assert.ok(up.ok, JSON.stringify(task));
      const attachment = (task.message || task.task?.message || task).refs?.attachments?.[0] || task.task?.refs?.attachments?.[0];
      assert.ok(attachment?.id, 'the task carries the photo: ' + JSON.stringify(task).slice(0, 300));
      const asked = await api('engineering-lead', 'POST', '/v2/finance/actions', {kind: 'equipment_create', payload: {name: 'Rooftop unit 9', kind: 'owned', make: 'Trane', serial: 'SN-9', photo_file_id: attachment.id}});
      assert.equal(asked.status < 300, true, JSON.stringify(asked.body));
      assert.equal(asked.body.status, 'needs_approval', 'a bot photo waits for a person');
      const none = (await api('dana', 'GET', '/v2/equipment?q=Rooftop%20unit%209')).body.equipment;
      assert.deepEqual(none, [], 'not saved before a person confirms');

      const page = await pageAs(browser, hub, 'dana');
      const errors = []; page.on('pageerror', e => errors.push(String(e)));
      await go(page, '#/tasks');
      await page.locator('#task-view [data-view="foryou"]').click();
      await page.locator('[data-needs-row], .needs-row, .row', {hasText: 'Rooftop unit 9'}).first().click();
      const img = page.locator('.approval-photo img').first();
      await img.waitFor({timeout: t(20000)});
      await img.scrollIntoViewIfNeeded();
      await page.waitForFunction(() => { const i = document.querySelector('.approval-photo img'); return i?.complete && i.naturalWidth > 0; }, null, {timeout: t(15000)});
      await shot(page, 'real-desktop-approval-photo');
      await page.locator('.approval-payload').first().locator('xpath=ancestor-or-self::*[.//button[contains(., "Approve")]][1]').locator('button', {hasText: 'Approve'}).first().click();
      await page.waitForFunction(async () => (await (await fetch('/api/v2/equipment?q=Rooftop%20unit%209')).json()).equipment?.length === 1, null, {timeout: t(15000)}).catch(() => {});
      const saved = (await api('dana', 'GET', '/v2/equipment?q=Rooftop%20unit%209')).body.equipment;
      assert.equal(saved.length, 1, 'approving saves it');
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
