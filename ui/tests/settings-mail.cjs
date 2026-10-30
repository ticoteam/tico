// Settings > Mail (ui/app/settings-mail.js) against a fake server. Guards: addresses list state, kind, the full
// address only once active, registered agent and Form 1583 status, with open orders as Pending; Get an address shows
// each kind's price (Tico Gateway's `prices` win, the office lease falls back to $437.50/mo) and line, offers an office lease only in CA, DE and FL, and
// sends the order once with an Idempotency-Key, after which it shows as Pending; the intake inbox is picked from the
// company's email inboxes, and a failed save goes back to the last saved inbox; a failed letter is retried by an owner, and its task is a link. A member reads only.
// Without Tico Gateway the addresses card says so and offers no order. No sideways scroll on a phone.
// TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const shots = process.env.TICO_SHOTS || '';
const AGO = m => new Date(Date.now() - m * 60000).toISOString();

function server() {
  return {
    role: 'owner',
    gateway: true,
    addresses: [
      {assignment_id: 'as_1', state: 'DE', kind: 'shared', address: '8 The Green, Suite 4000, Dover DE 19901', recipient_name: 'Acme Inc',
        status: 'active', agent: true, started_at: AGO(9000)},
      {assignment_id: 'as_2', state: 'TX', kind: 'private', address: '100 Keller Pkwy, Suite 220, Keller TX 76248', recipient_name: 'Acme Inc',
        status: 'pending_1583', agent: false, started_at: AGO(60)},
    ],
    orders: [{order_id: 'mo_1', kind: 'shared', state: 'NV', company_name: 'Acme Inc', status: 'open', created_at: AGO(30)},
      {order_id: 'mo_0', kind: 'shared', state: 'DE', company_name: 'Acme Inc', status: 'fulfilled', assignment_id: 'as_1', created_at: AGO(9100)}],
    prices: {shared: 12, private: '25'},
    intakeFail: false,
    intake: null,
    inboxes: [{inbox_id: 'ib_mail', kind: 'email', name: 'Mail', address: 'mail@acme.example'},
      {inbox_id: 'ib_phone', kind: 'phone', name: 'Front desk', address: '+15550100'}],
    items: [
      {id: 'pm_3', received_at: AGO(20), kind: null, summary: '', sender: '', status: 'failed', task_id: null, attempts: 5, error: 'unreadable'},
      {id: 'pm_2', received_at: AGO(1500), kind: 'legal', summary: 'Summons, answer within 21 days', sender: 'Superior Court of California',
        status: 'filed', task_id: 't-42', attempts: 1, error: ''},
      {id: 'pm_1', received_at: AGO(4000), kind: 'junk', summary: 'Credit card offer', sender: 'Big Bank', status: 'archived', task_id: null, attempts: 1, error: ''},
    ],
    sent: [],
  };
}

async function open(browser, viewport = {width: 1280, height: 860}, state = server()) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1])))
      return route.fulfill({contentType: ui[1].endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]), 'utf8')});
    if (p.startsWith('/vendor/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({body: fs.readFileSync(uiFile(p.slice(1))),
      contentType: p.endsWith('.woff2') ? 'font/woff2' : 'application/javascript'});
    if (p.endsWith('.js')) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const config = {version: '0.5.14', app_name: 'Tico', company_name: 'Acme Inc'};
    if (method !== 'GET' && p.startsWith('/api/v2/postal/'))
      state.sent.push({method, path: p, body: req.postDataJSON(), key: req.headers()['idempotency-key'] || ''});
    if (p === '/api/me') return json({id: 'ana', role: state.role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (p === '/api/v2/operations') return json({machines: [], services: []});
    if (p === '/api/v2/postal/addresses') {
      if (!state.gateway) return json({error: {code: 'gateway', detail: 'Connect Tico Gateway first'}}, 409);
      return json({addresses: state.addresses, orders: state.orders, prices: state.prices});
    }
    if (p === '/api/v2/postal/orders') {
      const body = req.postDataJSON();
      const order = {order_id: 'mo_' + (state.orders.length + 1), ...body, status: 'open', assignment_id: null, created_at: AGO(0), decided_at: null};
      state.orders.push(order);
      return json({order});
    }
    if (p === '/api/v2/postal/intake') {
      if (method === 'PUT') {
        if (state.intakeFail) return json({error: {code: 'bad_request', detail: 'Not saved'}}, 422);
        const id = req.postDataJSON().inbox_id, box = state.inboxes.find(i => i.inbox_id === id);
        state.intake = id ? {inbox_id: id, address: box.address, set_by: 'human:ana', set_at: AGO(0)} : null;
      }
      return json({intake: state.intake});
    }
    if (p === '/api/v2/inbound/inboxes') return json({inboxes: state.inboxes, next: null, configured: true, gaps: [], last_sync: null, error: null});
    if (p === '/api/v2/postal/items') return json({items: state.items, next: null});
    const retry = p.match(/^\/api\/v2\/postal\/items\/([^/]+)\/retry$/);
    if (retry) {
      Object.assign(state.items.find(m => m.id === retry[1]), {status: 'filed', kind: 'other', sender: 'Unknown', summary: 'Filed unread for the owner', task_id: 't-43'});
      return json({ok: true});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('https://tico-ui.test/#/settings/mail');
  return {page, errors, state};
}
const noSideways = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const rowText = async row => (await row.innerText()).replace(/\s+/g, ' ').trim();

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const shot = async (page, name) => {
    if (!shots) return;
    for (const mode of ['dark', 'light']) {
      await page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
      await page.locator('#settings-mail').screenshot({path: path.join(shots, `mail-${name}-${mode}.png`)});
    }
  };
  try {
    {
      // The owner: addresses, ordering, the intake inbox and a retry.
      const {page, errors, state} = await open(browser);
      const pane = page.locator('#settings-mail');
      await pane.locator('[data-pm-address="as_1"]').waitFor();
      assert.equal(await page.locator('[data-settings-tab="mail"]').getAttribute('aria-selected'), 'true', 'Mail is a Settings tab');
      assert.equal(await rowText(pane.locator('[data-pm-address="as_1"]')), 'Delaware Shared 8 The Green, Suite 4000, Dover DE 19901 Yes Active');
      const waiting = pane.locator('[data-pm-address="as_2"]');
      assert.equal(await rowText(waiting), 'Texas Private — No Waiting for Form 1583', 'no address before Form 1583');
      assert.ok(!(await pane.innerText()).includes('Keller Pkwy'));
      assert.equal(await rowText(pane.locator('[data-pm-pending="mo_1"]')), 'Nevada Shared — Pending', 'an open order is pending');
      assert.equal(await pane.locator('[data-pm-pending="mo_0"]').count(), 0, 'a fulfilled order is its address');
      // Letters: newest first, the failed one has Retry, a filed one links to its task, junk reads Archived.
      await pane.locator('[data-pm-letter="pm_2"]').waitFor();
      assert.equal(await pane.locator('[data-pm-letter="pm_2"] [data-pm-task]').getAttribute('href'), '#/task/t-42');
      assert.match(await rowText(pane.locator('[data-pm-letter="pm_2"]')), /Superior Court of California Summons, answer within 21 days Legal Task Filed$/);
      assert.match(await rowText(pane.locator('[data-pm-letter="pm_1"]')), /Big Bank Credit card offer Junk Archived Archived$/);
      assert.equal(await pane.locator('[data-pm-retry]').count(), 1, 'only the failed letter offers Retry');
      // The intake inbox: only email inboxes are offered.
      const intake = pane.locator('[data-pm-intake-select]');
      assert.deepEqual(await intake.locator('option').allInnerTexts(), ['None', 'Mail · mail@acme.example']);
      await shot(page, 'desktop');
      await intake.selectOption('ib_mail');
      await page.locator('.toast', {hasText: 'Saved'}).waitFor();
      assert.deepEqual(state.sent.find(s => s.path === '/api/v2/postal/intake')?.body, {inbox_id: 'ib_mail'});
      // A refused change goes back to the inbox saved a moment ago, not to the one the page loaded with (None).
      state.intakeFail = true;
      await intake.selectOption('');
      await pane.locator('#settings-mail-intake [data-field-err]').waitFor();
      assert.equal(await intake.inputValue(), 'ib_mail', 'a failed save restores the last saved inbox');
      state.intakeFail = false;
      // Get an address: prices and lines, lease only where it is sold, then one order.
      await pane.locator('[data-pm-order]').click();
      const dialog = page.locator('.pm-dialog');
      await dialog.waitFor();
      const kind = k => dialog.locator(`[data-pm-kind="${k}"]`);
      assert.equal(await rowText(kind('shared')), 'Shared $12/mo Mailing address and registered agent', "Tico Gateway's price wins");
      assert.equal(await kind('private').locator('[data-pm-price]').innerText(), '$25/mo', 'a price sent as text reads the same');
      assert.equal(await rowText(kind('lease')), 'Office lease $437.50/mo For bank accounts, Amazon, Stripe; billed yearly',
        'no lease price sent: the catalogue price');
      assert.equal(await dialog.locator('[name=company_name]').inputValue(), 'Acme Inc');
      await dialog.locator('[name=state]').selectOption('TX');
      assert.equal(await kind('lease').locator('input').isDisabled(), true, 'no office lease in Texas');
      await dialog.locator('[name=state]').selectOption('CA');
      assert.equal(await kind('lease').locator('input').isDisabled(), false);
      await kind('lease').locator('input').check();
      if (shots) await dialog.screenshot({path: path.join(shots, 'mail-order-dark.png')});
      await dialog.locator('[data-pm-submit]').click();
      await pane.locator('[data-pm-pending="mo_3"]').waitFor();
      const orders = state.sent.filter(s => s.path === '/api/v2/postal/orders');
      assert.equal(orders.length, 1, 'one order');
      assert.deepEqual(orders[0].body, {kind: 'lease', state: 'CA', company_name: 'Acme Inc'});
      assert.ok(orders[0].key, 'the order carries an Idempotency-Key');
      assert.equal(await rowText(pane.locator('[data-pm-pending="mo_3"]')), 'California Office lease — Pending');
      await page.locator('.pm-dialog').waitFor({state: 'detached'});   // the dialog closes
      // Retry the failed letter: it is asked for once, and the list redraws with what the server says now.
      await pane.locator('[data-pm-retry="pm_3"]').click();
      await pane.locator('[data-pm-letter="pm_3"] [data-pm-task]').waitFor();
      assert.deepEqual(state.sent.filter(s => s.path.endsWith('/retry')).map(s => s.path), ['/api/v2/postal/items/pm_3/retry']);
      assert.equal(await pane.locator('[data-pm-retry]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member reads: no order, no inbox picker, no retry.
      const state = Object.assign(server(), {role: 'member', intake: {inbox_id: 'ib_mail', address: 'mail@acme.example'}});
      const {page, errors} = await open(browser, undefined, state);
      const pane = page.locator('#settings-mail');
      await pane.locator('[data-pm-letter="pm_3"]').waitFor();
      await pane.locator('[data-pm-intake-shown]').waitFor();
      assert.equal(await pane.locator('[data-pm-order]').count(), 0);
      assert.equal(await pane.locator('[data-pm-retry]').count(), 0);
      assert.equal(await pane.locator('[data-pm-intake-shown]').innerText(), 'mail@acme.example');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // No Tico Gateway: the card says so, with the way to connect, and nothing can be ordered.
      const {page, errors} = await open(browser, undefined, Object.assign(server(), {gateway: false}));
      const pane = page.locator('#settings-mail');
      await pane.locator('[data-pm-gateway]').waitFor();
      assert.equal(await pane.locator('[data-pm-gateway]').innerText(), 'Connect Tico Gateway first. Key and connection');
      assert.equal(await pane.locator('[data-pm-order]').isDisabled(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone: nothing sideways, the dialog fits. Every price here is Tico Gateway's, in dollars.
      const {page, errors} = await open(browser, {width: 390, height: 844}, Object.assign(server(), {prices: {shared: 10, private: 25, lease: 437.5}}));
      const pane = page.locator('#settings-mail');
      await pane.locator('[data-pm-address="as_1"]').waitFor();
      await pane.locator('[data-pm-letter="pm_2"]').waitFor();
      await pane.locator('[data-pm-intake-select]').waitFor();
      assert.ok(await noSideways(page), 'Mail fits a phone');
      await shot(page, 'phone');
      await pane.locator('[data-pm-order]').click();
      const dialog = page.locator('.pm-dialog');
      await dialog.waitFor();
      assert.ok(await dialog.evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'the order form fits');
      assert.deepEqual(await dialog.locator('[data-pm-price]').allInnerTexts(), ['$10/mo', '$25/mo', '$437.50/mo']);
      if (shots) await dialog.screenshot({path: path.join(shots, 'mail-order-phone.png')});
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('settings-mail ok');
})().catch(error => { console.error(error); process.exit(1); });
