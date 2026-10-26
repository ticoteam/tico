// Explicit acceptance works from Done without reopening or waking the task.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors, posts, tasks} = await open(browser, {hash: '#/tasks?view=done'});
    await page.locator('#task-view [data-view="done"]').click();
    await page.locator('[data-task-key="tt-receipt"] .tl-title').click();
    const peek = page.locator('#task-peek');
    await peek.locator('[data-task-more]').click();
    assert.equal(await peek.getByRole('menuitem', {name: 'Close task', exact: true}).count(), 1);
    await peek.getByRole('menuitem', {name: 'Open full', exact: true}).click();
    const modal = page.locator('#task-modal');
    await modal.locator('[data-task-more]').click();
    await modal.getByRole('menuitem', {name: 'Close task', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('#task-modal [data-prop="status"]')?.textContent.includes('Closed'));
    assert.equal(tasks.find(t => t.id === 't-receipt').status, 'closed');
    assert.deepEqual(posts.filter(p => p.p === '/api/v2/tasks/t-receipt').map(p => p.body), [{version: 3, close: true}]);
    await modal.locator('[data-task-more]').click();
    assert.equal(await modal.getByRole('menuitem', {name: 'Close task', exact: true}).count(), 0);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Escape');
    // Bulk accepts Done; closed work is under Closed, not Done.
    assert.equal(await page.locator('#task-body [data-task-key="tt-faq"]').count(), 0);
    await page.locator('[data-task-key="tt-sso"] .tl-check').click();
    await page.locator('#task-bulk [data-bulk="close"]').click();
    await page.waitForFunction(() => /closed/.test(document.querySelector('.tl-bulk-msg')?.textContent || ''));
    assert.deepEqual(posts.filter(p => /^\/api\/v2\/tasks\/t-(sso|faq)$/.test(p.p)), [{p: '/api/v2/tasks/t-sso', body: {version: 3, close: true}}]);
    assert.deepEqual(errors, []);
    await page.close();
    const viewer = await open(browser, {hash: '#/tasks?view=done', mover: false});
    await viewer.page.locator('#task-view [data-view="done"]').click();
    await viewer.page.locator('[data-task-key="tt-receipt"] .tl-title').click();
    await viewer.page.locator('#task-peek [data-task-more]').click();
    assert.equal(await viewer.page.getByRole('menuitem', {name: 'Close task', exact: true}).count(), 1, 'a requester can accept without mover rights');
    await viewer.page.close();
    // The header's Done (the owner) and Close (as the "…" menu allows): one tap, no reason asked, and the window closes.
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
      const v = await open(browser, {viewport, hash: '#/tasks'});
      const head = v.page.locator('#task-modal .tmodal-head');
      const show = async id => { await v.page.evaluate(id => taskModalShow(TASKS_ST.tasks.find(t => t.id === id)), id); await head.waitFor(); };
      const finish = async (id, kind) => {
        await show(id);
        const fits = await head.evaluate(h => [...h.querySelectorAll('button')].every(b => b.getBoundingClientRect().right <= innerWidth));
        assert.ok(fits, `the header fits ${viewport.width}px`);
        await head.locator(`[data-task-finish="${kind}"]`).click();
        await v.page.waitForFunction(() => !document.querySelector('#task-modal').open);
        assert.equal(await v.page.locator('dialog.task-outcome').count(), 0, 'no reason is asked');
        return v.posts.filter(p => p.p === `/api/v2/tasks/${id}`).at(-1).body;
      };
      await show('t-news');                       // a bot owns it, Ana asked: Close, no Done
      assert.equal(await head.locator('[data-task-finish="done"]').count(), 0, 'Done is the owner\'s');
      assert.deepEqual(await finish('t-news', 'close'), {version: 3, close: true});
      assert.deepEqual(await finish('t-deck', 'done'), {version: 3, status: 'done'});
      assert.deepEqual(await finish('t-access', 'close'), {version: 3, close: true}, 'a bot\'s request closes without a note');
      // Done on a bot's request is one tap too: nothing asks what was decided, and no note goes when none was typed.
      assert.deepEqual(await finish('t-copy', 'done'), {version: 3, status: 'done'});
      assert.deepEqual(v.errors, []);
      await v.page.close();
    }
    // With open subtasks, finishing asks what becomes of them: closed with it (the server's default) or standalone.
    // Backing out saves nothing.
    const s = await open(browser, {hash: '#/tasks'});
    const head = s.page.locator('#task-modal .tmodal-head'), choice = s.page.locator('dialog.task-children-choice');
    const sent = id => s.posts.filter(p => p.p === `/api/v2/tasks/${id}`).map(p => p.body);
    const pick = async (id, kind, button) => {
      s.tasks.find(t => t.id === id).children_summary = {total: 2, open: 2, done: 0, direct_total: 2, direct_done: 0};
      await s.page.evaluate(id => taskModalShow({...TASKS_ST.tasks.find(t => t.id === id),
        children_summary: {total: 2, open: 2, done: 0, direct_total: 2, direct_done: 0}}), id);
      await head.locator(`[data-task-finish="${kind}"]`).click();
      await choice.waitFor();
      assert.match(await choice.textContent(), /2 open subtasks/);
      await choice.getByRole('button', {name: button, exact: true}).click();
      await choice.waitFor({state: 'detached'});
    };
    await pick('t-news', 'close', 'Cancel');
    assert.deepEqual(sent('t-news'), [], 'backing out saves nothing');
    assert.ok(await s.page.evaluate(() => document.querySelector('#task-modal').open), 'the task stays open to finish later');
    await pick('t-news', 'close', 'Keep as standalone');
    await s.page.waitForFunction(() => !document.querySelector('#task-modal').open);
    assert.deepEqual(sent('t-news'), [{version: 3, close: true, children: 'detach'}]);
    await pick('t-deck', 'done', 'Close them too');
    await s.page.waitForFunction(() => !document.querySelector('#task-modal').open);
    assert.deepEqual(sent('t-deck'), [{version: 3, status: 'done'}], 'closing them with it is the default');
    assert.deepEqual(s.errors, []);
    await s.page.close();
    console.log('Done explicit closure: detail, peek, bulk, requester rights, header Done/Close and open subtasks passed');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
