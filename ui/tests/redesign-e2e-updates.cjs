// Updates on a real hub: the Daily and Weekly trees, below Dana (the owner) and the whole org, have no row for the
// built-ins behind the Assistant (BotOps, the Strategy Bot, the Librarian) or the Task Manager (backend/updates.py
// off_tree), once the hub has them; the people and bots on the chart still show. Phone, then desktop.
const assert = require('node:assert/strict');
const {run, open, ok, sideways, shot, t} = require('./support/redesign-e2e.cjs');

const HELPERS = ['botops', 'goal-manager', 'librarian', 'task-manager'];
const keys = nodes => nodes.flatMap(n => [n.key, ...keys(n.children)]);

run('redesign-e2e-updates', ['org', 'toured'], async ({hub, browser, phone, tag}) => {
  const page = await open(browser, hub, 'dana', {phone, hash: '#/updates'});
  // The scenario org has none of them yet: Dana adds them as an install has them, reporting to her.
  for (const [slug, name] of [['botops', 'BotOps'], ['goal-manager', 'Strategy Bot'], ['librarian', 'Librarian'], ['task-manager', 'Task Manager']])
    await ok(page, 'POST', 'bots', {slug, display_name: name, description: 'A built-in.', reports_to: 'human:dana', status: 'active',
      repo: 'emp-' + slug, thread_mode: 'personal', model: 'hermes-profile', effort: 'as-configured', harness: 'hermes',
      owner_id: 'dana', owners: ['dana'], runner_id: null});
  const bots = (await ok(page, 'GET', '../employees')).map(e => e.name);
  assert.ok(HELPERS.every(slug => bots.includes(slug)), `${tag}: the hub has its helpers: ${bots}`);
  await page.reload();
  for (const q of ['', '?scope=all', '?kind=weekly&scope=all']) {
    const tree = keys((await ok(page, 'GET', 'updates/tree' + q)).roots);
    assert.ok(tree.length > 1, `${tag} ${q}: the tree has teammates`);
    for (const slug of HELPERS) assert.ok(!tree.includes('b:' + slug), `${tag} ${q}: no ${slug} in ${tree}`);
  }
  // On the page: every report shown, and still no row for a helper.
  await page.locator('[data-upd-node], .upd-card').first().waitFor({timeout: t(15000)});
  for (let i = 0; i < 6; i++) {
    const closed = page.locator('[data-upd-kids][aria-expanded="false"]');
    if (!await closed.count()) break;
    await closed.first().click();
  }
  const names = (await page.locator('#main .upd-name').allInnerTexts()).map(s => s.trim());
  for (const name of ['BotOps', 'Strategy Bot', 'Librarian', 'Task Manager'])
    assert.ok(!names.includes(name), `${tag}: no ${name} row: ${names}`);
  assert.equal(await sideways(page), false);
  await shot(page, `updates-${tag}`);
  assert.deepEqual(page.errors, [], `${tag}: no page errors`);
  await page.context().close();
});
