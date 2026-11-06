// The Assistant's daily sweep on a real hub (backend/redesign_migration.py adopt, backend/automations.py may_change): Dana,
// the owner, lists it with the Assistant as its owner and its schedule, opens it, sees its switch on and runs it with
// Test run (the options menu), and the run lands in its Runs. Omar, who is not above the Assistant, does not list it and
// is refused it by id. Phone, then desktop. TICO_UI_SHOTS=<dir> saves the list and the open routine at each width.
const assert = require('node:assert/strict');
const {run, open, api, ok, shot, t} = require('./support/redesign-e2e.cjs');

const ASSISTANT = 'bot:coo';

run('automations-assistant-routine', ['org', 'toured', 'assistant'], async ({hub, browser, phone, tag}) => {
  // The sweep's title comes from the catalog (a template refresh renames it), so the test finds it by its owner.
  const dana = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  await dana.locator('tr.au-row').first().waitFor({timeout: t(10000)});
  const sweeps = (await ok(dana, 'GET', 'automations?limit=500')).automations.filter(a => a.owner_id === ASSISTANT);
  assert.equal(sweeps.length, 1, `${tag}: the Assistant has one routine on Automations`);
  const sweep = sweeps[0];
  assert.equal(sweep.trigger.type, 'schedule', `${tag}: a schedule`);

  // The list row: its title, its schedule words, on (not greyed out).
  const row = dana.locator(`tr.au-row[data-row="${sweep.id}"]`);
  await row.waitFor({timeout: t(10000)});
  assert.equal(await row.locator('.au-nm').innerText(), sweep.name, `${tag}: its title on the row`);
  assert.match(await row.locator('td.au-tt').innerText(), /\b8\b|08:00|8:00/, `${tag}: its time on the row`);
  assert.doesNotMatch(await row.getAttribute('class'), /\boff\b/, `${tag}: on`);
  await shot(dana, `${tag}-assistant-routine-list`);

  // Opened: the Assistant's name over it, the switch on, the spend limit and the options menu (owner's controls).
  await row.locator('a').first().click();
  await dana.locator('.au-hd .au-name').filter({hasText: sweep.name}).waitFor({timeout: t(10000)});
  assert.equal(await dana.locator('.au-hd .au-sw').isChecked(), true, `${tag}: the switch is on`);
  assert.equal(await dana.locator('.au-hd [data-spend]').count(), 1, `${tag}: the spend limit is editable`);
  assert.equal(await dana.locator('.au-hd [data-more]').count(), 1, `${tag}: the options menu`);
  await shot(dana, `${tag}-assistant-routine-open`);

  // Test run from the options menu: the editor's Run makes a run, and the routine's Runs list has it.
  await dana.locator('.au-hd [data-more]').click();
  await dana.locator('.au-menu [data-mi="run"]').click();
  await dana.locator('.au-ed .au-save').click();
  const end = Date.now() + t(20000);
  let runs = [];
  while (Date.now() < end) {
    runs = (await ok(dana, 'GET', `automations/${sweep.id}/runs?limit=25`)).runs;
    if (runs.length) break;
    await dana.waitForTimeout(200);
  }
  assert.ok(runs.length >= 1, `${tag}: Test run made a run`);
  assert.deepEqual(dana.errors, [], `${tag}: no page errors`);

  // Omar is a peer of the Engineering Lead, not above the Assistant: no row, and the routine is refused by id.
  const omar = await open(browser, hub, 'omar', {phone, hash: '#/automations'});
  await omar.waitForTimeout(t(1000));
  assert.equal(await omar.locator(`tr.au-row[data-row="${sweep.id}"]`).count(), 0, `${tag}: Omar does not list it`);
  assert.ok(!(await ok(omar, 'GET', 'automations?limit=500')).automations.some(a => a.id === sweep.id), `${tag}: nor the API`);
  assert.equal((await api(omar, 'GET', `automations/${sweep.id}`)).status, 403, `${tag}: refused by id`);
  assert.deepEqual(omar.errors, [], `${tag}: no page errors for Omar`);
});
