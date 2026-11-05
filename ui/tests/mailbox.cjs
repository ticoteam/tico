// Offline browser regression: one Google mailbox's page in Settings > Inboxes (ui/app/inboxes-email.js,
// #/settings/inboxes/mailbox/<id>) over a fake GET /v2/mailboxes/<id> (ui/tests/support/mailboxes-fake.cjs). Whoever
// manages it (an admin for a shared inbox, the owner for a personal mailbox) adds access, switches Read / Read and write
// and removes it; the implicit row (Admins, or the owner) has no controls. Automations switch on and off from here.
// Stop using in Tico confirms in the page and returns to the list. Anyone else reads the page with no controls. A
// refused sign-in or a failing sync shows on the page with its reason; Check connection asks Google for a Workspace
// mailbox and reads the page again; Recent lists its newest mail. A card another file registers with inboxesPanel
// shows under the mailbox's own.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {googleFake} = require('./support/google-fake.cjs');
const {mailboxesFake, open} = require('./support/mailboxes-fake.cjs');
const launch = () => chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined});

const READY = '#mailbox-access tr[data-mb-access-row]';
const at = id => ({hash: `#/settings/inboxes/mailbox/${id}`, ready: READY});
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
const pill = page => page.locator('#inbox-head [data-ib-status]');

const scenarios = {
  'an admin on a shared inbox adds, changes and removes access; the Admins row has no controls': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes/mailbox/mb-support');
    assert.equal(await page.locator('#inbox-head h2').textContent(), 'support@acme.example');
    assert.equal(await page.locator('[data-ib-crumb]').textContent(), 'support@acme.example');
    assert.equal(await pill(page).textContent(), 'Connected');
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Shared inbox · Not synced yet · Tico may read and organize');
    assert.equal(await page.locator('[data-ib-list]').count(), 0, 'the list is not drawn under the page');
    assert.deepEqual(await page.locator('.ib-detail .card > header h2').allTextContents(), ['Who can use it', 'Automations', 'Recent']);
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
    await page.locator('[data-ib-row="mailbox:mb-support"]').waitFor();
    assert.equal(writes(fake, 'DELETE', '/api/v2/mailboxes/mb-support').length, 1);
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes');
    assert.equal(await page.locator('[data-ib-detail]').count(), 0);
    assert.equal(await page.locator('[data-ib-row="mailbox:mb-support"] [data-ib-status]').textContent(), 'Not in use');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'the breadcrumb returns to the list and its address, and the list opens another mailbox': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-ana'));
    await page.locator('.ib-crumb a[href="#/settings/inboxes"]').click();
    await page.locator('[data-ib-row]').first().waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/settings/inboxes');
    assert.equal(await page.locator('[data-ib-detail]').count(), 0);
    // And the address opens it again: a second visit to a page drawn before is drawn afresh.
    await page.locator('[data-ib-row="mailbox:mb-ben"] .ib-name a').click();
    await page.locator('#inbox-head h2', {hasText: 'ben@acme.example'}).waitFor();
    await page.locator('.ib-crumb a[href="#/settings/inboxes"]').click();
    await page.locator('[data-ib-row]').first().waitFor();
    await page.locator('[data-ib-row="mailbox:mb-ben"] .ib-name a').click();
    await page.locator('#inbox-head h2', {hasText: 'ben@acme.example'}).waitFor();
    await page.locator(READY).first().waitFor();
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'the owner manages their own mailbox; an admin reads someone else\'s and may only stop it': async browser => {
    const fake = mailboxesFake();
    let {page, seen} = await open(browser, fake, googleFake(), at('mb-ana'));
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Ana · Not synced yet · Tico may read and organize');
    assert.deepEqual(await accessRows(page), [['Ana', 'Read and write', false]]);
    assert.equal(await page.locator('[data-mb-add-open]').count(), 1);
    assert.equal(await page.locator('[data-mb-stop]').count(), 1);
    assert.deepEqual(seen.errors, []);
    await page.close();
    ({page, seen} = await open(browser, fake, googleFake(), at('mb-ben')));
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Ben · Not synced yet');
    assert.equal(await page.locator('[data-mb-recent]').textContent(), 'Only people who can use it see its mail.');
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
    assert.equal(await page.locator('[data-mb-status]').textContent(), 'Shared inbox · Not synced yet · Tico may read');
    assert.equal(await page.locator('[data-mb-check]').count(), 1, 'anyone who can use it checks its connection');
    assert.equal(await page.locator('[data-mb-share]').count(), 0);
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


  'a refused sign-in and a failing sync show on the page with the last sync, and the list says the same': async browser => {
    const fake = mailboxesFake();
    const support = fake.state.mailboxes.find(m => m.id === 'mb-support');
    Object.assign(support, {sync: 'signin', sync_error: 'Google refused the sign-in (invalid_grant)', synced_at: new Date(Date.now() - 3 * 3600e3).toISOString()});
    Object.assign(fake.state.mailboxes.find(m => m.id === 'mb-ben'), {sync: 'failed', sync_error: 'Gmail answered 500'});
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    assert.equal(await pill(page).textContent(), 'Sign-in expired');
    assert.equal(await pill(page).getAttribute('data-ib-status'), 'attention');
    assert.equal(await page.locator('[data-mb-sync-error]').textContent(), 'Google refused the sign-in (invalid_grant)');
    assert.match(await page.locator('[data-mb-status]').textContent(), /^Shared inbox · Last sync 3h ago · Tico may/);
    assert.equal(await page.locator('[data-mb-signin] a').getAttribute('href'), '#/settings/inboxes/settings/email');
    await page.locator('.ib-crumb a[href="#/settings/inboxes"]').click();
    await page.locator('[data-ib-row="mailbox:mb-ben"]').waitFor();
    const status = id => page.locator(`[data-ib-row="mailbox:${id}"] [data-ib-status]`).textContent();
    assert.deepEqual([await status('mb-support'), await status('mb-ben'), await status('mb-dan'), await status('mb-old'), await status('mb-ana')],
      ['Sign-in expired', 'Sync failed', 'Awaiting person', 'Not in use', 'Connected']);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Check connection asks Google about a Workspace mailbox, then reads the page again': async browser => {
    const fake = mailboxesFake();
    const google = googleFake();
    google.checks.push('ok');
    const {page, seen} = await open(browser, fake, google, at('mb-support'));
    fake.state.mailboxes.find(m => m.id === 'mb-support').synced_at = new Date(Date.now() - 60e3).toISOString();
    await page.locator('[data-mb-check]').click();
    await page.locator('[data-mb-status]', {hasText: 'Last sync'}).waitFor();
    assert.deepEqual(google.writes.filter(w => w.path === '/api/v2/google/workspace/check').map(w => w.body), [{mailbox: 'support@acme.example'}]);
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'Recent lists the newest mail, each a link to its thread': async browser => {
    const fake = mailboxesFake();
    const mail = {'support@acme.example': [
      {thread_id: 'th-1', subject: 'Refund for order 1042', from_header: 'Dana <dana@customer.example>', epoch: Math.floor(Date.now() / 1000) - 600},
      {thread_id: 'th-2', subject: '', from_addr: 'noreply@shop.example', epoch: Math.floor(Date.now() / 1000) - 7200}]};
    const {page, seen} = await open(browser, fake, googleFake(), {...at('mb-support'), mail});
    await page.locator('[data-mb-recent] .ib-recent li').nth(1).waitFor();
    assert.deepEqual(await page.locator('[data-mb-recent] .ib-recent-what a').allTextContents(), ['Refund for order 1042', '(no subject)']);
    assert.equal(await page.locator('[data-mb-recent] a').first().getAttribute('href'), '#/mail?mailbox=support%40acme.example&thread=th-1');
    assert.equal(await page.locator('#mailbox-recent header a').getAttribute('href'), '#/mail?mailbox=support%40acme.example');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'a card another file registers for mailboxes shows under the page with the mailbox it was given': async browser => {
    const fake = mailboxesFake();
    const {page, seen} = await open(browser, fake, googleFake(), at('mb-support'));
    await page.evaluate(() => {
      inboxesPanel('test-panel', {kinds: ['mailbox'], order: 1, render: (host, inbox) => {
        host.innerHTML = `<section class="card" data-test-panel>${esc(inbox.kind)} ${esc(inbox.address)} ${inbox.can_manage}</section>`;
      }});
      inboxesPanel('test-other', {kinds: ['tico'], order: 2, render: host => { host.innerHTML = '<p data-test-other>no</p>'; }});
      return renderSettingsInboxes();
    });
    await page.locator('[data-test-panel]').waitFor();
    assert.equal(await page.locator('[data-test-panel]').textContent(), 'mailbox support@acme.example true');
    assert.equal(await page.locator('[data-test-other]').count(), 0, 'a panel shows only on the kinds it names');
    assert.deepEqual(seen.errors, []);
    await page.close();
  },

  'phone width: the page fits with no sideways scroll, light and dark': async browser => {
    const fake = mailboxesFake();
    const mail = {'support@acme.example': [{thread_id: 'th-1', subject: 'A subject long enough to wrap on a phone screen without pushing the page', from_header: 'dana@customer.example', epoch: 1}]};
    for (const theme of ['light', 'dark']) {
      const {page, seen} = await open(browser, fake, googleFake(), {...at('mb-support'), width: 390, height: 844, mail, theme});
      await page.locator('[data-mb-recent] .ib-recent li').waitFor();
      const widths = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth}));
      assert.ok(widths.scroll <= widths.client, `page scrolls sideways: ${widths.scroll} > ${widths.client}`);
      if (process.env.TICO_SHOTS) await page.screenshot({path: `${process.env.TICO_SHOTS}/inboxes-mailbox-phone-${theme}.png`, fullPage: true});
      assert.deepEqual(seen.errors, []);
      await page.close();
    }
    const {page, seen} = await open(browser, fake, googleFake(), {...at('mb-support'), mail});
    await page.locator('[data-mb-recent] .ib-recent li').waitFor();
    if (process.env.TICO_SHOTS) await page.screenshot({path: `${process.env.TICO_SHOTS}/inboxes-mailbox-desktop-light.png`, fullPage: true});
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
