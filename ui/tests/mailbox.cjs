// Offline browser regression: one mailbox's page in Settings > Mailboxes (ui/app/mailbox.js, #/settings/mailboxes/<id>)
// over a fake GET /v2/mailboxes/<id> (ui/tests/support/mailboxes-fake.cjs). Whoever manages it (an admin for a shared
// inbox, the owner for a personal mailbox) adds access, switches Read / Read and write and removes it; the implicit
// row (Admins, or the owner) has no controls. Routines switch on and off from here. Stop using in Tico confirms in the
// page and returns to the list. Anyone else reads the page with no controls.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {googleFake} = require('./support/google-fake.cjs');
const {mailboxesFake, open} = require('./support/mailboxes-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const READY = '#mailbox-access tr[data-mb-access-row]';
const at = id => ({hash: `#/settings/mailboxes/${id}`, ready: READY});
const accessRows = page => page.$$eval('#mailbox-access tr[data-mb-access-row]', rows => rows.map(row => {
  const cells = [...row.querySelectorAll('td')];
  const picked = row.querySelector('[data-mb-level][aria-checked=true]');
  return [cells[0].textContent.replace(/\s+/g, ' ').trim(), picked ? `[${picked.textContent}]` : cells[1].textContent.trim(),
    !!row.querySelector('[data-mb-remove]')];
}));
const writes = (fake, method, path) => fake.writes.filter(w => w.method === method && w.path === path).map(w => w.body);
const until = async (page, check, what) => {
  const end = Date.now() + 8000;
  while (!check()) {
    if (Date.now() > end) throw new Error(`Timed out waiting for ${what}`);
    await page.waitForTimeout(50);
  }
};

const scenarios = {
  'an admin on a shared inbox adds, changes and removes access; the Admins row has no controls': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    assert.equal(await page.evaluate(() => location.hash), '#/settings/mailboxes/mb-support');
    assert.equal(await page.locator('#mailbox-head h2').textContent(), 'support@acme.example');
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Shared inbox · connected · Tico may read and organize');
    assert.equal(await page.locator('[data-mb-list]').isHidden(), true, 'the list is not drawn under the page');
    assert.deepEqual(await accessRows(page), [
      ['Admins', 'Read and write', false],
      ['Cara', '[Read]', true],
      ['Support bot · bot', '[Read and write]', true],
    ]);
    // Add access: the people and bots who have none yet.
    const form = page.locator('[data-mb-add-form]');
    assert.equal(await form.isHidden(), true);
    await page.locator('[data-mb-add-open]').click();
    assert.deepEqual(await form.locator('[name=who] option').allTextContents(), ['Ben', 'Ops (bot)']);
    await form.locator('[name=who]').selectOption('bot:ops');
    await form.locator('[name=level]').selectOption('read_write');
    await form.locator('[type=submit]').click();
    await page.locator('tr[data-mb-access-row=acc-ops]').waitFor();
    assert.deepEqual(writes(fake, 'POST', '/api/v2/mailboxes/mb-support/accesses'),
      [{grantee_type: 'bot', grantee_id: 'ops', level: 'read_write'}]);
    // Read -> Read and write.
    await page.locator('tr[data-mb-access-row=acc-cara] [data-mb-level=read_write]').click();
    await page.locator('tr[data-mb-access-row=acc-cara] [data-mb-level=read_write][aria-checked=true]').waitFor();
    assert.deepEqual(writes(fake, 'PATCH', '/api/v2/mailboxes/mb-support/accesses/acc-cara'), [{level: 'read_write'}]);
    // The level already picked sends nothing.
    await page.locator('tr[data-mb-access-row=acc-cara] [data-mb-level=read_write]').click();
    await page.waitForTimeout(200);
    assert.equal(writes(fake, 'PATCH', '/api/v2/mailboxes/mb-support/accesses/acc-cara').length, 1);
    // Remove.
    await page.locator('[data-mb-remove=acc-bot]').click();
    await page.locator('tr[data-mb-access-row=acc-bot]').waitFor({state: 'detached'});
    assert.equal(writes(fake, 'DELETE', '/api/v2/mailboxes/mb-support/accesses/acc-bot').length, 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a routine switches on and off from the mailbox, and a refusal puts the switch back': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    const row = page.locator('[data-mb-routine-row=au-triage]');
    assert.equal(await row.locator('a').getAttribute('href'), '#/automations/au-triage');
    assert.equal(await row.locator('a').textContent(), 'Support triage');
    const on = row.locator('[data-mb-routine]');
    assert.equal(await on.isChecked(), true);
    await on.click();   // a click, not uncheck(): a second draw of the page may replace the switch mid-click
    await until(page, () => writes(fake, 'PATCH', '/api/v2/automations/au-triage').length, 'the routine PATCH');
    await page.waitForFunction(() => document.querySelector('[data-mb-routine=au-triage]')?.checked === false);
    assert.deepEqual(writes(fake, 'PATCH', '/api/v2/automations/au-triage'), [{enabled: false}]);
    await page.close();
    // Without the right to change it, the switch is off limits.
    fake.state.routines['support@acme.example'][0].can_edit = false;
    const second = await open(browser, fake, googleFake(), at('mb-support'));
    assert.equal(await second.page.locator('[data-mb-routine=au-triage]').isDisabled(), true);
    await second.page.close();
    // A refusal from the server.
    Object.assign(fake.state.routines['support@acme.example'][0], {can_edit: true, enabled: true});
    const third = await open(browser, fake, googleFake(), {...at('mb-support'), automations: {refuse: 'Not yours to change'}});
    const box = third.page.locator('[data-mb-routine=au-triage]');
    await third.page.waitForFunction(() => document.querySelector('[data-mb-routine=au-triage]')?.checked === true);
    await box.click();
    await until(third.page, () => writes(fake, 'PATCH', '/api/v2/automations/au-triage').length === 2, 'the refused PATCH');
    await third.page.waitForFunction(() => {
      const el = document.querySelector('[data-mb-routine=au-triage]');
      return el && !el.disabled && el.checked;
    });
    assert.deepEqual([...seen.errors, ...third.seen.errors, ...second.seen.errors], []);
    await third.page.close();
  },

  'Stop using in Tico confirms in the page, then shows the list with the mailbox not in use': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    page.on('dialog', () => { throw new Error('no browser dialog'); });
    await page.locator('[data-mb-stop]').click();
    const dialog = page.locator('dialog.settings-confirm');
    await dialog.waitFor();
    assert.equal(await dialog.locator('.tmodal-title').textContent(), 'Stop using in Tico?');
    await dialog.locator('[data-confirm-cancel]').click();
    await dialog.waitFor({state: 'detached'});
    assert.equal(writes(fake, 'DELETE', '/api/v2/mailboxes/mb-support').length, 0, 'Cancel stops nothing');
    await page.locator('[data-mb-stop]').click();
    await page.locator('dialog.settings-confirm [data-confirm-ok]').click();
    await page.locator('[data-mb-row=mb-support]').waitFor();
    assert.equal(writes(fake, 'DELETE', '/api/v2/mailboxes/mb-support').length, 1);
    assert.equal(await page.evaluate(() => location.hash), '#/settings/mailboxes');
    assert.equal(await page.locator('#set-mailbox').isHidden(), true);
    assert.equal(await page.locator('[data-mb-row=mb-support] td').nth(2).textContent(), 'Not in use');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'the back link returns to the list and its address': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-ana'));
    await page.locator('[data-mb-back]').click();
    await page.locator('#mailboxes-list tr[data-mb-row]').first().waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/mailboxes');
    assert.equal(await page.locator('#set-mailbox').isHidden(), true);
    // And the address opens it again.
    await page.locator('#mailboxes-list a[href="#/settings/mailboxes/mb-ben"]').click();
    await page.locator('#mailbox-head h2', {hasText: 'ben@acme.example'}).waitFor();
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'the owner manages their own mailbox; an admin reads someone else\'s and may only stop it': async browser => {
    const fake = mailboxesFake();
    let {page, seen} = await open(browser, fake, googleFake(), at('mb-ana'));
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Ana · connected · Tico may read and organize');
    assert.deepEqual(await accessRows(page), [['Ana', 'Read and write', false]]);
    assert.equal(await page.locator('[data-mb-add-open]').count(), 1);
    assert.equal(await page.locator('[data-mb-stop]').count(), 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
    ({page, seen} = await open(browser, fake, googleFake(), at('mb-ben')));
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Ben · connected');
    assert.deepEqual(await accessRows(page), [['Ben', 'Read and write', false]]);
    assert.equal(await page.locator('[data-mb-add-open]').count(), 0);
    assert.equal(await page.locator('[data-mb-stop]').count(), 1, 'an admin may stop any mailbox');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'someone with Read access sees who can use it, and no controls': async browser => {
    const fake = mailboxesFake({admin: false, me: 'cara'});
    fake.state.mailboxes.find(m => m.id === 'mb-support').level = 'read';
    const google = googleFake();
    google.state.can_admin = false;
    const {page, seen} = await open(browser, fake, google, at('mb-support'));
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Shared inbox · connected · Tico may read');
    assert.deepEqual(await accessRows(page), [
      ['Admins', 'Read and write', false],
      ['Cara', 'Read', false],
      ['Support bot · bot', 'Read and write', false],
    ]);
    assert.equal(await page.locator('#mailbox-access [data-mb-level]').count(), 0);
    assert.equal(await page.locator('[data-mb-add-open]').count(), 0);
    assert.equal(await page.locator('[data-mb-add-form]').count(), 0);
    assert.equal(await page.locator('[data-mb-stop]').count(), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'phone width: the page fits with no sideways scroll': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), {...at('mb-support'), width: 390});
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
