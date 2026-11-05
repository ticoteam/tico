// Offline browser regression: Settings > Inboxes (ui/app/inboxes.js and its kind files) over fakes of every kind's
// routes (ui/tests/support/mailboxes-fake.cjs). One table lists Google mailboxes, Tico addresses (apart from Google
// email), numbers and phone requests, mailing addresses, open orders and mail by email, with one status vocabulary;
// search and the Kind / Status / Used by filters narrow it, no match offers Clear filters, and a long list pages. An
// admin without company email sees first run until they skip it (optional steps dismiss, and stay dismissed); empty
// offers it again. A member sees only their own mailbox and the ones shared with them. The old Mailboxes, Email &
// Phone, Mail and Google addresses open the tab. Inboxes > Settings has a section a channel with every setting, and
// Coming soon where another change fills it. Add inbox > Email connects a mailbox. Phone width has no sideways scroll,
// light and dark. TICO_SHOTS=<dir> saves the list, Settings and first run at desktop and phone width.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {googleFake, row: workspace} = require('./support/google-fake.cjs');
const {mailboxesFake, box, open} = require('./support/mailboxes-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});
const SHOTS = process.env.TICO_SHOTS || '';
const NOW = Date.now() / 1000;
const AGO = minutes => new Date(Date.now() - minutes * 60000).toISOString();

const LIST = '[data-ib-row]';
const rows = page => page.$$eval('[data-ib-row]', trs => trs.map(tr => [tr.dataset.ibRow,
  tr.querySelector('.ib-kind').textContent.trim(), tr.querySelector('[data-ib-status]').textContent.trim()]));
const ids = async page => (await rows(page)).map(r => r[0]);
const writesTo = (fake, path, method = 'POST') => fake.writes.filter(w => w.path === path && w.method === method);
const fits = page => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const settle = page => page.waitForFunction(() => !document.querySelector('#set-inboxes .skel'));

// Every other kind: a Tico address, a number, a quoted phone request, two addresses, an open order and mail by email.
function others({gateway = true} = {}) {
  const writes = [];
  const inbound = {configured: true, error: '', last_synced_at: NOW - 60, gaps: [], next: null, inboxes: [
    {inbox_id: 'ibx_a', kind: 'email', name: 'Account logins', address: 'logins@tico.example', state: 'active', audience: 'company',
      group_id: null, used_for: 'Vendor portal sign-ins', period_end: null, cancel_pending: 0},
    {inbox_id: 'ibx_s', kind: 'sms', name: 'Front desk', address: '+15550100', state: 'active', audience: 'company', group_id: null,
      used_for: '', period_end: NOW + 86400 * 20, cancel_pending: 0}]};
  const requests = [{request_id: 'prq_q', name: 'Second line', state: 'quoted', price_micros: 14000000, quoted_until: NOW + 3600}];
  const postal = {addresses: [
    {assignment_id: 'as_1', state: 'DE', kind: 'shared', address: '8 The Green, Suite 4000, Dover DE 19901', status: 'active', agent: true},
    {assignment_id: 'as_2', state: 'TX', kind: 'private', address: '', status: 'pending_1583', agent: false}],
  orders: [{order_id: 'mo_1', kind: 'shared', state: 'NV', status: 'open'}], prices: {}};
  const intake = {inbox_id: 'ibx_a', address: 'logins@tico.example', senders: ['scans@vpm.example']};
  const extra = (p, method, body) => {
    if (method !== 'GET') writes.push({method, path: p, body});
    if (p === '/api/v2/inbound/inboxes' && method === 'GET') return structuredClone(inbound);
    if (p === '/api/v2/inbound/phone-requests' && method === 'GET') return {phone_requests: structuredClone(requests), next: null};
    if (p === '/api/v2/inbound/messages') return {messages: [{seq: 1, message_id: 'm1', inbox_id: 'ibx_a', kind: 'email', received_at: NOW - 300,
      from: 'Vendor', subject: 'Sign in', preview: 'Your code', has_code: true}], next: null};
    if (p === '/api/v2/postal/addresses') return gateway ? structuredClone(postal)
      : {status: 409, body: {error: {code: 'gateway', detail: 'Connect Tico Gateway first'}}};
    if (p === '/api/v2/postal/items') return {items: [{id: 'pm_1', received_at: AGO(120), kind: 'bill', sender: 'Power Co', summary: 'Bill', status: 'filed',
      address: '8 The Green, Suite 4000, Dover DE 19901'}], next: null};
    if (p === '/api/v2/postal/intake') return {intake};
    return undefined;
  };
  return {extra, writes, inbound, requests, postal};
}

// A Google side with company email not set up yet: no Workspace key and no OAuth client (google-fake's start).
const bare = () => googleFake();

const scenarios = {
  'an admin sees every inbox in one table, one status vocabulary, each row opening its page': async browser => {
    const fake = mailboxesFake(), more = others();
    Object.assign(fake.state.mailboxes[0], {newest_epoch: Math.floor(NOW) - 7200, used_by: {people: 1, bots: 1, names: ['Ana', 'Ops']}});
    const {page, seen} = await open(browser, fake, googleFake(), {ready: LIST, extra: more.extra});
    await page.locator('[data-ib-row="postal:intake"]').waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes');
    assert.equal(await page.locator('[data-settings-tab="inboxes"]').getAttribute('aria-selected'), 'true');
    assert.deepEqual(await rows(page), [
      ['mailbox:mb-ana', 'Email', 'Connected'],
      ['mailbox:mb-ben', 'Email', 'Connected'],
      ['mailbox:mb-support', 'Email', 'Connected'],
      ['mailbox:mb-dan', 'Email', 'Awaiting person'],
      ['mailbox:mb-old', 'Email', 'Not in use'],
      ['tico:ibx_a', 'Tico address', 'Connected'],
      ['number:ibx_s', 'Texts in', 'Connected'],
      ['number:prq_q', 'Texts in', 'Quote $14.00 / 30 days'],
      ['postal:as_1', 'Letters in', 'Connected'],
      ['postal:as_2', 'Letters in', 'Waiting for Form 1583'],
      ['postal:order-mo_1', 'Letters in', 'Ordered'],
      ['postal:intake', 'Letters in', 'Connected'],
    ]);
    assert.equal(await page.locator('[data-ib-count]').textContent(), '12');
    const ana = page.locator('[data-ib-row="mailbox:mb-ana"]');
    assert.equal((await ana.locator('.ib-name .muted').textContent()).trim(), 'Ana · Ana, Ops');
    assert.equal(await ana.locator('.ib-latest').textContent(), '2h ago');
    assert.equal(await page.locator('[data-ib-row="postal:as_1"] .ib-latest').textContent(), '2h ago', 'a letter dates its address');
    assert.equal(await ana.locator('.ib-name a').getAttribute('href'), '#/settings/inboxes/mailbox/mb-ana');
    assert.equal(await page.locator('[data-ib-row="tico:ibx_a"] .ib-name a').getAttribute('href'), '#/settings/inboxes/tico/ibx_a');
    assert.deepEqual(await page.locator('[data-ib-filter=kind] option').allTextContents(), ['All kinds', 'Email', 'Texts in', 'Letters in', 'Letters out']);
    assert.equal(await page.locator('[data-ib-settings]').getAttribute('href'), '#/settings/inboxes/settings');
    await page.locator('[data-ib-add] > summary').click();
    assert.deepEqual(await page.locator('[data-ib-add-kind]').allTextContents(), ['Email (Google)', 'Tico address', 'Phone number', 'Mailing address']);
    await page.locator('[data-ib-add] > summary').click();
    // Each row opens its page, with the three sections and no side panel.
    await page.locator('[data-ib-row="tico:ibx_a"] .ib-name a').click();
    await page.locator('#inbox-head h2', {hasText: 'Account logins'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/tico/ibx_a');
    assert.deepEqual(await page.locator('[data-ib-detail-body] > .card > header h2').allTextContents(), ['Who can use it', 'Automations', 'Recent']);
    assert.deepEqual(await page.locator('#inbox-head .ib-actions button').allTextContents(), ['Check connection', 'Edit', 'Stop using in Tico']);
    if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-detail-desktop-light.png`, fullPage: true});
    await page.locator('.ib-crumb a').click();
    await page.locator('[data-ib-row="postal:as_2"] .ib-name a').click();
    await page.locator('[data-pm-progress="pending_1583"]').waitFor();
    assert.deepEqual(await page.locator('[data-pm-progress] li').evaluateAll(lis => lis.map(li => `${li.textContent}:${li.className}`)),
      ['Ordered:done', 'Form 1583:cur', 'Receiving:']);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'search and the filters narrow the table; no match offers Clear filters': async browser => {
    const fake = mailboxesFake(), more = others();
    Object.assign(fake.state.mailboxes.find(m => m.id === 'mb-support'), {used_by: {people: 2, bots: 1, names: ['Cara', 'Support bot', 'Ben']}});
    Object.assign(fake.state.mailboxes.find(m => m.id === 'mb-ben'), {used_by: {people: 1, bots: 0, names: ['Ben']}, sync: 'failed'});
    const {page, seen} = await open(browser, fake, googleFake(), {ready: LIST, extra: more.extra});
    await page.locator('[data-ib-row="postal:intake"]').waitFor();
    const q = page.locator('[data-ib-q]');
    await q.fill('front');
    assert.deepEqual(await ids(page), ['number:ibx_s']);
    await q.fill('support bot');
    assert.deepEqual(await ids(page), ['mailbox:mb-support'], 'a bot that uses it finds it');
    await q.fill('vendor portal');
    assert.deepEqual(await ids(page), ['tico:ibx_a'], 'what an inbox is used for finds it');
    await q.fill('');
    await page.locator('[data-ib-filter=kind]').selectOption('letters-in');
    assert.deepEqual(await ids(page), ['postal:as_1', 'postal:as_2', 'postal:order-mo_1', 'postal:intake']);
    await page.locator('[data-ib-filter=kind]').selectOption('');
    await page.locator('[data-ib-filter=status]').selectOption('attention');
    assert.deepEqual(await ids(page), ['mailbox:mb-ben']);
    await page.locator('[data-ib-filter=status]').selectOption('pending');
    assert.deepEqual(await ids(page), ['mailbox:mb-dan', 'number:prq_q', 'postal:as_2', 'postal:order-mo_1']);
    await page.locator('[data-ib-filter=status]').selectOption('');
    await page.locator('[data-ib-filter=used]').selectOption('bots');
    assert.deepEqual(await ids(page), ['mailbox:mb-support']);
    await page.locator('[data-ib-filter=kind]').selectOption('letters-out');
    await page.locator('[data-ib-none]').waitFor();
    assert.equal(await page.locator(LIST).count(), 0);
    await page.locator('[data-ib-clear]').click();
    assert.equal(await page.locator(LIST).count(), 12);
    assert.deepEqual(await page.$$eval('[data-ib-filter]', els => els.map(el => el.value)), ['', '', '']);
    assert.equal(await q.inputValue(), '');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a long list shows 25 rows and Show more pages the rest': async browser => {
    const fake = mailboxesFake();
    fake.state.mailboxes = Array.from({length: 30}, (_, n) => box(`mb-${n}`, `person${String(n).padStart(2, '0')}@acme.example`));
    const {page, seen} = await open(browser, fake, googleFake(), {ready: LIST});
    assert.equal(await page.locator(LIST).count(), 25);
    assert.match(await page.locator('.ib-more').textContent(), /^Showing 25 of 30/);
    await page.locator('[data-ib-more]').click();
    assert.equal(await page.locator(LIST).count(), 30);
    assert.equal(await page.locator('.ib-more').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a kind that fails to load says so, and the others still list': async browser => {
    const fake = mailboxesFake(), more = others();
    const extra = (p, method, body) => p === '/api/v2/inbound/inboxes'
      ? {status: 503, body: {error: {code: 'inbound_unavailable', detail: 'Tico addresses and numbers did not answer. Please retry.'}}}
      : more.extra(p, method, body);
    const {page, seen} = await open(browser, fake, googleFake(), {ready: LIST, extra});
    await page.locator('[data-ib-error]').first().waitFor();
    assert.match(await page.locator('[data-ib-errors]').textContent(), /did not answer/);
    assert.ok((await ids(page)).includes('mailbox:mb-ana'));
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'first run: an admin with no company email sees setup until they skip it; empty offers it again': async browser => {
    const fake = mailboxesFake();
    fake.state.mailboxes = [];
    const google = bare();
    let {page, seen} = await open(browser, fake, google, {ready: '[data-ib-first]'});
    assert.deepEqual(await page.locator('[data-ib-step] h3').allTextContents(),
      ['Connect company email', 'Phone number', 'Receive letters', 'Send letters', 'Give inboxes to bots']);
    assert.equal(await page.locator('[data-ib-step=email] [data-ib-dismiss]').count(), 0, 'the required step has no dismiss');
    await page.locator('[data-ib-step=email] #set-google [data-google-mode]').waitFor();
    assert.equal(await page.locator('[data-ib-step="letters-out"] button[disabled]').textContent(), 'Connect Click2Mail');
    assert.ok(await fits(page));
    if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-first-run-desktop-light.png`, fullPage: true});
    await page.locator('[data-ib-dismiss=phone]').click();
    assert.equal(await page.locator('[data-ib-step=phone]').count(), 0);
    await page.reload();
    await page.locator('[data-ib-first]').waitFor();
    assert.equal(await page.locator('[data-ib-step=phone]').count(), 0, 'a dismissed step stays dismissed');
    await page.locator('[data-ib-skip]').last().click();
    await page.locator('[data-ib-empty]').waitFor();
    assert.match(await page.locator('[data-ib-empty]').textContent(), /No inboxes yet/);
    await page.reload();
    await page.locator('[data-ib-empty]').waitFor();
    assert.equal(await page.locator('[data-ib-first]').count(), 0, 'skipping is kept');
    await page.locator('[data-ib-setup]').click();
    await page.locator('[data-ib-first]').waitFor();
    assert.equal(await page.locator('[data-ib-step=phone]').count(), 1, 'setting up again brings back dismissed steps');
    // The demo starts set up without Google, so it opens on the list.
    await page.evaluate(() => { S.config.demo = true; return renderSettingsInboxes(); });
    await page.locator('[data-ib-empty]').waitFor();
    assert.equal(await page.locator('[data-ib-first]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
    // Phone width: first run fits.
    ({page, seen} = await open(browser, fake, bare(), {ready: '[data-ib-first]', width: 390, height: 844, theme: 'dark'}));
    await page.locator('[data-ib-step=email] #set-google [data-google-mode]').waitFor();
    assert.ok(await fits(page), 'first run fits a phone');
    if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-first-run-phone-dark.png`, fullPage: true});
    assert.deepEqual(seen.errors, []);
    await page.close();
    // Once company email is connected the list shows, and a member never sees first run.
    const connected = bare();
    connected.state.workspace = workspace('connected');
    ({page, seen} = await open(browser, fake, connected, {ready: '[data-ib-empty]'}));
    assert.equal(await page.locator('[data-ib-first]').count(), 0);
    assert.equal(await page.locator('[data-ib-setup]').count(), 0, 'nothing was skipped, so nothing to bring back');
    await page.close();
    const member = mailboxesFake({admin: false, me: 'cara'});
    member.state.mailboxes = [];
    const google2 = bare();
    google2.state.can_admin = false;
    ({page, seen} = await open(browser, member, google2, {ready: '[data-ib-empty]'}));
    assert.equal(await page.locator('[data-ib-first]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a member sees only their own mailbox and the ones shared with them': async browser => {
    const fake = mailboxesFake({admin: false, me: 'cara'});
    fake.state.mailboxes = [
      box('mb-cara', 'cara@acme.example', {person_id: 'cara', person_name: 'Cara', level: 'read_write'}),
      box('mb-support', 'support@acme.example', {kind: 'shared', level: 'read'}),
      box('mb-ben', 'ben@acme.example', {person_id: 'ben', person_name: 'Ben', level: 'read'}),
      box('mb-dan', 'dan@acme.example', {person_id: 'dan', person_name: 'Dan'}),
    ];
    const google = googleFake();
    google.state.can_admin = false;
    google.state.me = {mailbox: 'cara@acme.example', covered_by_workspace: true};
    const {page, seen} = await open(browser, fake, google, {ready: LIST});
    assert.deepEqual(await ids(page), ['mailbox:mb-cara', 'mailbox:mb-support', 'mailbox:mb-ben']);
    await page.locator('[data-ib-add] > summary').click();
    assert.deepEqual(await page.locator('[data-ib-add-kind]').allTextContents(), ['Tico address'], 'only Tico addresses are anyone\'s to add');
    await page.locator('[data-ib-settings]').click();
    await page.locator('[data-mb-mine-box]').waitFor();
    assert.equal(await page.locator('#mailboxes-top h2').textContent(), 'Your mailbox');
    assert.match((await page.locator('[data-mb-mine-box]').textContent()).replace(/\s+/g, ' '),
      /cara@acme\.example · connected by your company.*Tico may read and organize\. Only you and your Assistant can use it\./);
    assert.equal(await page.locator('#mailboxes-may').count(), 0);
    assert.equal(await page.locator('#inbound-phone-card').count(), 0);
    assert.equal(await page.locator('#inbound-connection').count(), 0);
    assert.equal(await page.locator('[data-pm-order]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
    // A mailbox the person signed in themselves (not the company's key) has no "connected by your company" line.
    fake.state.mailboxes = [box('mb-cara', 'cara@acme.example', {person_id: 'cara', person_name: 'Cara', level: 'read_write', connection: 'personal'})];
    google.state.me = {mailbox: 'cara@acme.example', covered_by_workspace: false};
    const again = await open(browser, fake, google, {hash: '#/settings/inboxes/settings/email', ready: '#mailboxes-top #set-google'});
    await again.page.locator('#set-google .google-card').first().waitFor();
    assert.equal(await again.page.locator('[data-mb-mine-box]').count(), 0);
    assert.deepEqual(again.seen.errors, []);
    await again.page.close();
  },

  'the old Mailboxes, Email & Phone, Mail and Google addresses open Inboxes': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), {hash: '#/settings/mailboxes', ready: LIST});
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes');
    for (const [from, to] of [
      ['#/settings/inbound', '#/settings/inboxes'],
      ['#/settings/company-inbox', '#/settings/inboxes'],
      ['#/settings/email-phone', '#/settings/inboxes'],
      ['#/settings/mailboxes/mb-support', '#/settings/inboxes/mailbox/mb-support'],
      ['#/settings/mail', '#/settings/inboxes/settings/letters-in'],
      ['#/settings/inbox', '#/settings/inboxes/settings/email'],
      ['#/settings/google', '#/settings/inboxes/settings/email'],
      ['#/settings/inboxes/texts', '#/settings/inboxes/settings/texts'],
    ]) {
      await page.evaluate(hash => { location.hash = hash; }, from);
      await page.waitForFunction(want => location.hash === want && SETTINGS_TAB === 'inboxes', to);
    }
    await page.locator('#settings-inboxes [data-ib-section=texts]').waitFor();
    // A reload keeps the page it was on, and another tab leaves it.
    await page.evaluate(() => { location.hash = '#/settings/inboxes/mailbox/mb-support'; });
    await page.locator('#inbox-head h2', {hasText: 'support@acme.example'}).waitFor();
    await page.reload();
    await page.locator('#inbox-head h2', {hasText: 'support@acme.example'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/mailbox/mb-support');
    await page.locator('[data-settings-tab="general"]').click();
    await page.waitForFunction(() => SETTINGS_TAB === 'general');
    await page.locator('[data-settings-tab="inboxes"]').click();
    await page.waitForFunction(() => location.hash === '#/settings/inboxes');
    await page.locator(LIST).first().waitFor();
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Inboxes > Settings: a section a channel, every setting in its section, Coming soon for what is still to come': async browser => {
    const fake = mailboxesFake(), more = others();
    const {page, seen} = await open(browser, fake, googleFake(), {hash: '#/settings/inboxes/settings', ready: '[data-ib-settings-page]', extra: more.extra});
    await page.locator('#settings-mail-addresses [data-pm-address="as_1"]').waitFor();
    await page.locator('#set-google [data-google-mode]').waitFor();
    assert.deepEqual(await page.locator('.ib-section > h2').allTextContents(), ['Email', 'Texts', 'Letters in', 'Letters out']);
    const cards = section => page.locator(`[data-ib-section="${section}"] .card`).evaluateAll(els => els.map(el => el.id));
    assert.deepEqual(await cards('email'), ['mailboxes-top', 'mailboxes-may', 'set-inbound', 'inboxes-email-sending']);
    assert.deepEqual(await cards('texts'), ['inbound-phone-card', 'inboxes-alerts']);
    assert.deepEqual(await cards('letters-in'), ['settings-mail-addresses', 'settings-mail-intake']);
    assert.deepEqual(await cards('letters-out'), ['inboxes-letters-out']);
    for (const id of ['inboxes-email-sending', 'inboxes-alerts', 'inboxes-letters-out'])
      assert.equal(await page.locator(`#${id} header .pill`).textContent(), 'Coming soon', id);
    assert.equal(await page.locator('[data-mb-provider]').textContent(), 'Google Workspace · acme.example');
    assert.equal(await page.locator('[data-pm-intake-select]').inputValue(), 'ibx_a');
    assert.equal(await page.locator('[data-in-request="prq_q"] [data-inbound-phone-accept]').count(), 1);
    assert.equal(await page.locator('.ib-crumb a').getAttribute('href'), '#/settings/inboxes');
    // A section's address opens Settings at it.
    await page.evaluate(() => { location.hash = '#/settings/inboxes/settings/letters-in'; });
    await page.waitForFunction(() => {
      const top = document.querySelector('#inboxes-letters-in')?.getBoundingClientRect().top;
      return top !== undefined && top >= -2 && top < 120;
    });
    assert.ok(await fits(page));
    if (SHOTS) {
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({path: `${SHOTS}/inboxes-settings-desktop-light.png`, fullPage: true});
    }
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'What Tico may do: Read always, each box saves all three together, a refusal puts the box back': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {hash: '#/settings/inboxes/settings/email', ready: '#mailboxes-may'});
    const card = page.locator('#mailboxes-may');
    assert.equal(await card.locator('[data-google-may-read] input').count(), 0, 'Read has no box');
    const boxes = () => card.locator('[data-google-may]').evaluateAll(els => els.map(el => [el.dataset.googleMay, el.checked]));
    assert.deepEqual(await boxes(), [['organize_mail', true], ['send_mail', false], ['change_calendar', false]]);
    await card.locator('[data-google-may=change_calendar]').check();
    const end = Date.now() + 5000;
    while (!writesTo(google, '/api/v2/google/permissions', 'PUT').length && Date.now() < end) await page.waitForTimeout(50);
    assert.deepEqual(writesTo(google, '/api/v2/google/permissions', 'PUT').map(w => w.body),
      [{organize_mail: true, send_mail: false, change_calendar: true}]);
    await page.waitForFunction(() => [...document.querySelectorAll('[data-google-may]')].every(el => !el.disabled));
    await page.route('**/api/v2/google/permissions', route => route.fulfill({status: 403, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'forbidden', detail: 'Only an admin changes this', retryable: false}})}));
    await card.locator('[data-google-may=send_mail]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector('[data-google-may=send_mail]');
      return el && !el.disabled && !el.checked;
    });
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Add inbox > Email connects a mailbox and opens its page; an address already in Tico says so in the dialog': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), {ready: LIST});
    await page.locator('[data-ib-add] > summary').click();
    await page.locator('[data-ib-add-kind=mailbox]').click();
    const dialog = page.locator('dialog.mb-dialog');
    await dialog.locator('[name=address]').fill('ana@acme.example');
    await dialog.locator('[data-mb-connect]').click();
    await dialog.locator('[data-wh-error]', {hasText: 'ana@acme.example is already in Tico'}).waitFor();
    assert.equal(await dialog.locator('[name=address]').inputValue(), 'ana@acme.example', 'the address stays to fix');
    await dialog.locator('[name=address]').fill(' Help@Acme.example ');
    await dialog.locator('[name=shared]').check();
    await dialog.locator('[data-mb-connect]').click();
    await page.locator('#inbox-head h2', {hasText: 'help@acme.example'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/mailbox/mb-new1');
    assert.deepEqual(writesTo(fake, '/api/v2/mailboxes').map(w => w.body),
      [{address: 'ana@acme.example', shared: false}, {address: 'Help@Acme.example', shared: true}]);
    // Make shared inbox turns an address with no person into a shared inbox, from its page.
    await page.evaluate(() => { location.hash = '#/settings/inboxes/mailbox/mb-dan'; });
    await page.locator('[data-mb-share]').click();
    await page.locator('[data-mb-status]', {hasText: /^Shared inbox/}).waitFor();
    assert.equal(writesTo(fake, '/api/v2/mailboxes/mb-dan/share').length, 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'phone width: the list, Settings and a page fit with no sideways scroll, light and dark': async browser => {
    for (const theme of ['light', 'dark']) {
      const fake = mailboxesFake(), more = others();
      let {page, seen} = await open(browser, fake, googleFake(), {ready: LIST, extra: more.extra, width: 390, height: 844, theme});
      await page.locator('[data-ib-row="postal:intake"]').waitFor();
      assert.ok(await fits(page), `the list fits a phone (${theme})`);
      assert.equal(await page.locator('.ib-table thead').isHidden(), true, 'rows are cards on a phone');
      if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-list-phone-${theme}.png`, fullPage: true});
      await page.evaluate(() => { location.hash = '#/settings/inboxes/settings'; });
      await page.locator('#settings-mail-addresses [data-pm-address="as_1"]').waitFor();
      await settle(page);
      assert.ok(await fits(page), `Settings fits a phone (${theme})`);
      if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-settings-phone-${theme}.png`, fullPage: true});
      await page.evaluate(() => { location.hash = '#/settings/inboxes/tico/ibx_a'; });
      await page.locator('[data-inbound-read]').waitFor();
      assert.ok(await fits(page), `a page fits a phone (${theme})`);
      if (SHOTS) await page.screenshot({path: `${SHOTS}/inboxes-detail-phone-${theme}.png`, fullPage: true});
      assert.deepEqual(seen.errors, []);
      await page.close();
      if (!SHOTS) continue;
      ({page, seen} = await open(browser, fake, googleFake(), {ready: LIST, extra: more.extra, theme}));
      await page.locator('[data-ib-row="postal:intake"]').waitFor();
      await page.screenshot({path: `${SHOTS}/inboxes-list-desktop-${theme}.png`, fullPage: true});
      await page.close();
    }
  },
};

(async () => {
  const browser = await launch();
  try {
    for (const [name, run] of Object.entries(scenarios)) {
      await run(browser);
      console.log(`ok  ${name}`);
    }
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
