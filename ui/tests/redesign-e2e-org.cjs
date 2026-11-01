// Journey 7 on a real hub (redesign TESTING.md §L3): Strategy › Org on a phone (then a desktop). Dana, the top of the
// org, taps Builder (Claude) and moves it from Engineering to Release: the hub's groups have it there at once. Omar
// leads Engineering only, so his Move to… offers Engineering's groups and not Release, and a Release teammate's sheet
// has no Move to…. No approval is filed: org changes beyond that are the Strategy Bot's to suggest in words.
const assert = require('node:assert/strict');
const {run, open, ok, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const card = ref => `.rdo-card[data-rdo-ref="${ref}"]`;
const groupsOf = async page => { const g = await ok(page, 'GET', 'groups'); return g.groups || g; };
const groupNamed = (groups, name) => groups.find(g => g.name === name);
const holds = (group, slug) => JSON.stringify(group).includes(`"${slug}"`) || JSON.stringify(group).includes(`bot:${slug}"`);

run('redesign-e2e-org', ['org', 'toured', 'groups'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/strategy/org'});
  await page.locator(card('bot:builder-claude')).waitFor({timeout: t(15000)});
  let groups = await groupsOf(page);
  const eng = groupNamed(groups, 'Engineering'), release = groupNamed(groups, 'Release');
  assert.ok(eng && release, `${tag}: both groups: ${JSON.stringify(groups).slice(0, 300)}`);
  assert.ok(holds(eng, 'builder-claude'));
  assert.equal(await page.locator(`[data-rdo-group="${eng.id}"] ${card('bot:builder-claude')}`).count(), 1);
  assert.equal(await sideways(page), false, `${tag}: the chart pans inside its pane`);
  await shot(page, `${tag}-01-chart`);

  // Dana moves Builder (Claude) at once.
  await page.locator(card('bot:builder-claude')).click();
  const sheet = page.locator('dialog.rd-sheet[open]');
  await sheet.waitFor();
  assert.equal(await sheet.locator('[data-rdo-home]').innerText(), 'Engineering');
  await sheet.locator('select[name="to"]').selectOption({label: 'Release'});
  assert.equal(await sheet.locator('[type=submit]').innerText(), 'Move', `${tag}: the top of the org moves`);
  await shot(page, `${tag}-02-move-sheet`);
  await sheet.locator('[type=submit]').click();
  await sheet.waitFor({state: 'detached'});
  await page.locator(`[data-rdo-group="${release.id}"] ${card('bot:builder-claude')}`).waitFor({timeout: t(10000)});
  groups = await groupsOf(page);
  assert.ok(holds(groupNamed(groups, 'Release'), 'builder-claude'), `${tag}: the hub has it in Release`);
  assert.ok(!holds(groupNamed(groups, 'Engineering'), 'builder-claude'), `${tag}: and out of Engineering`);
  await shot(page, `${tag}-03-moved`);

  // Omar (lead of Engineering, not Release): Move to… offers only what he leads; Release's teammates have none.
  const omar = await open(browser, hub, 'omar', {phone, hash: '#/strategy/org'});
  await omar.locator(card('bot:builder-openai')).click();
  const mine = omar.locator('dialog.rd-sheet[open]');
  await mine.waitFor();
  const options = (await mine.locator('select[name="to"] option').allInnerTexts()).map(o => o.trim());
  assert.ok(options.includes('Engineering') && !options.includes('Release') && !options.includes('No group'),
    `${tag}: a lead moves inside its groups only: ${options}`);
  assert.equal(await mine.locator('[type=submit]').innerText(), 'Move');
  await shot(omar, `${tag}-04-lead-sheet`);
  await mine.locator('[data-close]').first().click();
  await omar.locator(card('bot:builder-claude')).click();
  const theirs = omar.locator('dialog.rd-sheet[open]');
  await theirs.waitFor();
  assert.equal(await theirs.locator('select[name="to"], [type=submit]').count(), 0, `${tag}: no Move to… outside his groups`);
  await theirs.locator('[data-close]').first().click();
  groups = await groupsOf(omar);
  assert.ok(holds(groupNamed(groups, 'Engineering'), 'builder-openai'), `${tag}: nothing moved`);
  const needs = await ok(page, 'GET', 'needs-you');
  assert.equal((needs.items || needs.needs || needs).filter(it => it.kind === 'approval' && ['org', 'group'].includes(it.what)).length, 0,
    `${tag}: no org approval for Dana`);
  assert.deepEqual(omar.errors, [], `${tag}: no page errors for Omar`);
  await omar.context().close();
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
