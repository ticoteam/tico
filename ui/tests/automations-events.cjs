// Automations with an event trigger (ui/app/automations*.js) against the fake hub in support/automations-fake.cjs: an
// event reads as a few words everywhere (the list's Trigger column, the trigger card, a wake rule's condition, the
// editor), never its dotted id (task.done, tico.notice, tico.builtin.*); the id is only the chip's tooltip, and the
// editor still saves it; Custom… takes a name the catalog does not list, saved as typed. A phone first, then a computer.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

const base = {owner_id: 'person:dana', enabled: true, version: 1, steps: []};
const tweak = fx => fx.automations.push(
  // The built-ins the converter writes (backend/redesign_migration.py): read only, with rules on internal event names.
  {...base, id: 'au_wakes', name: 'What wakes bots', builtin: true, readonly: true, can_edit: false,
    trigger: {type: 'event', name: 'tico.builtin.wakes'},
    steps: [{id: 'b1', type: 'filter', enabled: true, rules: [
      {when: [{field: 'event', op: 'is', value: 'task.done'}], then: [{do: 'wake_bot', args: {}}], stop: true},
      {when: [{field: 'event', op: 'is', value: 'status_or_relation'}], then: [{do: 'archive', args: {}}], stop: true}]}]},
  {...base, id: 'au_job', name: 'Cleanup', builtin: true, readonly: true, can_edit: false, trigger: {type: 'event', name: 'tico.builtin.job.cleanup'}},
  {...base, id: 'au_rule', name: 'Close stale', builtin: true, readonly: true, can_edit: false, trigger: {type: 'event', name: 'tico.builtin.task_rule'}},
  {...base, id: 'au_notice', name: 'From Tico', trigger: {type: 'event', name: 'tico.notice'}},
  {...base, id: 'au_done', name: 'Done to Slack', trigger: {type: 'event', name: 'task.done'}},
  {...base, id: 'au_custom', name: 'Deploys', trigger: {type: 'event', name: 'custom.deploy_finished'}},
);
const RAW = /tico\.|task\.done|custom\.|status_or_relation|builtin/;
const rowText = (page, id) => page.locator(`tr.au-row[data-row="${id}"] td.au-tt`).innerText();

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: the list, then a built-in's flow.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, tweak});
      await page.waitForSelector('tr.au-row[data-row="au_wakes"]');
      const want = {au_wakes: 'Every event', au_job: 'System job', au_rule: 'Task rule', au_notice: 'Notice', au_done: 'Task finished',
        au_custom: 'Deploy finished'};
      for (const [id, words] of Object.entries(want)) assert.equal(await rowText(page, id), words, id);
      assert.doesNotMatch(await page.locator('.au-tbl').innerText(), RAW, 'no dotted ids in the list');
      // Search still finds one by its words.
      await page.locator('[data-filters]').click();
      await page.locator('.au-q').fill('task finished');
      assert.deepEqual(await page.locator('tr.au-row').evaluateAll(r => r.map(x => x.dataset.row)), ['au_done']);
      await page.locator('.au-q').fill('');
      await page.locator('[data-filters]').click();
      await shot(page, 'phone-events-list');

      await page.locator('tr.au-row[data-row="au_wakes"] a').click();
      await page.waitForSelector('.au-card[data-open="b1"]');
      const chip = page.locator('.au-trig .au-c1');
      assert.equal(await chip.innerText(), 'Every event');
      assert.equal(await chip.getAttribute('title'), 'tico.builtin.wakes', 'the id is the tooltip');
      assert.deepEqual(await page.locator('.au-card[data-open="b1"] .au-rule > span:first-child').allInnerTexts(), ['Task finished', 'Status or relation']);
      assert.doesNotMatch(await page.locator('.au-pane').innerText(), RAW, 'no dotted ids in the flow');
      // Its editor, read only: one Event field, in words.
      await chip.click();
      await page.waitForSelector('.au-ed:not([hidden]) [data-b="evpick"]');
      assert.equal(await page.locator('.au-ed .au-lb', {hasText: /^Event$/i}).count(), 1, 'one Event field');
      assert.equal(await page.locator('.au-ed [data-b="evpick"] option:checked').innerText(), 'Every event');
      assert.equal(await page.locator('.au-ed [data-b="evpick"]').isDisabled(), true);
      assert.equal(await page.locator('.au-ed [data-b="trigger.name"]').count(), 0, 'a built-in is no custom name');
      await shot(page, 'phone-events-builtin');
      assert.equal(await sideways(page), false);
      assert.deepEqual(writes, []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    // A computer: an event trigger of one's own changes event in words and saves the id.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, tweak, hash: '#/automations/au_notice'});
      await page.waitForSelector('.au-trig .au-c1');
      assert.equal(await page.locator('.au-trig .au-c1').innerText(), 'Notice');
      assert.doesNotMatch(await page.locator('.au-c').innerText(), RAW);
      await page.locator('.au-card[data-open="trigger"]').click();
      const pick = page.locator('.au-ed [data-b="evpick"]');
      await pick.waitFor();
      assert.equal(await pick.locator('option:checked').innerText(), 'Notice');
      const labels = await pick.locator('option').allInnerTexts();
      assert.ok(labels.includes('Task finished') && labels.includes('Message received'), 'the catalog in words');
      assert.ok(!labels.some(l => RAW.test(l)), 'no dotted ids among the choices');
      assert.equal(labels.at(-1), 'Custom…', 'Custom… comes last');
      assert.equal(await page.locator('.au-ed [data-b="trigger.name"]').count(), 0, 'no name box for a catalog event');
      await pick.selectOption('task.done');
      await page.locator('.au-ed .au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-trig .au-c1')?.textContent === 'Task finished');
      const saved = writes.find(w => w.method === 'PATCH' && w.body?.trigger);
      assert.deepEqual(saved.body.trigger, {type: 'event', name: 'task.done'});
      // A name of one's own: Custom… and a box for it; saved exactly as typed.
      await page.locator('.au-card[data-open="trigger"]').click();
      await pick.selectOption('__custom');
      const name = page.locator('.au-ed [data-b="trigger.name"]');
      await name.waitFor();
      assert.equal(await name.inputValue(), '', 'a catalog name is not carried into the box');
      assert.equal(await name.evaluate(el => el === document.activeElement), true, 'the box takes the cursor');
      await name.fill('custom.deploy_finished');
      await shot(page, 'desktop-events-custom');
      await page.locator('.au-ed .au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-trig .au-c1')?.textContent === 'Deploy finished');
      assert.deepEqual(writes.filter(w => w.method === 'PATCH' && w.body?.trigger).at(-1).body.trigger, {type: 'event', name: 'custom.deploy_finished'});
      // Back to a catalog event drops the box.
      await page.locator('.au-card[data-open="trigger"]').click();
      assert.equal(await pick.locator('option:checked').innerText(), 'Custom…');
      await pick.selectOption('tico.notice');
      assert.equal(await page.locator('.au-ed [data-b="trigger.name"]').count(), 0);
      await page.locator('.au-ed [data-op="close"]').click();
      // One the catalog does not list opens on Custom… with its name filled in.
      await page.evaluate(() => { location.hash = '#/automations/au_custom'; });
      await page.waitForSelector('.au-hd .au-name:text("Deploys")');
      assert.equal(await page.locator('.au-trig .au-c1').innerText(), 'Deploy finished');
      await page.locator('.au-card[data-open="trigger"]').click();
      assert.equal(await pick.locator('option:checked').innerText(), 'Custom…');
      assert.equal(await name.inputValue(), 'custom.deploy_finished');
      await shot(page, 'desktop-events-editor');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations events ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
