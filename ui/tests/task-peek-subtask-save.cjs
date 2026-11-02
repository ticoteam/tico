// A subtask opened from its parent in the peek stays open after a save takes it out of the filtered list: the peek
// never moves on to its neighbour (a declined sibling), so the next edit lands on the task that was saved (Dev 283).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const at = mins => new Date(Date.now() - mins * 60000).toISOString();
const t = (id, title, over = {}) => ({id, title, body: 'Details.', owner: 'bot:reviewer', requester: 'bot:lead', status: 'doing',
  lane: 'company', rank: null, labels: [], tags: [], links: [], parts: {total: 0, done: 0}, version: 3,
  created_at: at(600), updated_at: at(30), ...over});
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const baseTasks = [
      t('t-dev', 'Dev 99: fix the checkout total', {children_summary: {total: 2, done: 1, direct_total: 2, direct_done: 1}, parts: {total: 2, done: 1}}),
      t('t-r1', 'Review 1: PR #12', {_parent: 't-dev', status: 'review', updated_at: at(20)}),
      t('t-dup', 'Review 1: PR #12 (duplicate)', {_parent: 't-dev', status: 'declined', updated_at: at(25)}),
      t('t-other', 'Someone else\'s task', {owner: 'bot:engineer'}),
    ];
    const {page, errors, posts} = await open(browser, {hash: '#/tasks?owner=bot:reviewer&view=list', baseTasks});
    const peek = page.locator('#task-peek');
    const title = name => page.waitForFunction(name => document.querySelector('#task-peek .tmodal-title')?.textContent === name, name);
    const writes = () => posts.filter(x => /^\/api\/v2\/tasks\/t-/.test(x.p)).map(x => [x.p, x.body]);
    assert.equal(await page.locator('[data-task-key="tt-other"]').count(), 0, 'the list is filtered');
    assert.equal(await page.locator('[data-task-key="tt-dup"]').count(), 1, 'the declined sibling is a row of the list');
    // The parent from the list, then its subtask from the peek's Subtasks.
    await page.locator('[data-task-key="tt-dev"] .tl-title').click();
    await title('Dev 99: fix the checkout total');
    await peek.locator('[data-sub-open="t-r1"]').click();
    await title('Review 1: PR #12');
    await page.waitForFunction(() => TASKS_ST.peek === 'tt-r1');
    // Done takes it out of the list; once the reload has drawn, the peek is still on it.
    await peek.locator('[data-prop="status"]').click();
    await peek.locator('.prop-pop [data-prop-pick="done"]').click();
    await page.waitForFunction(() => !document.querySelector('[data-task-key="tt-r1"]') && !TASKS_ST.pendingFinish);
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => document.querySelector('#task-peek').dataset.task), 't-r1', 'the peek keeps the saved task');
    assert.equal(await page.evaluate(() => TASKS_ST.peek), 'tt-r1');
    await title('Review 1: PR #12');
    // The next edit goes to the same task, never to the declined sibling.
    await peek.locator('[data-prop="status"]').click();
    await peek.locator('.prop-pop [data-prop-pick="doing"]').click();
    await page.waitForFunction(() => document.querySelector('[data-task-key="tt-r1"]'));
    assert.deepEqual(writes(), [['/api/v2/tasks/t-r1', {version: 3, status: 'done'}], ['/api/v2/tasks/t-r1', {version: 4, status: 'doing'}]]);
    // Opened from its own row instead, Done still moves the list and the peek on to the next row.
    await page.locator('[data-task-key="tt-r1"] .tl-title').click();
    await page.waitForFunction(() => TASKS_ST.peek === 'tt-r1' && TASKS_ST.peekVia === 'list');
    await title('Review 1: PR #12');
    await peek.locator('[data-prop="status"]').click();
    await peek.locator('.prop-pop [data-prop-pick="done"]').click();
    await page.waitForFunction(() => TASKS_ST.peek && TASKS_ST.peek !== 'tt-r1');
    assert.deepEqual(writes().at(-1), ['/api/v2/tasks/t-r1', {version: 5, status: 'done'}]);
    assert.deepEqual(errors, []);
    console.log('PASS: a subtask opened from its parent keeps the peek after a save; a row opened from the list still moves on.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
