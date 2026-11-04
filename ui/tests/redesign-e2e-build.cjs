// Journey 3 on a real hub (redesign TESTING.md §L3): + New, On a schedule makes a blank automation at once (Ask has the
// Assistant build one instead): the hub has an off "New automation" of Dana's, weekdays at 9, no steps, open in the editor. Each "+" adds a step in a
// sheet (Filter, then Decide, then an LLM prompt); each save is a version on the hub; History lists them and restoring
// v1 brings back the empty flow as a new version. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');

// "+" at the end of the flow, a step kind from its menu, the sheet open on it.
async function add(page, type) {
  await page.locator('.au-pane .au-add').last().click();
  await page.locator(`.au-menu [data-ty="${type}"]`).click();
  await page.locator('.au-ed:not([hidden]) .au-save').waitFor();
}
async function save(page, version) {
  await page.locator('.au-ed .au-save').click();
  await page.waitForFunction(v => document.querySelector('.au-ver')?.textContent === v
    && document.querySelector('.au-ed').hidden, 'v' + version, {timeout: t(10000)});
}

run('redesign-e2e-build', ['org', 'toured', 'automations'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  assert.equal(await page.locator('.au-ph [data-ask-open]').getAttribute('aria-label'), 'Ask', `${tag}: Ask beside + New`);
  await page.locator('.au-new').click();
  await page.locator('.au-newm [data-new="schedule"]').click();
  await page.locator('.au-hd .au-name:text("New automation")').waitFor({timeout: t(10000)});
  await page.locator('.au-card[data-open="trigger"]').waitFor();
  assert.equal(await page.locator('.au-pane .au-card[data-open]:not([data-open="trigger"])').count(), 0, 'no steps yet');
  assert.equal(await page.locator('.au-hd .au-sw').isChecked(), false, `${tag}: a new one starts off`);
  const made = await automationNamed(page, 'New automation');
  assert.ok(made, `${tag}: the hub has it`);
  assert.equal(made.version, 1);
  assert.equal(made.enabled, false);
  assert.equal(made.owner_id, 'person:dana');
  assert.equal(made.trigger.type, 'schedule');
  assert.equal(made.trigger.cron, '0 9 * * 1-5');
  assert.deepEqual(made.steps, []);
  await shot(page, `${tag}-00-blank`);

  // Filter: a rule on the subject, archive.
  await add(page, 'filter');
  await page.fill('[data-b="step.rules.0.when.0.field"]', 'item.title');
  await page.fill('[data-b="step.rules.0.when.0.value"]', 'draft');
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-01-filter-sheet`);
  await save(page, 2);

  // Decide: what to ask is required; one yes/no question.
  await add(page, 'decide');
  await page.fill('[data-b="qs.0.id"]', 'worth_it');
  await page.locator('.au-ed .au-save').click();
  await page.locator('.toast:has-text("needs what to ask")').waitFor();
  await page.fill('[data-b="qs.0.ask"]', 'Is there enough news for a digest?');
  await shot(page, `${tag}-02-decide-sheet`);
  await save(page, 3);

  // LLM: Dana's Assistant writes it.
  await add(page, 'llm');
  await page.fill('[data-b="step.prompt"]', 'Write this week’s digest in five bullets.');
  await shot(page, `${tag}-03-llm-sheet`);
  await save(page, 4);
  assert.equal(await sideways(page), false);
  await shot(page, `${tag}-04-flow`);

  const built = (await ok(page, 'GET', `automations/${made.id}`));
  const flow = built.automation || built;
  assert.deepEqual(flow.steps.map(s => s.type), ['filter', 'decide', 'llm'], `${tag}: the hub has the three steps`);
  assert.deepEqual(flow.steps[0].rules[0].when, [{field: 'item.title', op: 'contains', value: 'draft'}]);
  assert.deepEqual(flow.steps[0].rules[0].then, [{do: 'archive', args: {}}]);
  assert.deepEqual(flow.steps[1].questions, {worth_it: {type: 'noul', instructions: 'Is there enough news for a digest?'}});
  assert.equal(flow.steps[2].prompt, 'Write this week’s digest in five bullets.');
  assert.match(await page.locator('.au-pane .au-card').allInnerTexts().then(x => x.join('\n')), /worth_it[\s\S]*five bullets/);

  // History: v1..v4, v4 current; restore v1 makes v5 with no steps.
  await page.locator('.au-ver').click();
  await page.locator('.au-vr').first().waitFor();
  const rows = await page.locator('.au-vr strong').allInnerTexts();
  assert.deepEqual(rows, ['v4', 'v3', 'v2', 'v1']);
  assert.match(await page.locator('.au-vr').first().innerText(), /Current/);
  for (const h of await page.locator('.au-vr').evaluateAll(els => els.map(e => e.getBoundingClientRect().height)))
    assert.ok(h < 52, `${tag}: a version is one row (${h}px)`);
  await shot(page, `${tag}-05-history`);
  await page.locator('.au-vr:has(strong:text-is("v1")) [data-op="restore"]').click();
  await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v5', null, {timeout: t(10000)});
  await page.locator('.au-vr strong:text-is("v5")').waitFor();
  await shot(page, `${tag}-06-restored`);
  const after = await automationNamed(page, 'New automation');
  assert.equal(after.version, 5);
  assert.deepEqual(after.steps, [], `${tag}: v1's empty flow is back`);
  await page.locator('.au-ed [data-op="close"]').click();
  assert.equal(await page.locator('.au-pane .au-card[data-open]:not([data-open="trigger"])').count(), 0);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
