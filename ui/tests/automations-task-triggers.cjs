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
      tweak: fx => { fx.automations.push(JSON.parse(JSON.stringify(TASK_AU))); fx.task_types = TYPES; }});
    const {page, errors, writes} = o;
    await page.locator('.au-trig[data-open="trigger"]').click();
    await page.waitForSelector('.au-ed:not([hidden]) select[data-b="stepPick"]');
    assert.deepEqual(await page.locator('.au-ed select[data-b="evpick"] option').evaluateAll(options => options.map(option => option.value)),
      ['task.created', 'task.step_changed', 'task.done', 'task.declined', 'task.closed'], 'the Trigger choices are the five task Triggers');
    assert.equal(await page.locator('.au-ed label').filter({hasText: 'Trigger'}).count(), 1);
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

// Criteria 3 and 4 (Dev 666): a failed last run shows its error and its time on the row; a paused task-event automation
// reads "Paused: too many runs". A built-in row is not given the error line.
(async () => {
  const browser = await chromium.launch();
  try {
    const failed = {...JSON.parse(JSON.stringify(TASK_AU)), id: 'au_failed', name: 'Failed step',
      enabled: true, last_run: {id: 'run_f', status: 'failed', started_at: '2026-11-03T10:00:00Z', finished_at: '2026-11-03T10:00:05Z',
        error: 'The note could not be saved'}};
    const paused = {...JSON.parse(JSON.stringify(TASK_AU)), id: 'au_paused', name: 'Paused one', enabled: false,
      paused: true, paused_reason: 'Paused: too many runs'};
    // Cold open (Dev 666): the page loads the task types itself, so the step choices show with no Tasks page visited first.
    const cold = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_task',
      tweak: fx => { fx.automations.push(JSON.parse(JSON.stringify(TASK_AU))); fx.task_types = TYPES; }});
    await cold.page.locator('.au-trig[data-open="trigger"]').click();
    await cold.page.waitForFunction(() => document.querySelectorAll('select[data-b="stepPick"] option').length > 1);
    const coldSteps = await cold.page.locator('select[data-b="stepPick"] option').allInnerTexts();
    assert.deepEqual(coldSteps.filter(x => x && x !== 'Choose a step'), ['Review 1', 'In QA'], 'step choices on a cold open');
    await cold.page.close();
    // Switching the event away from "enters a step" drops the step filter: the saved trigger does not keep it (Dev 666).
    const stale = {...JSON.parse(JSON.stringify(TASK_AU)), id: 'au_stale', name: 'Stale step',
      trigger: {type: 'event', name: 'task.step_changed', filters: [{field: 'event.data.step', op: 'is', value: 'st_qa'}]}};
    const sw = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_stale',
      tweak: fx => { fx.automations.push(stale); fx.task_types = TYPES; }});
    await sw.page.locator('.au-trig[data-open="trigger"]').click();
    await sw.page.waitForSelector('.au-ed:not([hidden]) select[data-b="evpick"]');
    await sw.page.selectOption('.au-ed select[data-b="evpick"]', 'task.done');
    await sw.page.locator('.au-save').click();
    await sw.page.waitForFunction(() => !document.querySelector('.au-ed') || document.querySelector('.au-ed').hidden !== false);
    const swPatch = sw.writes.filter(w => w.method === 'PATCH' && w.body?.trigger).at(-1);
    assert.ok(swPatch, 'the switched trigger is saved');
    assert.equal(swPatch.body.trigger.name, 'task.done');
    assert.deepEqual(swPatch.body.trigger.filters, [], 'no step filter left on a done trigger');
    await sw.page.close();
    const o = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations',
      tweak: fx => fx.automations.push(failed, paused)});
    const row = o.page.locator('.au-tn', {hasText: 'Failed step'});
    await row.waitFor();
    assert.match(await o.page.locator('.au-tn', {hasText: 'Failed step'}).innerText(), /Last run failed.*could not be saved/);
    assert.match(await o.page.locator('.au-tn', {hasText: 'Paused one'}).innerText(), /Paused: too many runs/);
    assert.deepEqual(o.errors, [], 'no page errors');
    console.log('automations task rows ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
