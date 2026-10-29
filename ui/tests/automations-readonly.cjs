// Automations for a viewer without edit rights (can_edit false on every automation, CONTRACT §2) against the fake hub in
// support/automations-fake.cjs: no switches, no "+", the spend limit is shown but not a button, an editor shows its
// fields off with no Save, ⋯ offers Versions only and Versions offers no Restore; a proposal can be read, not turned on.
// A phone first, then a computer. Then the built-in switch rule: only the top of the org (can_pause) gets the switch on a
// built-in; anyone else reads "Built-in: can’t be turned off". A system job says what it does and that it is fixed.
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
      await page.waitForSelector('tr.au-row');
      assert.equal(await page.locator('.au-sw').count(), 0);
      await page.locator('tr.au-row[data-row="au_inbox"] a').click();
      await readOnly(page, writes);
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-readonly');
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A computer: the same, and the bot's proposal in Versions has View diff only.
    {
      const {page, errors, writes, db} = await openAutomations(browser, {viewport: DESKTOP, viewer: 'priya', hash: '#/automations/au_inbox'});
      await readOnly(page, writes);
      db.versions.au_inbox.push({n: 8, actor: 'bot:support', at: new Date().toISOString(), status: 'pending', summary: 'Archive receipts before triage',
        flow: {trigger: db.list[0].trigger, steps: db.list[0].steps}});
      await page.evaluate(() => { location.hash = '#/automations/au_tasks'; });
      await page.waitForSelector('.au-hd .au-name:text("Task triage")');
      assert.match(await page.locator('.au-hd').innerText(), /Built-in: can’t be turned off/);
      await page.evaluate(() => { location.hash = '#/automations/au_inbox'; });
      // The proposal waits in Versions; a viewer gets View diff only.
      await page.locator('.au-ver').click();
      await page.waitForSelector('.au-prop[data-n="8"]');
      assert.deepEqual(await page.locator('.au-prop [data-op]').evaluateAll(b => b.map(x => x.dataset.op)), ['diff']);
      await shot(page, 'desktop-readonly');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A read-only built-in (can_edit false) the top of the org may pause (can_pause true): its switch shows, nothing else.
    {
      const tweak = fx => Object.assign(fx.automations.find(a => a.id === 'au_tasks'), {can_edit: false, can_pause: true, readonly: true});
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, tweak});
      await page.waitForSelector('tr.au-row[data-row="au_tasks"]');
      assert.equal(await page.locator('tr.au-row .au-sw').count(), 0, 'the list has no switches');
      await page.locator('tr.au-row[data-row="au_tasks"] a').click();
      await page.waitForSelector('.au-hd [data-on-cur]', {state: 'attached'});
      assert.equal(await page.locator('.au-hd .au-lock').count(), 0);
      assert.equal(await page.locator('.au-add').count(), 0, 'no "+"');
      assert.equal(await page.locator('.au-card .au-sw').count(), 0, 'no step switches');
      await page.locator('[data-more]').click();
      assert.deepEqual(await page.locator('.au-menu [data-mi]').evaluateAll(b => b.map(x => x.dataset.mi)), ['versions'], 'no Test run');
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A system job: the top of the org sees what it does, that it is fixed, and its switch; a member sees why there is none.
    for (const viewer of ['dana', 'priya']) {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, viewer, system: true});
      await page.waitForSelector('tr.au-grp[data-agrp="system"]');
      await page.locator('[data-ftrig="system"]').click();
      assert.deepEqual(await page.locator('tr.au-row').evaluateAll(r => r.map(x => x.dataset.row)), ['au_sys_still']);
      assert.match(await page.locator('tr.au-row').innerText(), /Daily at 07:00/);
      await page.locator('tr.au-row[data-row="au_sys_still"] a').click();
      await page.waitForSelector('.au-about');
      assert.match(await page.locator('.au-about').innerText(), /tasks open for 30 days[\s\S]*Built-in: can’t be edited or deleted/);
      assert.match(await page.locator('.au-trig').innerText(), /System job[\s\S]*Daily at 07:00/);
      assert.equal(await page.locator('.au-add').count(), 0);
      assert.equal(await page.locator('[data-spend]').count(), 0);
      const lock = page.locator('.au-hd .au-lock');
      if (viewer === 'dana') {
        assert.equal(await page.locator('.au-hd [data-on-cur]').count(), 1, 'the top of the org may pause it');
        assert.equal(await lock.count(), 0);
        await shot(page, 'desktop-system-job');
      } else {
        assert.equal(await page.locator('.au-hd [data-on-cur]').count(), 0);
        assert.match(await lock.innerText(), /Built-in: can’t be turned off/);
      }
      // Its runs come from the job's own record.
      await page.getByRole('tab', {name: 'Runs'}).click();
      await page.waitForSelector('.au-rr');
      assert.equal(await page.locator('.au-rr').count(), 8);
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations read-only ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
