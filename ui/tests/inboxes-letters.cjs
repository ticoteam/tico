// Letters in (ui/app/inboxes-letters.js) in Settings > Inboxes against a fake server. Guards: Inboxes > Settings > Letters
// in (where the old Mail address lands) lists each address's state, kind, the full address only once active, registered
// agent and Form 1583 status, with open orders as Pending, each linking to its page; Get an address shows shared and
// private, each with its price and line, and the scan price (Tico Gateway's `prices` win, else Tico's own), and sends
// the order once with an Idempotency-Key, after which it shows as Pending; the intake inbox is picked from the company's
// email inboxes and needs a sender, and a failed save goes back to the last saved inbox. An address's page shows its
// Form 1583 progress and its letters (sender, summary, PDF, task), with no scan or forward request; a failed letter is
// retried by an owner. Mail by email turned off stays listed as Not in use while it has letters, and its page keeps
// them, with Retry. Read grants: Who can use it on all addresses (Letters in) and on one address's page gives Read,
// with an Idempotency-Key, lists it and takes it back; a pick waiting for its button survives a redraw; a bot that can't
// take mail can't be picked. Legal mail goes to Counsel by its stable id, a refused change goes back to the saved one,
// and a read from before a save can't put the old choice back. A member reads only: no grants, Legal mail as text.
// Without Tico Gateway the addresses card says so, offers no order, and the list shows no error. No sideways scroll on a
// phone. TICO_SHOTS=<dir> saves screenshots (desktop and phone, dark and light).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cjs');
const {unrouted} = require('./support/routes.cjs');
const {googleFake} = require('./support/google-fake.cjs');
const {mailboxesFake} = require('./support/mailboxes-fake.cjs');
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
    prices: {shared: 12, private: '25', scan: 1.5, page_over_10: '0.3'},
    intakeFail: false,
    intake: null,
    legal_bot_id: null,
    routingFail: false,
    holdRouting: null,
    grants: [],
    got: {},
    inboxes: [{inbox_id: 'ib_mail', kind: 'email', name: 'Mail', address: 'mail@acme.example', state: 'active'},
      {inbox_id: 'ib_phone', kind: 'sms', name: 'Front desk', address: '+15550100', state: 'active'}],
    items: [
      {id: 'pm_3', received_at: AGO(20), kind: null, summary: '', sender: '', status: 'failed', task_id: null, attempts: 5, error: 'unreadable'},
      {id: 'pm_2', received_at: AGO(1500), kind: 'legal', summary: 'Summons, answer within 21 days', sender: 'Superior Court of California',
        status: 'filed', task_id: 't-42', attempts: 1, error: '', file_url: '/api/v2/postal/items/pm_2/file'},
      {id: 'pm_1', received_at: AGO(4000), kind: 'junk', summary: 'Credit card offer', sender: 'Big Bank', status: 'archived', task_id: null, attempts: 1, error: ''},
    ],
    sent: [],
  };
}

async function open(browser, {viewport = {width: 1280, height: 860}, state = server(), hash = '#/settings/mail'} = {}) {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  const errors = [], mailboxes = mailboxesFake({admin: state.role === 'owner'}), google = googleFake();
  page.on('pageerror', e => errors.push(e.message));
  await page.addInitScript(() => {
    try { localStorage.setItem('tico.inboxes.skip', '1'); } catch { /* storage off */ }
  });
  await page.route('**/*', async route => {
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
    const body = req.postData() ? req.postDataJSON() : null;
    if (method !== 'GET' && p.startsWith('/api/v2/postal/'))
      state.sent.push({method, path: p, body, key: req.headers()['idempotency-key'] || ''});
    if (method === 'GET') state.got[p] = (state.got[p] || 0) + 1;
    const admin = state.role === 'owner', refuse = () => json({error: {code: 'forbidden', detail: 'An owner or admin reads mail grants'}}, 403);
    if (p === '/api/me') return json({id: 'ana', role: state.role, name: 'Ana', email: 'ana@acme.example', cloud: true, registered: true, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/issues') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/needs-you') return json({items: []});
    if (p === '/api/v2/operations') return json({machines: [], services: []});
    if (p.startsWith('/api/v2/mailboxes')) { const answer = mailboxes.answer(p, method, body); if (answer !== undefined) return json(answer); }
    if (p.startsWith('/api/v2/google')) { const answer = google.answer(p, req.url(), method, body); if (answer !== undefined) return json(answer); }
    if (p === '/api/v2/postal/addresses') {
      if (!state.gateway) return json({error: {code: 'gateway', detail: 'Connect Tico Gateway first'}}, 409);
      return json({addresses: state.addresses, orders: state.orders, prices: state.prices});
    }
    if (p === '/api/v2/postal/bots') return admin ? json({bots: [
      {id: 'tico-counsel', slug: 'legal', display_name: 'Counsel', state: 'active', read_error: null, routing_error: null},
      {id: 'synced', slug: 'synced', display_name: 'Chat bot', state: 'active', read_error: 'Physical mail is unavailable: this bot only syncs chat',
        routing_error: 'Physical mail is unavailable: this bot only syncs chat'},
    ], next: null}) : refuse();
    if (p === '/api/v2/postal/settings') {
      if (method === 'PUT') {
        if (state.holdRouting) await state.holdRouting;
        if (state.routingFail) return json({error: {code: 'validation', detail: 'Not saved'}}, 422);
        state.legal_bot_id = body.legal_bot_id;
      }
      return json({settings: {legal_bot_id: state.legal_bot_id}});
    }
    const grant = p.match(/^\/api\/v2\/postal\/addresses\/([^/]+)\/grants(?:\/(bot|person)\/([^/]+))?$/);
    if (grant) {
      if (!admin) return refuse();
      const [address_id, grantee_type, grantee_id] = grant.slice(1).map(x => x && decodeURIComponent(x));
      const same = g => g.address_id === address_id && g.grantee_type === grantee_type && g.grantee_id === grantee_id;
      if (method === 'PUT') {
        const made = state.grants.find(same) || {id: `pmg_${state.grants.length + 1}`, address_id, grantee_type, grantee_id, level: 'read'};
        if (!state.grants.includes(made)) state.grants.push(made);
        return json({grant: made});
      }
      if (method === 'DELETE') { state.grants = state.grants.filter(g => !same(g)); return json({ok: true}); }
      return json({address_id, grants: state.grants.filter(g => g.address_id === address_id), next: null});
    }
    if (p === '/api/v2/postal/orders') {
      const order = {order_id: 'mo_' + (state.orders.length + 1), ...body, status: 'open', assignment_id: null, created_at: AGO(0), decided_at: null};
      state.orders.push(order);
      return json({order});
    }
    if (p === '/api/v2/postal/intake') {
      if (method === 'PUT') {
        if (state.intakeFail) return json({error: {code: 'bad_request', detail: 'Not saved'}}, 422);
        const {inbox_id: id, senders} = body, box = state.inboxes.find(i => i.inbox_id === id);
        state.intake = id ? {inbox_id: id, address: box.address, senders, set_by: 'human:ana', set_at: AGO(0)} : null;
      }
      return json({intake: state.intake});
    }
    if (p === '/api/v2/inbound/inboxes') return json({inboxes: state.inboxes, next: null, configured: true, gaps: [], last_synced_at: null, error: null});
    if (p === '/api/v2/postal/items') return json({items: state.items, next: null});
    const retry = p.match(/^\/api\/v2\/postal\/items\/([^/]+)\/retry$/);
    if (retry) {
      Object.assign(state.items.find(m => m.id === retry[1]), {status: 'filed', kind: 'other', sender: 'Unknown', summary: 'Filed unread for the owner', task_id: 't-43'});
      return json({ok: true});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/${hash}`);
  return {page, errors, state};
}
const noSideways = page => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1);
const rowText = async row => (await row.innerText()).replace(/\s+/g, ' ').trim();
const go = (page, hash) => page.evaluate(h => { location.hash = h; }, hash);
const until = async (ok, what) => {
  for (const end = Date.now() + 5000; !ok();) {
    if (Date.now() > end) throw new Error('timed out: ' + what);
    await new Promise(r => setTimeout(r, 20));
  }
};
const disabled = option => option.evaluate(o => o.disabled);   // the option itself: a label's disabled state follows its select

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
  const shot = async (page, name) => {
    if (!shots) return;
    for (const mode of ['dark', 'light']) {
      await page.evaluate(m => document.documentElement.setAttribute('data-theme', m), mode);
      // The route's fade would otherwise be caught mid-way by the first theme's picture.
      await page.waitForFunction(() => document.getAnimations().every(a => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity));
      await page.locator('#set-inboxes').screenshot({path: path.join(shots, `letters-${name}-${mode}.png`)});
    }
  };
  try {
    {
      // The owner: addresses, the intake inbox, ordering; an address's page with its letters and a retry.
      const {page, errors, state} = await open(browser);
      const pane = page.locator('#inboxes-letters-in');
      await pane.locator('[data-pm-address="as_1"]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/settings/letters-in', 'the old Mail address opens Letters in');
      assert.equal(await page.locator('[data-settings-tab="inboxes"]').getAttribute('aria-selected'), 'true');
      assert.equal(await rowText(pane.locator('[data-pm-address="as_1"]')), 'Delaware Shared 8 The Green, Suite 4000, Dover DE 19901 Yes Connected');
      assert.equal(await pane.locator('[data-pm-address="as_1"] a').getAttribute('href'), '#/settings/inboxes/postal/as_1');
      assert.equal(await rowText(pane.locator('[data-pm-address="as_2"]')), 'Texas Private — No Waiting for Form 1583', 'no address before Form 1583');
      assert.ok(!(await pane.innerText()).includes('Keller Pkwy'));
      assert.equal(await rowText(pane.locator('[data-pm-pending="mo_1"]')), 'Nevada Shared — Pending', 'an open order is pending');
      assert.equal(await pane.locator('[data-pm-pending="mo_0"]').count(), 0, 'a fulfilled order is its address');
      // The intake inbox: only email inboxes are offered.
      const intake = pane.locator('[data-pm-intake-select]');
      assert.deepEqual(await intake.locator('option').allInnerTexts(), ['None', 'Mail · mail@acme.example']);
      await shot(page, 'settings-desktop');
      // Senders alone save nothing; with an inbox picked both go in one save.
      const from = pane.locator('[data-pm-intake-senders]'), puts = () => state.sent.filter(s => s.path === '/api/v2/postal/intake').length;
      await from.fill('Scans@VPM.example, @vpm.example');
      await from.dispatchEvent('change');
      assert.equal(puts(), 0, 'no inbox, nothing saved');
      await Promise.all([
        page.waitForResponse(r => new URL(r.url()).pathname === '/api/v2/postal/intake' && r.request().method() === 'PUT'),
        intake.selectOption('ib_mail'),
      ]);
      assert.equal(puts(), 1, 'one save, though picking the inbox also blurred the senders field');
      assert.deepEqual(state.sent.find(s => s.path === '/api/v2/postal/intake')?.body,
        {inbox_id: 'ib_mail', senders: ['scans@vpm.example', '@vpm.example']});
      // A refused change goes back to the inbox saved a moment ago, not to the one the page loaded with (None).
      state.intakeFail = true;
      await intake.selectOption('');
      await pane.locator('#settings-mail-intake [data-field-err]').waitFor();
      assert.equal(await intake.inputValue(), 'ib_mail', 'a failed save restores the last saved inbox');
      state.intakeFail = false;
      // An inbox needs a sender: clearing them is refused on the page, before anything is sent.
      const before = puts();
      await from.fill('');
      await from.dispatchEvent('change');
      await pane.locator('#settings-mail-intake [data-field-err]', {hasText: 'address or @domain'}).waitFor();
      assert.equal(puts(), before, 'no save without a sender');
      // Legal mail: a bot that can't take mail shows but can't be picked; a refused change goes back to the saved bot.
      const legal = pane.locator('[data-pm-legal]');
      await legal.waitFor();
      assert.equal(await disabled(legal.locator('option[value="synced"]')), true, 'a chat-only bot cannot take legal mail');
      const legalSaved = () => page.waitForFunction(() => document.querySelector('[data-pm-legal]')?.disabled === false);
      await legal.selectOption('tico-counsel');
      await legalSaved();
      assert.equal(state.legal_bot_id, 'tico-counsel', 'legal mail goes to Counsel by its stable id');
      state.routingFail = true;
      await legal.selectOption('');
      await pane.locator('[data-pm-legal-error]', {hasText: 'Not saved'}).waitFor();
      assert.equal(await legal.inputValue(), 'tico-counsel', 'a refused change restores the saved bot');
      state.routingFail = false;
      // A read from before a save (the server still says Counsel) can't put Counsel back while the save is in flight.
      let release; state.holdRouting = new Promise(r => { release = r; });
      const reads = state.got['/api/v2/postal/settings'];
      await legal.selectOption('');
      await page.evaluate(() => { window.pmRedraw = renderSettingsInboxes(); });
      await until(() => state.got['/api/v2/postal/settings'] > reads, 'the redraw reads Legal mail');
      await page.evaluate(() => window.pmRedraw);
      assert.equal(await legal.inputValue(), '', 'the choice being saved stays');
      release(); state.holdRouting = null;
      await legalSaved();
      assert.equal(state.legal_bot_id, null, 'back to the owner');
      assert.equal(await legal.inputValue(), '');
      // Read on all addresses: a pick waits through a redraw; give it, see it, take it back.
      const all = pane.locator('#settings-mail-access');
      await all.locator('[data-pm-grants-empty]').waitFor();
      assert.equal(await disabled(all.locator('[data-pm-grantee] option[value="bot:synced"]')), true, 'a chat-only bot cannot read mail');
      await all.locator('[data-pm-grantee]').selectOption('person:ana');
      await page.evaluate(() => renderSettingsInboxes());
      assert.equal(await all.locator('[data-pm-grantee]').inputValue(), 'person:ana', 'a redraw keeps the pick');
      await all.locator('[data-pm-grant-form] button').click();
      await all.locator('[data-pm-grant="person:ana"]', {hasText: 'Ana · Read'}).waitFor();
      const given = state.sent.find(s => s.path === '/api/v2/postal/addresses/all/grants/person/ana');
      assert.equal(given.method, 'PUT');
      assert.deepEqual(given.body, {level: 'read'});
      assert.ok(given.key, 'a grant carries an Idempotency-Key');
      await all.locator('[data-pm-revoke]').click();
      await all.locator('[data-pm-grants-empty]').waitFor();
      assert.deepEqual(state.grants, [], 'Remove takes it back');
      // Get an address: prices and lines, then one order.
      await pane.locator('[data-pm-order]').click();
      const dialog = page.locator('.pm-dialog');
      await dialog.waitFor();
      const kind = k => dialog.locator(`[data-pm-kind="${k}"]`);
      assert.equal(await rowText(kind('shared')), 'Shared $12/mo Mailing address and registered agent', "Tico Gateway's price wins");
      assert.equal(await kind('private').locator('[data-pm-price]').innerText(), '$25/mo', 'a price sent as text reads the same');
      assert.equal(await dialog.locator('[data-pm-kind]').count(), 2, 'shared and private only');
      assert.equal(await dialog.locator('[data-pm-scan]').innerText(), 'Scans $1.50 each, $0.30 a page after 10', "Tico Gateway's scan price wins");
      assert.equal(await dialog.locator('[name=company_name]').inputValue(), 'Acme Inc');
      await dialog.locator('[name=state]').selectOption('CA');
      await kind('private').locator('input').check();
      if (shots) await dialog.screenshot({path: path.join(shots, 'letters-order-dark.png')});
      await dialog.locator('[data-pm-submit]').click();
      await page.locator('#inboxes-letters-in [data-pm-pending="mo_3"]').waitFor();
      const orders = state.sent.filter(s => s.path === '/api/v2/postal/orders');
      assert.equal(orders.length, 1, 'one order');
      assert.deepEqual(orders[0].body, {kind: 'private', state: 'CA', company_name: 'Acme Inc'});
      assert.ok(orders[0].key, 'the order carries an Idempotency-Key');
      assert.equal(await rowText(page.locator('#inboxes-letters-in [data-pm-pending="mo_3"]')), 'California Private — Pending');
      await page.locator('.pm-dialog').waitFor({state: 'detached'});   // the dialog closes
      // An address's page: its letters, newest first; the failed one has Retry, a filed one links its task and PDF.
      await page.locator('#inboxes-letters-in [data-pm-address="as_1"] a').click();
      const letters = page.locator('#inbox-letters');
      await letters.locator('[data-pm-letter="pm_2"]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/postal/as_1');
      assert.equal(await page.locator('#inbox-head h2').textContent(), '8 The Green, Suite 4000, Dover DE 19901');
      assert.deepEqual(await letters.locator('[data-pm-letter]').evaluateAll(trs => trs.map(tr => tr.dataset.pmLetter)), ['pm_3', 'pm_2', 'pm_1']);
      assert.equal(await letters.locator('[data-pm-letter="pm_2"] [data-pm-task]').getAttribute('href'), '#/task/t-42');
      assert.equal(await letters.locator('[data-pm-letter="pm_2"] [data-pm-pdf]').getAttribute('href'), '/api/v2/postal/items/pm_2/file');
      assert.match(await rowText(letters.locator('[data-pm-letter="pm_2"]')), /Superior Court of California Summons, answer within 21 days Legal PDF Task Filed$/);
      assert.match(await rowText(letters.locator('[data-pm-letter="pm_1"]')), /Big Bank Credit card offer Junk — Archived Archived$/);
      assert.equal(await letters.locator('[data-pm-retry]').count(), 1, 'only the failed letter offers Retry');
      assert.equal(await page.locator('[data-ib-detail-body] button', {hasText: /scan|forward/i}).count(), 0, 'no scan or forward request');
      assert.equal(await page.locator('[data-ib-detail-body] [data-pm-progress]').count(), 0, 'a receiving address has no setup steps');
      // This address's own Read, in Who can use it.
      const access = page.locator('#inbox-access');
      await access.locator('[data-pm-grants-empty]').waitFor();
      await access.locator('[data-pm-grantee]').selectOption('bot:tico-counsel');
      await access.locator('[data-pm-grant-form] button').click();
      await access.locator('[data-pm-grant="bot:tico-counsel"]', {hasText: 'Counsel · Read'}).waitFor();
      assert.ok(state.sent.some(s => s.method === 'PUT' && s.path === '/api/v2/postal/addresses/as_1/grants/bot/tico-counsel'));
      if (shots) await shot(page, 'address-desktop');
      await access.locator('[data-pm-revoke]').click();
      await access.locator('[data-pm-grants-empty]').waitFor();
      assert.ok(state.sent.some(s => s.method === 'DELETE' && s.path === '/api/v2/postal/addresses/as_1/grants/bot/tico-counsel'));
      // Retry the failed letter: it is asked for once, and the page redraws with what the server says now.
      await letters.locator('[data-pm-retry="pm_3"]').click();
      await page.locator('#inbox-letters [data-pm-letter="pm_3"] [data-pm-task]').waitFor();
      assert.deepEqual(state.sent.filter(s => s.path.endsWith('/retry')).map(s => s.path), ['/api/v2/postal/items/pm_3/retry']);
      assert.equal(await page.locator('#inbox-letters [data-pm-retry]').count(), 0);
      // An address waiting for Form 1583 shows where it is, and not its address yet.
      await go(page, '#/settings/inboxes/postal/as_2');
      await page.locator('[data-pm-progress="pending_1583"]').waitFor();
      assert.equal(await page.locator('#inbox-head h2').textContent(), 'Texas · Private');
      assert.deepEqual(await page.locator('[data-pm-progress] li').evaluateAll(lis => lis.map(li => [li.textContent, li.className])),
        [['Ordered', 'done'], ['Form 1583', 'cur'], ['Receiving', '']]);
      assert.ok(!(await page.locator('#set-inboxes').innerText()).includes('Keller Pkwy'));
      await page.locator('#inbox-letters .empty', {hasText: 'No letters yet.'}).waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Mail by email turned off with letters in: listed as Not in use, its page keeps them, and a failed one is retried.
      const base = server();
      const state = Object.assign(base, {intake: null, items: [{id: 'pm_e1', provider: 'email', received_at: AGO(10), kind: null, summary: '',
        sender: '', status: 'failed', task_id: null, attempts: 5, error: 'unreadable'}, ...base.items]});
      const {page, errors} = await open(browser, {state, hash: '#/settings/inboxes'});
      const row = page.locator('[data-ib-row="postal:intake"]');
      await row.waitFor();
      assert.equal(await row.locator('[data-ib-status]').getAttribute('data-ib-status'), 'off');
      assert.equal(await row.locator('[data-ib-status]').innerText(), 'Not in use');
      await row.locator('.ib-name a').click();
      const letters = page.locator('#inbox-letters');
      await letters.locator('[data-pm-letter="pm_e1"]').waitFor();
      assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/postal/intake');
      assert.deepEqual(await letters.locator('[data-pm-letter]').evaluateAll(trs => trs.map(tr => tr.dataset.pmLetter)), ['pm_e1'], 'only emailed letters');
      assert.equal(await page.locator('#inbox-head [data-ib-status]').getAttribute('data-ib-status'), 'off');
      assert.equal(await page.locator('[data-pm-intake-stop]').count(), 0, 'nothing to stop');
      assert.equal(await page.locator('[data-pm-intake-on]').getAttribute('href'), '#/settings/inboxes/settings/letters-in');
      await letters.locator('[data-pm-retry="pm_e1"]').click();
      await page.locator('#inbox-letters [data-pm-letter="pm_e1"] [data-pm-task]').waitFor();
      assert.deepEqual(state.sent.filter(s => s.path.endsWith('/retry')).map(s => s.path), ['/api/v2/postal/items/pm_e1/retry']);
      // With no emailed letters, mail by email that is off has no row.
      state.items = state.items.filter(m => m.provider !== 'email');
      await go(page, '#/settings/inboxes');
      await page.locator('[data-ib-row="postal:as_1"]').waitFor();
      assert.equal(await page.locator('[data-ib-row="postal:intake"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // A member reads: no order, no inbox picker, no retry.
      const state = Object.assign(server(), {role: 'member', intake: {inbox_id: 'ib_mail', address: 'mail@acme.example'}});
      const {page, errors} = await open(browser, {state});
      const pane = page.locator('#inboxes-letters-in');
      await pane.locator('[data-pm-intake-shown]').waitFor();
      await pane.locator('[data-pm-address="as_1"]').waitFor();
      await pane.locator('#settings-mail-legal [data-pm-legal-shown]', {hasText: 'Goes to the owner'}).waitFor();
      assert.equal(await pane.locator('#settings-mail-access').count(), 0, 'a member gives no Read');
      assert.equal(await pane.locator('[data-pm-order]').count(), 0);
      assert.equal(await pane.locator('[data-pm-intake-shown]').innerText(), 'mail@acme.example');
      await go(page, '#/settings/inboxes/postal/as_1');
      await page.locator('#inbox-letters [data-pm-letter="pm_3"]').waitFor();
      assert.equal(await page.locator('[data-pm-retry]').count(), 0);
      assert.equal(await page.locator('#inbox-access [data-pm-grant-form]').count(), 0, 'no grants on the page for a member');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // No Tico Gateway: the card says so, with the way to connect, nothing can be ordered, and the list shows no error.
      const {page, errors} = await open(browser, {state: Object.assign(server(), {gateway: false})});
      const pane = page.locator('#inboxes-letters-in');
      await pane.locator('[data-pm-gateway]').waitFor();
      assert.equal(await pane.locator('[data-pm-gateway]').innerText(), 'Connect Tico Gateway first. Key and connection');
      assert.equal(await pane.locator('[data-pm-order]').isDisabled(), true);
      await go(page, '#/settings/inboxes');
      await page.locator('[data-ib-row="tico:ib_mail"]').waitFor();
      assert.equal(await page.locator('[data-ib-error]').count(), 0);
      assert.equal(await page.locator('[data-ib-kind="postal"]').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Phone: nothing sideways, the dialog fits. Tico Gateway sends no prices, so Tico's own show.
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, state: Object.assign(server(), {prices: {}})});
      const pane = page.locator('#inboxes-letters-in');
      await pane.locator('[data-pm-address="as_1"]').waitFor();
      await pane.locator('[data-pm-intake-select]').waitFor();
      await pane.locator('[data-pm-legal]').waitFor();
      await pane.locator('#settings-mail-access [data-pm-grants-empty]').waitFor();
      assert.ok(await noSideways(page), 'Letters in fits a phone');
      await shot(page, 'settings-phone');
      await pane.locator('[data-pm-order]').click();
      const dialog = page.locator('.pm-dialog');
      await dialog.waitFor();
      assert.ok(await dialog.evaluate(d => d.scrollWidth <= d.clientWidth + 1), 'the order form fits');
      assert.deepEqual(await dialog.locator('[data-pm-price]').allInnerTexts(), ['$10/mo', 'from $25/mo']);
      assert.equal(await dialog.locator('[data-pm-scan]').innerText(), 'Scans $1 each, $0.25 a page after 10');
      if (shots) await dialog.screenshot({path: path.join(shots, 'letters-order-phone.png')});
      await dialog.locator('[data-close]').click();
      await go(page, '#/settings/inboxes/postal/as_1');
      await page.locator('#inbox-letters [data-pm-letter="pm_2"]').waitFor();
      await page.locator('#inbox-access [data-pm-grants-empty]').waitFor();
      assert.ok(await noSideways(page), 'an address page fits a phone');
      await shot(page, 'address-phone');
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('inboxes-letters ok');
})().catch(error => { console.error(error); process.exit(1); });
