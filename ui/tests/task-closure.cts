import type * as Tasks from './tasks-page.cts';
// Done and Close in a task window's header (docs/pipelines-v3.md S5): Done is the owner's, Close is for whoever may
// close it; each is one tap, asks no reason, and the window closes. Done sends POST /tasks/{id}/done with the version,
// Close moves the task to closed and says "Closed" with Undo, which reopens it. A finished task offers neither. The
// header fits at desktop and phone widths.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {open, fixtures} = require('./tasks-page.cts') as Tasks.Api;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}, {width: 375, height: 667}]) {
      const tasks = fixtures();
      const show = async (id: string) => {
        const v = await open(browser, {viewport, hash: '#/task/' + id, tasks});
        const head = v.page.locator('#task-modal .tmodal-head');
        // Nothing in the header is cut off or pushed past the screen, and the header does not scroll sideways.
        const fits = await head.evaluate(h => h.scrollWidth <= h.clientWidth + 1
          && [...h.children].every(c => { const r = c.getBoundingClientRect(); return r.width === 0 || (r.left >= 0 && r.right <= innerWidth + 0.5); }));
        assert.ok(fits, `the header fits ${viewport.width}px`);
        return {...v, head};
      };
      const finish = async (v: Awaited<ReturnType<typeof show>>, kind: string) => {
        await v.head.locator(`[data-task-finish="${kind}"]`).click();
        await v.page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#task-modal')!.open);
        assert.equal(await v.page.locator('dialog.task-outcome').count(), 0, 'no reason is asked');
      };

      // A bot owns it and Ana asked: Close, no Done. Undo on the toast reopens it.
      const bot = await show('t-4');
      assert.equal(await bot.head.locator('[data-task-finish="done"]').count(), 0, "Done is the owner's");
      await finish(bot, 'close');
      assert.deepEqual(bot.posts, [{p: '/api/v2/tasks/t-4/move', body: {to: 'closed', version: 3}}]);
      assert.equal(tasks.find(t => t.id === 't-4')!.status, 'closed');
      await bot.page.locator('.toast', {hasText: 'Closed'}).getByRole('button', {name: 'Undo'}).click();
      await bot.page.locator('.toast', {hasText: 'Closed'}).waitFor({state: 'detached'});
      assert.deepEqual(bot.posts[1], {p: '/api/v2/tasks/t-4/move', body: {to: 'reopen', version: 4}});
      assert.deepEqual(bot.errors, []);
      await bot.page.close();

      // Ana owns it: Done, one tap, with the version shown.
      const own = await show('t-3');
      await finish(own, 'done');
      assert.deepEqual(own.posts, [{p: '/api/v2/tasks/t-3/done', body: {version: 3}}]);
      assert.deepEqual(own.errors, []);
      await own.page.close();

      // A finished task offers neither.
      const done = await show('t-6');
      assert.equal(await done.head.locator('[data-task-finish]').count(), 0);
      assert.deepEqual(done.errors, []);
      await done.page.close();
    }
    console.log('Done and Close: the owner\'s Done, Close with Undo, nothing on a finished task, the header fits: ok');
  } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
