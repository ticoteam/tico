// Automations' task-event triggers (ui/app/automations-editors.js, Dev 666): "a task enters a step" does not save until a
// step is chosen, then saves one step filter on the chosen step. Runs on the fake hub in support/automations-fake.cjs.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, DESKTOP} = require('./support/automations-fake.cjs');

const TASK_AU = {id: 'au_task', name: 'Step test', owner_id: 'person:dana', enabled: false, builtin: false, version: 1,
  can_edit: true, trigger: {type: 'event', name: 'task.step_changed', filters: []}, steps: []};
const TYPES = [{id: 'dev', name: 'Dev tickets', steps: [{id: 'st_review', name: 'Review 1'}, {id: 'st_qa', name: 'In QA'}]}];

(async () => {
  const browser = await chromium.launch();
  try {
    const o = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_task',
      tweak: fx => fx.automations.push(JSON.parse(JSON.stringify(TASK_AU)))});
    const {page, errors, writes} = o;
    await page.evaluate(types => { TASK_TYPES = types; }, TYPES);
    await page.locator('.au-trig[data-open="trigger"]').click();
    await page.waitForSelector('.au-ed:not([hidden]) select[data-b="stepPick"]');
    // Nothing chosen: Save names the step and writes nothing.
    await page.locator('.au-save').click();
    await page.waitForSelector('.toast:has-text("Choose a step")');
    assert.equal(writes.filter(w => w.method === 'PATCH').length, 0, 'no save before a step is chosen');
    // A step chosen: one step filter on it is saved with the trigger.
    await page.selectOption('select[data-b="stepPick"]', 'st_qa');
    await page.locator('.au-save').click();
    await page.waitForFunction(() => document.querySelector('.au-ed')?.hidden !== false || !document.querySelector('.au-ed'));
    const patch = writes.filter(w => w.method === 'PATCH').at(-1);
    assert.ok(patch, 'saved once a step is chosen');
    assert.deepEqual(patch.body.trigger.filters, [{field: 'event.data.step', op: 'is', value: 'st_qa'}]);
    assert.deepEqual(errors, [], 'no page errors');
    console.log('automations task triggers ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
