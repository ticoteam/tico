// Journey 6 on a real hub (redesign TESTING.md §L3): who sees whose automations. Priya, who reports to Dana, sees her
// own "Standup notes" and none of Dana's; Dana's address opens nothing for her and the hub refuses its read. Dana,
// above Priya, sees Priya's among her own. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, go, api, ok, automationNamed, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const listed = page => page.locator('.au-row').allInnerTexts();

run('redesign-e2e-visibility', ['org', 'toured', 'automations'], async ({hub, browser, phone, tag}) => {
  const dana = await open(browser, hub, 'dana', {phone, hash: '#/automations'});
  await dana.locator('.au-row').first().waitFor({timeout: t(15000)});
  const triage = await automationNamed(dana, 'Inbox triage');
  assert.ok((await listed(dana)).some(x => x.includes('Standup notes')), `${tag}: Dana sees Priya's`);
  assert.ok(await automationNamed(dana, 'Standup notes'), 'and the hub lists it for her');
  await shot(dana, `${tag}-01-dana`);
  await dana.context().close();

  const priya = await open(browser, hub, 'priya', {phone, hash: '#/automations'});
  await priya.locator('.au-row').first().waitFor({timeout: t(15000)});
  const rows = await listed(priya);
  assert.ok(rows.some(x => x.includes('Standup notes')), `${tag}: her own`);
  for (const name of ['Inbox triage', 'Route mail', 'Weekly digest'])
    assert.ok(!rows.some(x => x.includes(name)), `${tag}: ${name} is absent for Priya`);
  const names = (await ok(priya, 'GET', 'automations')).automations.map(a => a.name);
  assert.ok(!names.includes('Inbox triage'), `${tag}: the hub leaves it out too: ${names}`);
  const read = await api(priya, 'GET', `automations/${triage.id}`);
  assert.ok([403, 404].includes(read.status), `${tag}: the hub refuses the read: ${read.status}`);
  assert.equal(await sideways(priya), false);
  await shot(priya, `${tag}-02-priya`);

  // Dana's address opens nothing of it for Priya.
  await go(priya, `#/automations/${triage.id}`);
  await priya.locator('.au-pane:has-text("Not shared with you")').waitFor({timeout: t(5000)});
  assert.equal(await priya.locator('.au-pane .err').count(), 0, `${tag}: a short line, not the hub's refusal`);
  assert.equal(await priya.locator('.au-pane .au-card[data-open="sort"]').count(), 0, `${tag}: no flow shown`);
  assert.notEqual(await priya.locator('.au-hd .au-name').innerText().catch(() => ''), 'Inbox triage');
  await shot(priya, `${tag}-03-priya-direct`);
  assert.deepEqual(priya.errors, [], `${tag}: no page errors`);
  await priya.context().close();
});
