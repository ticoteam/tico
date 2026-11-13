// Offline browser regression: the inbox kinds under Tools (ui/app/inboxes.ts and its kind files) over fakes of every
// kind's routes (ui/tests/support/mailboxes-fake.cts). The old Settings > Inboxes, Mailboxes, Email & Phone, Mail and
// Google addresses open their Tools pages. Tools › Settings has a section a channel with every setting, and Coming soon
// where another change fills it; What Tico may do never loses a pending save to an older read. Add tool > Email (Google)
// connects a mailbox. Phone width has no sideways scroll, light and dark. TICO_SHOTS=<dir> saves Settings and a page.
// (The one list of every tool is Tools itself: ui/tests/tools.cts.)
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {googleFake} = require('./support/google-fake.cts') as import('./support/google-fake.cts').Api;
const {mailboxesFake, open, shot} = require('./support/mailboxes-fake.cts') as import('./support/mailboxes-fake.cts').Api;
type Page = import('playwright').Page;
type Route = import('playwright').Route;
declare function inboxesRender(): unknown;
const launch = () => chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
const SHOTS = process.env.TICO_SHOTS || '';
const NOW = Date.now() / 1000;
const AGO = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();

const writesTo = <W extends {method: string; path: string}>(fake: {writes: W[]}, path: string, method = 'POST') => fake.writes.filter(w => w.path === path && w.method === method);
const fits = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
const settle = (page: Page) => page.waitForFunction(() => !document.querySelector('#tools-inbox .skel'));

// Every other kind: a Tico address, a number, a quoted phone request, two addresses, an open order and mail by email.
function others({gateway = true} = {}) {
  const writes: {method: string; path: string; body: unknown}[] = [];
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
  const extra = (p: string, method: string, body: unknown) => {
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

const scenarios = {
  'the old Settings > Inboxes, Mailboxes, Email & Phone, Mail and Google addresses open their Tools pages': async (browser: import('playwright').Browser) => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), {hash: '#/settings/mailboxes', ready: '#tools-root', extra: others().extra});
    assert.equal(await page.evaluate(() => location.hash), '#/tools?show=mail');
    for (const [from, to] of [
      ['#/settings/inboxes', '#/tools?show=mail'],
      ['#/settings/inbound', '#/tools?show=mail'],
      ['#/settings/company-inbox', '#/tools?show=mail'],
      ['#/settings/email-phone', '#/tools?show=mail'],
      ['#/settings/mailboxes/mb-support', '#/tools/mailbox/mb-support'],
      ['#/settings/inboxes/mailbox/mb-support', '#/tools/mailbox/mb-support'],
      ['#/settings/inboxes/tico/ibx_a', '#/tools/number/ibx_a'],
      ['#/settings/inboxes/postal/as_1', '#/tools/address/as_1'],
      ['#/settings/inboxes/messages', '#/tools/messages'],
      ['#/settings/mail', '#/tools/settings/letters-in'],
      ['#/settings/inbox', '#/tools/settings/email'],
      ['#/settings/google', '#/tools/settings/email'],
      ['#/settings/inboxes/settings/email', '#/tools/settings/email'],
      ['#/settings/inboxes/texts', '#/tools/settings/texts'],
    ] as [string, string][]) {
      await page.evaluate((hash: string) => { location.hash = hash; }, from);
      await page.waitForFunction((want: string) => location.hash === want, to).catch(() => assert.fail(`${from} leads to ${to}`));
    }
    await page.locator('#tools-inbox [data-ib-section=texts]').waitFor();
    assert.equal(await page.locator('#side a[data-nav="tools"].cur').count(), 1, 'Tools is current');
    // A reload keeps the page it was on.
    await page.evaluate(() => { location.hash = '#/tools/mailbox/mb-support'; });
    await page.locator('#inbox-head h1', {hasText: 'support@acme.example'}).waitFor();
    await page.reload();
    await page.locator('#inbox-head h1', {hasText: 'support@acme.example'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tools/mailbox/mb-support');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Tools › Settings: a section a channel, every setting in its section, Coming soon for what is still to come': async (browser: import('playwright').Browser) => {
    const fake = mailboxesFake(), more = others();
    const {page, seen} = await open(browser, fake, googleFake(), {hash: '#/tools/settings', ready: '[data-ib-settings-page]', extra: more.extra});
    await page.locator('#settings-mail-addresses [data-pm-address="as_1"]').waitFor();
    await page.locator('#set-google [data-google-mode]').waitFor();
    await page.locator('#inboxes-email-sending [data-send-form]').waitFor();
    assert.deepEqual(await page.locator('.ib-section > h2').allTextContents(), ['Email', 'Texts', 'Letters in', 'Letters out']);
    // The existing settings, each in its section and order. Sending, alerts and letters out are other changes' cards:
    // each section has a card with a header, and one still on its placeholder says Coming soon.
    const cards = (section: string) => page.locator(`[data-ib-section="${section}"] .card`).evaluateAll(els => els.map(el => el.id));
    const own = {email: ['mailboxes-top', 'mailboxes-may', 'set-inbound'], texts: ['inbound-phone-card'],
      'letters-in': ['settings-mail-addresses', 'settings-mail-intake'], 'letters-out': [] as string[]};
    for (const [section, ids] of Object.entries(own)) {
      assert.deepEqual((await cards(section)).filter(id => ids.includes(id)), ids, section);
      assert.ok(await page.locator(`[data-ib-section="${section}"] .card > header h2`).count(), `${section} has a card`);
    }
    for (const soon of await page.locator('.card:has([data-ib-soon])').all())
      assert.equal(await soon.locator('header .pill').textContent(), 'Coming soon');
    assert.equal(await page.locator('[data-mb-provider]').textContent(), 'Google Workspace · acme.example');
    assert.equal(await page.locator('[data-pm-intake-select]').inputValue(), 'ibx_a');
    assert.equal(await page.locator('[data-in-request="prq_q"] [data-inbound-phone-accept]').count(), 1);
    assert.equal(await page.locator('#tools-inbox .tt-crumb a').getAttribute('href'), '#/tools');
    // A section's address opens Settings at it.
    await page.evaluate(() => { location.hash = '#/tools/settings/letters-in'; });
    // At the top, or as far down as the page goes with the section in view (it is near the end).
    await page.waitForFunction(() => {
      const el = document.querySelector('#inboxes-letters-in');
      if (!el) return false;
      let box = el.parentElement;
      while (box && !(box.scrollHeight > box.clientHeight + 1 && /auto|scroll/.test(getComputedStyle(box).overflowY))) box = box.parentElement;
      const s = (box || document.scrollingElement) as Element, top = el.getBoundingClientRect().top;
      return top >= -2 && (top < 120 || (s.scrollTop > 0 && s.scrollTop + s.clientHeight >= s.scrollHeight - 2 && top < innerHeight - 200));
    });
    assert.ok(await fits(page));
    if (SHOTS) {
      await page.evaluate(() => document.querySelector<HTMLElement>('#tools-inbox')!.scrollIntoView({block: 'start'}));
      await shot(page, `inboxes-settings-desktop-light.png`, 'light');
    }
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a read from before a What Tico may do save cannot replace its controls during or after it': async (browser: import('playwright').Browser) => {
    for (const finishRead of ['during', 'after']) {
      const fake = mailboxesFake(), google = googleFake();
      const {page, seen} = await open(browser, fake, google, {hash: '#/tools/settings', ready: '#mailboxes-may [data-google-may]'});
      const card = page.locator('#mailboxes-may');
      const calendar = (await card.locator('[data-google-may=change_calendar]').elementHandle())!;
      let readStarted!: (r: Route) => void, saveStarted!: (r: Route) => void;
      const reading = new Promise<Route>(resolve => { readStarted = resolve; });
      const saving = new Promise<Route>(resolve => { saveStarted = resolve; });
      await page.route('**/api/v2/mailboxes', (route: Route) => readStarted(route));
      await page.route('**/api/v2/google/permissions', (route: Route) => saveStarted(route));
      await page.evaluate(() => { (window as unknown as {inboxesRead: unknown}).inboxesRead = inboxesRender(); });
      const read = await reading;
      const stale = fake.answer('/api/v2/mailboxes');
      await card.locator('[data-google-may=change_calendar]').check();
      const save = await saving;
      const body = save.request().postDataJSON();
      assert.deepEqual(body, {organize_mail: true, send_mail: false, change_calendar: true});
      const controls = () => card.locator('[data-google-may]').evaluateAll(els =>
        els.map(el => [(el as HTMLInputElement).dataset.googleMay, (el as HTMLInputElement).checked, (el as HTMLInputElement).disabled]));
      const pending = [['organize_mail', true, true], ['send_mail', false, true], ['change_calendar', true, true]];
      assert.deepEqual(await controls(), pending);
      const finish = async () => {
        await read.fulfill({contentType: 'application/json', body: JSON.stringify(stale)});
        await page.evaluate(() => (window as unknown as {inboxesRead: unknown}).inboxesRead);
        assert.equal(await calendar.evaluate(el => el.isConnected), true, `${finishRead}: the clicked control stays mounted`);
      };
      if (finishRead === 'during') {
        await finish();
        assert.deepEqual(await controls(), pending, 'an old read cannot roll back or unlock a pending save');
      }
      await page.unroute('**/api/v2/mailboxes');
      if (finishRead === 'during') await page.evaluate(() => inboxesRender());
      assert.deepEqual(await controls(), pending, 'another draw leaves the pending save alone');
      assert.equal(await calendar.evaluate(el => el.isConnected), true);
      const answer = google.answer('/api/v2/google/permissions', save.request().url(), 'PUT', body);
      Object.assign(fake.may, {organize: true, send: false, change_calendar: true});
      // A draw asked for during the save runs once it settles.
      const reread = finishRead === 'during' && page.waitForRequest(r => new URL(r.url()).pathname === '/api/v2/mailboxes');
      await save.fulfill({contentType: 'application/json', body: JSON.stringify(answer)});
      await page.waitForFunction(() => [...document.querySelectorAll<HTMLInputElement>('#mailboxes-may [data-google-may]')].every(el => !el.disabled));
      if (reread) await reread;
      if (finishRead === 'after') await finish();
      const saved = pending.map(([field, checked]) => [field, checked, false]);
      assert.deepEqual(await controls(), saved);
      await page.evaluate(() => inboxesRender());
      assert.deepEqual(await controls(), saved, 'a fresh read shows the saved settings');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
  },

  'What Tico may do: Read always, each box saves all three together, a refusal puts the box back': async (browser: import('playwright').Browser) => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {hash: '#/tools/settings/email', ready: '#mailboxes-may'});
    const card = page.locator('#mailboxes-may');
    assert.equal(await card.locator('[data-google-may-read] input').count(), 0, 'Read has no box');
    const boxes = () => card.locator('[data-google-may]').evaluateAll(els => els.map(el => [(el as HTMLInputElement).dataset.googleMay, (el as HTMLInputElement).checked]));
    assert.deepEqual(await boxes(), [['organize_mail', true], ['send_mail', false], ['change_calendar', false]]);
    await card.locator('[data-google-may=change_calendar]').check();
    const end = Date.now() + 5000;
    while (!writesTo(google, '/api/v2/google/permissions', 'PUT').length && Date.now() < end) await page.waitForTimeout(50);
    assert.deepEqual(writesTo(google, '/api/v2/google/permissions', 'PUT').map(w => w.body),
      [{organize_mail: true, send_mail: false, change_calendar: true}]);
    await page.waitForFunction(() => [...document.querySelectorAll<HTMLInputElement>('[data-google-may]')].every(el => !el.disabled));
    await page.route('**/api/v2/google/permissions', (route: Route) => route.fulfill({status: 403, contentType: 'application/json',
      body: JSON.stringify({error: {code: 'forbidden', detail: 'Only an admin changes this', retryable: false}})}));
    await card.locator('[data-google-may=send_mail]').click();
    await page.waitForFunction(() => {
      const el = document.querySelector<HTMLInputElement>('[data-google-may=send_mail]');
      return el && !el.disabled && !el.checked;
    });
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Add tool > Email (Google) connects a mailbox and opens its page; an address already in Tico says so in the dialog': async (browser: import('playwright').Browser) => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), {ready: '#tools-root [data-tl-add]'});
    await page.locator('[data-tl-add] > summary').click();
    assert.deepEqual(await page.locator('[data-tl-add-item]').allTextContents(), ['API', 'Website', 'Email (Google)', 'Tico address', 'Phone number', 'Mailing address', 'Outgoing webhook']);
    await page.locator('[data-tl-add-item="kind:mailbox"]').click();
    const dialog = page.locator('dialog.mb-dialog');
    await dialog.locator('[name=address]').fill('ana@acme.example');
    await dialog.locator('[data-mb-connect]').click();
    await dialog.locator('[data-wh-error]', {hasText: 'ana@acme.example is already in Tico'}).waitFor();
    assert.equal(await dialog.locator('[name=address]').inputValue(), 'ana@acme.example', 'the address stays to fix');
    await dialog.locator('[name=address]').fill(' Help@Acme.example ');
    await dialog.locator('[name=shared]').check();
    await dialog.locator('[data-mb-connect]').click();
    await page.locator('#inbox-head h1', {hasText: 'help@acme.example'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/tools/mailbox/help%40acme.example');
    assert.deepEqual(writesTo(fake, '/api/v2/mailboxes').map(w => w.body),
      [{address: 'ana@acme.example', shared: false}, {address: 'Help@Acme.example', shared: true}]);
    // Make shared inbox turns an address with no person into a shared inbox, from its page.
    await page.evaluate(() => { location.hash = '#/tools/mailbox/mb-dan'; });
    await page.locator('[data-mb-share]').click();
    await page.locator('[data-mb-status]', {hasText: /^Shared inbox/}).waitFor();
    assert.equal(writesTo(fake, '/api/v2/mailboxes/mb-dan/share').length, 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'leaving Tools › Settings mid-save and coming back keeps What Tico may do locked on what the save sends, then what the server kept': async (browser: import('playwright').Browser) => {
    const fake = mailboxesFake(), google = googleFake();
    fake.may.send = true;
    const {page, seen} = await open(browser, fake, google, {hash: '#/tools/settings', ready: '#mailboxes-may [data-google-may]'});
    const card = page.locator('#mailboxes-may');
    const controls = () => card.locator('[data-google-may]').evaluateAll(els => els.map(el => [(el as HTMLInputElement).dataset.googleMay, (el as HTMLInputElement).checked, (el as HTMLInputElement).disabled]));
    assert.deepEqual(await controls(), [['organize_mail', true, false], ['send_mail', true, false], ['change_calendar', false, false]]);
    let saveStarted!: (r: Route) => void;
    const saving = new Promise<Route>(resolve => { saveStarted = resolve; });
    await page.route('**/api/v2/google/permissions', (route: Route) => route.request().method() === 'PUT' ? saveStarted(route) : route.fallback());
    await card.locator('[data-google-may=send_mail]').uncheck();
    const save = await saving;
    const body = save.request().postDataJSON();
    assert.deepEqual(body, {organize_mail: true, send_mail: false, change_calendar: false});
    // Out by the breadcrumb and back while the save is in flight: the server still reads Send on.
    await page.locator('#tools-inbox .tt-crumb a', {hasText: 'Tools'}).click();
    await page.locator('#tools-root').waitFor();
    await page.evaluate(() => { location.hash = '#/tools/settings'; });
    await card.locator('[data-google-may]').first().waitFor();
    await settle(page);
    assert.deepEqual(await controls(), [['organize_mail', true, true], ['send_mail', false, true], ['change_calendar', false, true]],
      'a card drawn mid-save shows what the save sends, locked');
    Object.assign(fake.may, {send: false});
    const answer = google.answer('/api/v2/google/permissions', save.request().url(), 'PUT', body);
    await save.fulfill({contentType: 'application/json', body: JSON.stringify(answer)});
    await page.waitForFunction(() => [...document.querySelectorAll<HTMLInputElement>('#mailboxes-may [data-google-may]')].every(el => !el.disabled));
    assert.deepEqual(await controls(), [['organize_mail', true, false], ['send_mail', false, false], ['change_calendar', false, false]]);
    // The next save carries Send off, never the old Send on.
    await page.unroute('**/api/v2/google/permissions');
    const second = page.waitForRequest(r => new URL(r.url()).pathname === '/api/v2/google/permissions' && r.method() === 'PUT');
    await card.locator('[data-google-may=organize_mail]').uncheck();
    assert.deepEqual((await second).postDataJSON(), {organize_mail: false, send_mail: false, change_calendar: false});
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'phone width: Add tool, Settings and a page fit with no sideways scroll, light and dark': async (browser: import('playwright').Browser) => {
    for (const theme of ['light', 'dark']) {
      const fake = mailboxesFake(), more = others();
      const {page, seen} = await open(browser, fake, googleFake(), {ready: '#tools-root [data-tl-add]', extra: more.extra, width: 390, height: 844, theme});
      // Add tool opens inside the screen, every item whole.
      await page.locator('[data-tl-add] > summary').click();
      const menu = (await page.locator('.tt-add-menu').boundingBox())!;
      assert.ok(menu.x >= 0 && menu.x + menu.width <= 390, `the Add tool menu stays on screen (${menu.x}..${menu.x + menu.width})`);
      assert.ok(await page.evaluate(() => { const m = document.querySelector('#main'); return !m || m.scrollWidth <= m.clientWidth; }),
        'opening it scrolls nothing sideways');
      assert.ok(await page.locator('.tt-add-menu button').evaluateAll(bs => bs.every(b => b.scrollWidth <= b.clientWidth)), 'no item is cut off');
      await page.locator('[data-tl-add] > summary').click();
      await page.evaluate(() => { location.hash = '#/tools/settings'; });
      await page.locator('#settings-mail-addresses [data-pm-address="as_1"]').waitFor();
      await settle(page);
      assert.ok(await fits(page), `Settings fits a phone (${theme})`);
      await shot(page, `inboxes-settings-phone-${theme}.png`, theme);
      await page.evaluate(() => { location.hash = '#/tools/number/ibx_a'; });
      await page.locator('[data-inbound-read]').waitFor();
      assert.ok(await fits(page), `a page fits a phone (${theme})`);
      await shot(page, `inboxes-detail-phone-${theme}.png`, theme);
      assert.deepEqual(seen.errors, []);
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
