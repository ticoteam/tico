// Automations for a viewer without edit rights (can_edit false on every automation, CONTRACT §2) against the fake hub in
// support/automations-fake.cjs: no switches, no "+", the spend limit is shown but not a button, an editor shows its
// fields off with no Save, ⋯ offers Versions only and Versions offers no Restore; a proposal can be read, not turned on.
// A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

async function readOnly(page, writes) {
  await page.waitForSelector('.au-card[data-open="f1"]');
  assert.equal(await page.locator('.au-sw').count(), 0, 'no switches');
  assert.equal(await page.locator('.au-add').count(), 0, 'no "+"');
  assert.equal(await page.locator('[data-spend]').count(), 0);
  assert.match(await page.locator('.au-hd').innerText(), /\$5\.00 \/ 30 min/);
  await page.locator('.au-card[data-open="f1"]').click();
  await page.waitForSelector('.au-ed:not([hidden]) [data-b="step.rules.0.when.0.value"]');
  assert.equal(await page.locator('.au-ed [data-b]:not([disabled])').count(), 0, 'every field is off');
  assert.equal(await page.locator('.au-save').count(), 0);
  assert.equal(await page.locator('.au-ed [data-op="add"], .au-ed [data-op="rm"], .au-ed [data-op="del"]').count(), 0);
  await page.locator('.au-ed [data-op="close"]').click();
  await page.locator('[data-more]').click();
  assert.deepEqual(await page.locator('.au-menu [data-mi]').evaluateAll(b => b.map(x => x.dataset.mi)), ['versions']);
  await page.locator('.au-menu [data-mi="versions"]').click();
  await page.waitForSelector('.au-vr');
  assert.equal(await page.locator('[data-op="restore"]').count(), 0);
  await page.locator('.au-ed [data-op="close"]').click();
  assert.deepEqual(writes, []);
}

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, viewer: 'priya'});
      await page.waitForSelector('.au-row');
      assert.equal(await page.locator('.au-row .au-sw').count(), 0);
      assert.deepEqual(await page.locator('.au-gh > span:first-child').allTextContents(), ['Engineering', 'Company'], 'nothing of Priya’s own');
      await page.locator('.au-row[data-row="au_inbox"] a').click();
      await readOnly(page, writes);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-readonly');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A computer: the same, and the bot's proposal card has View diff only.
    {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport: DESKTOP, viewer: 'priya', hash: '#/automations/au_inbox'});
      await readOnly(page, writes);
      db.versions.au_inbox.push({n: 8, actor: 'bot:support', at: new Date().toISOString(), status: 'pending', summary: 'Archive receipts before triage',
        flow: {trigger: db.list[0].trigger, steps: db.list[0].steps}});
      await page.locator('.au-row[data-row="au_tasks"] a').click();
      await page.waitForSelector('.au-hd .au-name:text("Task triage")');
      await page.locator('.au-row[data-row="au_inbox"] a').click();
      await page.waitForSelector('.au-prop[data-n="8"]');
      assert.deepEqual(await page.locator('.au-prop [data-prop]').evaluateAll(b => b.map(x => x.dataset.prop)), ['diff']);
      await shot(page, 'desktop-readonly');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A read-only built-in (can_edit false) the top of the org may pause (can_pause true): its switch shows, nothing else.
    {
      const tweak = fx => Object.assign(fx.automations.find(a => a.id === 'au_tasks'), {can_edit: false, can_pause: true, readonly: true});
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, tweak});
      await page.waitForSelector('.au-row[data-row="au_tasks"]');
      assert.equal(await page.locator('.au-row[data-row="au_tasks"] .au-sw').count(), 1, 'the list shows its switch');
      await page.locator('.au-row[data-row="au_tasks"] a').click();
      await page.waitForSelector('.au-hd [data-on-cur]', {state: 'attached'});
      assert.equal(await page.locator('.au-add').count(), 0, 'no "+"');
      assert.equal(await page.locator('.au-card .au-sw').count(), 0, 'no step switches');
      await page.locator('[data-more]').click();
      assert.deepEqual(await page.locator('.au-menu [data-mi]').evaluateAll(b => b.map(x => x.dataset.mi)), ['versions'], 'no Test run');
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations read-only ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
