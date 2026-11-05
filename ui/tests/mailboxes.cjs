// Offline browser regression: Settings > Mailboxes (ui/app/mailboxes.js) over a fake GET /v2/mailboxes
// (ui/tests/support/mailboxes-fake.cjs). An admin sees the Connection card, every mailbox with its person, status and
// routines, Connect mailbox, Make shared inbox on an address with no person, and What Tico may do saving all three
// boxes together. A member sees their own mailbox (when the company's key covers it) and the ones shared with them,
// with no table and no What Tico may do. A phone width has no sideways scroll.
// One mailbox's page is ui/tests/mailbox.cjs; the Connection card's body is ui/tests/google-tab.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {googleFake} = require('./support/google-fake.cjs');
const {mailboxesFake, box, open} = require('./support/mailboxes-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const ADMIN_READY = '#mailboxes-list tr[data-mb-row]';
const cells = page => page.$$eval('#mailboxes-list tbody tr', rows => rows.map(row =>
  [...row.querySelectorAll('td')].map(td => td.textContent.replace(/\s+/g, ' ').trim())));
const writesTo = (fake, path, method = 'POST') => fake.writes.filter(w => w.path === path && w.method === method);

// A member (Cara): her own mailbox, covered by the company's key, and the support inbox shared with her for Read.
function member() {
  const fake = mailboxesFake({admin: false, me: 'cara'});
  fake.state.mailboxes = [
    box('mb-cara', 'cara@acme.example', {person_id: 'cara', person_name: 'Cara', level: 'read_write'}),
    box('mb-support', 'support@acme.example', {kind: 'shared', level: 'read'}),
    box('mb-ben', 'ben@acme.example', {person_id: 'ben', person_name: 'Ben', level: 'read'}),
  ];
  const google = googleFake();
  google.state.can_admin = false;
  google.state.me = {mailbox: 'cara@acme.example', covered_by_workspace: true};
  return {fake, google};
}

const scenarios = {
  'an admin sees every mailbox: its person, status and routines, each address opening its page': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY});
    assert.equal(await page.locator('#mailboxes-top h2').textContent(), 'Connection');
    assert.equal(await page.locator('[data-mb-provider]').textContent(), 'Google Workspace · acme.example');
    await page.locator('#set-google [data-google-mode]').waitFor();
    assert.deepEqual(await cells(page), [
      ['ana@acme.example', 'Ana', 'Connected', 'None'],
      ['ben@acme.example', 'Ben', 'Connected', 'None'],
      ['support@acme.example', 'Shared inbox', 'Connected', 'Support triage'],
      ['dan@acme.example', 'Awaiting person Make shared inbox', 'Private', 'None'],
      ['old@acme.example', 'Awaiting person', 'Not in use', 'None'],
    ]);
    assert.deepEqual(await page.$$eval('#mailboxes-list tbody a', as => as.map(a => a.getAttribute('href'))),
      ['mb-ana', 'mb-ben', 'mb-support', 'mb-dan', 'mb-old'].map(id => `#/settings/mailboxes/${id}`));
    assert.equal(await page.locator('[data-mb-mine-box]').count(), 0, 'an admin has no "your mailbox" line');
    assert.equal(await page.locator('#mailboxes-shared').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Connect mailbox adds the address, and an address already in Tico says so by the field': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY});
    const form = page.locator('[data-mb-form]');
    assert.equal(await form.isHidden(), true);
    await page.locator('[data-mb-connect-open]').click();
    await form.waitFor();
    await form.locator('[name=address]').fill(' Help@Acme.example ');
    await form.locator('[name=shared]').check();
    await form.locator('[type=submit]').click();
    await page.locator('#mailboxes-list a[href="#/settings/mailboxes/mb-new1"]').waitFor();
    assert.deepEqual(writesTo(fake, '/api/v2/mailboxes').map(w => w.body), [{address: 'Help@Acme.example', shared: true}]);
    assert.equal(await page.locator('[data-mb-row=mb-new1] td').nth(1).textContent(), 'Shared inbox');
    assert.equal(await form.isHidden(), true, 'the form closes on success');
    await page.locator('[data-mb-connect-open]').click();
    await form.locator('[name=address]').fill('ana@acme.example');
    await form.locator('[type=submit]').click();
    await page.locator('[data-mb-form] [data-field-err]').waitFor();
    assert.match(await page.locator('[data-mb-form] [data-field-err]').textContent(), /ana@acme\.example is already in Tico/);
    assert.equal(await form.locator('[name=address]').inputValue(), 'ana@acme.example', 'the address stays to fix');
    await page.locator('[data-mb-connect-cancel]').click();
    assert.equal(await form.isHidden(), true);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Make shared inbox turns an address with no person into a shared inbox': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY});
    await page.locator('[data-mb-share=mb-dan]').click();
    await page.locator('[data-mb-row=mb-dan] td:nth-child(2)', {hasText: /^Shared inbox$/}).waitFor();
    assert.equal(writesTo(fake, '/api/v2/mailboxes/mb-dan/share').length, 1);
    assert.equal(await page.locator('[data-mb-row=mb-dan] td').nth(2).textContent(), 'Connected');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a Mailboxes read from before a permission save cannot replace the controls during or after it': async browser => {
    for (const finishRead of ['during', 'after']) {
      const fake = mailboxesFake(), google = googleFake();
      const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY});
      const card = page.locator('#mailboxes-may');
      const calendar = await card.locator('[data-google-may=change_calendar]').elementHandle();
      let readStarted, saveStarted;
      const reading = new Promise(resolve => { readStarted = resolve; });
      const saving = new Promise(resolve => { saveStarted = resolve; });
      await page.route('**/api/v2/mailboxes', route => readStarted(route));
      await page.route('**/api/v2/google/permissions', route => saveStarted(route));
      await page.evaluate(() => { window.mailboxesRead = renderSettingsMailboxes(); });
      const read = await reading;
      const stale = fake.answer('/api/v2/mailboxes');
      await card.locator('[data-google-may=change_calendar]').check();
      const save = await saving;
      const body = save.request().postDataJSON();
      assert.deepEqual(body, {organize_mail: true, send_mail: false, change_calendar: true});
      const controls = () => card.locator('[data-google-may]').evaluateAll(els =>
        els.map(el => [el.dataset.googleMay, el.checked, el.disabled]));
      const pending = [['organize_mail', true, true], ['send_mail', false, true], ['change_calendar', true, true]];
      assert.deepEqual(await controls(), pending);
      const finish = async () => {
        await read.fulfill({contentType: 'application/json', body: JSON.stringify(stale)});
        await page.evaluate(() => window.mailboxesRead);
        assert.equal(await calendar.evaluate(el => el.isConnected), true, `${finishRead}: the clicked control stays mounted`);
      };
      if (finishRead === 'during') {
        await finish();
        assert.deepEqual(await controls(), pending, 'an old read cannot roll back or unlock a pending save');
      }
      await page.unroute('**/api/v2/mailboxes');
      await page.evaluate(() => renderSettingsMailboxes());
      assert.deepEqual(await controls(), pending, 'another render leaves the pending save alone');
      assert.equal(await calendar.evaluate(el => el.isConnected), true);
      const answer = google.answer('/api/v2/google/permissions', save.request().url(), 'PUT', body);
      Object.assign(fake.may, {organize: true, send: false, change_calendar: true});
      await save.fulfill({contentType: 'application/json', body: JSON.stringify(answer)});
      await page.waitForFunction(() => [...document.querySelectorAll('#mailboxes-may [data-google-may]')].every(el => !el.disabled));
      if (finishRead === 'after') await finish();
      assert.deepEqual(await controls(), pending.map(([field, checked]) => [field, checked, false]));
      await page.evaluate(() => renderSettingsMailboxes());
      assert.deepEqual(await controls(), pending.map(([field, checked]) => [field, checked, false]), 'a fresh read shows the saved settings');
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
  },

  'What Tico may do: Read always, each box saves all three together, a refusal puts the box back': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY});
    const card = page.locator('#mailboxes-may');
    assert.equal(await card.locator('h2').textContent(), 'What Tico may do');
    assert.equal(await card.locator('[data-google-may-read] input').count(), 0, 'Read has no box');
    const boxes = () => card.locator('[data-google-may]').evaluateAll(els => els.map(el => [el.dataset.googleMay, el.checked]));
    assert.deepEqual(await boxes(), [['organize_mail', true], ['send_mail', false], ['change_calendar', false]]);
    const saved = page.waitForResponse(response => response.url().endsWith('/api/v2/google/permissions') && response.request().method() === 'PUT');
    await card.locator('[data-google-may=change_calendar]').check();
    assert.equal((await saved).status(), 200);
    assert.deepEqual(writesTo(google, '/api/v2/google/permissions', 'PUT').map(w => w.body),
      [{organize_mail: true, send_mail: false, change_calendar: true}]);
    await page.waitForFunction(() => [...document.querySelectorAll('[data-google-may]')].every(el => !el.disabled));
    // Hold the refusal until check() has verified the optimistic state; rollback is asserted after the answer.
    let refuse;
    const refusal = new Promise(resolve => { refuse = resolve; });
    await page.route('**/api/v2/google/permissions', async route => {
      await refusal;
      await route.fulfill({status: 403, contentType: 'application/json',
        body: JSON.stringify({error: {code: 'forbidden', detail: 'Only an admin changes this', retryable: false}})});
    });
    const refused = page.waitForRequest(request => request.url().endsWith('/api/v2/google/permissions') && request.method() === 'PUT');
    await card.locator('[data-google-may=send_mail]').check();
    assert.deepEqual((await refused).postDataJSON(), {organize_mail: true, send_mail: true, change_calendar: true});
    assert.deepEqual(await boxes(), [['organize_mail', true], ['send_mail', true], ['change_calendar', true]]);
    assert.equal(await card.locator('[data-google-may]:disabled').count(), 3, 'the boxes stay locked while the server has not answered');
    refuse();
    await page.waitForFunction(() => {
      const el = document.querySelector('[data-google-may=send_mail]');
      return el && !el.disabled && !el.checked;
    });
    assert.deepEqual(await boxes(), [['organize_mail', true], ['send_mail', false], ['change_calendar', true]]);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a member sees their own mailbox and the ones shared with them, and no table or What Tico may do': async browser => {
    const {fake, google} = member();
    const {page, seen} = await open(browser, fake, google, {ready: '#mailboxes-shared'});
    assert.equal(await page.locator('#mailboxes-top h2').textContent(), 'Your mailbox');
    const mine = page.locator('[data-mb-mine-box]');
    assert.match((await mine.textContent()).replace(/\s+/g, ' '), /cara@acme\.example · connected by your company/);
    assert.match((await mine.textContent()).replace(/\s+/g, ' '), /Tico may read and organize\. Only you and your Assistant can use it\./);
    assert.deepEqual(await page.$$eval('#mailboxes-shared li', lis => lis.map(li => li.textContent.replace(/\s+/g, ' ').trim())),
      ['support@acme.example · shared inbox', 'ben@acme.example · Ben']);
    assert.equal(await page.locator('#mailboxes-shared a').first().getAttribute('href'), '#/settings/mailboxes/mb-support');
    assert.equal(await page.locator('#mailboxes-list').count(), 0);
    assert.equal(await page.locator('#mailboxes-may').count(), 0);
    assert.equal(await page.locator('[data-mb-connect-open]').count(), 0);
    assert.equal(await page.locator('#set-google [data-google-mode]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a member whose mailbox is not covered by the company sees no "your mailbox" line, and nothing shared shows no card': async browser => {
    const {fake, google} = member();
    fake.state.mailboxes = [box('mb-cara', 'cara@acme.example', {person_id: 'cara', person_name: 'Cara', level: 'read_write',
      connection: 'personal'})];
    google.state.me = {mailbox: 'cara@acme.example', covered_by_workspace: false};
    const {page, seen} = await open(browser, fake, google, {ready: '#set-google .google-card'});
    await page.waitForFunction(() => !document.querySelector('[data-mb-body] .skel'));
    assert.equal(await page.locator('[data-mb-mine-box]').count(), 0);
    assert.equal(await page.locator('#mailboxes-shared').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'phone width: the table and What Tico may do fit with no sideways scroll': async browser => {
    const fake = mailboxesFake(), google = googleFake();
    const {page, seen} = await open(browser, fake, google, {ready: ADMIN_READY, width: 390});
    await page.locator('#mailboxes-may').waitFor();
    const widths = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth}));
    assert.ok(widths.scroll <= widths.client, `page scrolls sideways: ${widths.scroll} > ${widths.client}`);
    assert.deepEqual(seen.errors, []);
    await page.close();
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
