// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-review.cjs
// Review mode: ▶ opens the queue; Approve moves on with a 10-second Undo; ⌘/Ctrl+⏎ approves only while the note has
// focus; send-back needs a note; a refused approval (the work changed) reloads the queue; the phone layout stacks and
// does not scroll sideways. Desktop 1440 and phone 390, dark and light.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const SIZES = [{width: 1440, height: 940, phone: false}, {width: 390, height: 844, phone: true}];

async function openReview(browser, {width, height, theme, refuseApprove = false, touch = false}) {
  const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block', ...(touch ? {isMobile: true, hasTouch: true} : {})});
  await context.addInitScript(([theme]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  }, [theme]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = await install(page, {refuseApprove});
  await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
  await page.waitForSelector('.tl-head .pipe-play', {timeout: t(8000)});
  await page.locator('.tl-head .pipe-play').click();
  await page.waitForSelector('.pipe-review .pipe-review-count', {timeout: t(5000)});
  return {context, page, state, errors};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      for (const size of SIZES) {
        const {context, page, state, errors} = await openReview(browser, {...size, theme});
        const count = () => page.locator('.pipe-review-count').first().innerText();
        assert.equal((await count()).trim(), 'For you · 1 of 2');
        assert.equal(await page.locator('.pipe-review-tab').count(), 1, 'every attachment is a tab');
        assert.equal(await page.locator('.pipe-review-approve kbd, .pipe-review-kbd').count() > 0, true);
        const label = `${theme} ${size.width}`;

        // The two Message buttons share one row; Mark met is a quiet text button under Approve, outside that row.
        const toAssignee = await page.locator('[data-review-send="assignee"]').boundingBox();
        const toManager = await page.locator('[data-review-send="manager"]').boundingBox();
        assert.ok(Math.abs(toAssignee.y - toManager.y) < 2 && toAssignee.height <= 44, label + ': the Message buttons share one row');
        assert.equal(await page.locator('.pipe-review-sendback [data-review-send="met"]').count(), 0, label + ': Mark met is not in the row');
        const approveBox = await page.locator('.pipe-review-approve').boundingBox();
        const metBox = await page.locator('.pipe-review-met').boundingBox();
        assert.ok(metBox.y >= approveBox.y + approveBox.height - 1, label + ': Mark met sits under Approve');

        // Layout: no sideways scroll; on a phone the decision sits under the attachment, not beside it.
        const scroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
        assert.ok(scroll, 'no horizontal page scroll');
        const mainBox = await page.locator('.pipe-review-main').boundingBox();
        const sideBox = await page.locator('.pipe-review-side').boundingBox();
        if (size.phone) assert.ok(sideBox.y >= mainBox.y + mainBox.height - 2, 'the phone stacks the decision below');
        else assert.ok(sideBox.x >= mainBox.x + mainBox.width - 2, 'the desktop puts the decision beside');
        if (size.phone) {
          // A phone: a 44px round × to exit, ‹ › at 44px, and the stage under the count.
          const exit = await page.locator('.pipe-review-exit').boundingBox();
          assert.ok(exit.width === 44 && exit.height === 44, label + ': Exit is a 44px round button');
          for (const sel of ['[data-review-prev]', '[data-review-next]']) {
            const box = await page.locator(sel).boundingBox();
            assert.ok(box.width === 44 && box.height === 44, label + ': ' + sel + ' is 44px');
          }
          assert.equal((await page.locator('.pipe-review-stage').innerText()).trim(), 'Build', label + ': the stage sits under the count');
        }

        // ⌘⏎ does nothing while the note box does not have focus.
        await page.locator('.pipe-review-approve').focus();
        await page.keyboard.press('Meta+Enter');
        await page.keyboard.press('Control+Enter');
        assert.equal(state.posts.length, 0, 'no approval without the note focused');

        // Send back needs a note; with one, it goes to the assignee and the item leaves the queue.
        await page.locator('[data-review-send="assignee"]').click();
        assert.match(await page.locator('.pipe-review-err').innerText(), /Write a note first/);
        assert.equal(state.posts.length, 0);
        await page.locator('.pipe-review textarea[name=note]').fill('Shot 2 still shows the old card');
        await page.locator('[data-review-send="assignee"]').click();
        await page.waitForFunction(() => document.querySelector('.pipe-review-count')?.textContent.includes('For you · 1 of 1'), null, {timeout: t(5000)});
        assert.deepEqual(state.posts.map(p => p.path), ['tasks/D52/requirements/r-shots/send-back'], 'send-back posts to the item');
        assert.equal(state.posts[0].body.note, 'Shot 2 still shows the old card');

        // Approve the last item with ⌘⏎ from the note, then Undo within the toast.
        await page.locator('.pipe-review textarea[name=note]').fill('Looks right');
        await page.locator('.pipe-review textarea[name=note]').focus();
        await page.keyboard.press(process.platform === 'darwin' ? 'Meta+Enter' : 'Control+Enter');
        await page.waitForSelector('.pipe-review-toast', {timeout: t(5000)});
        assert.match(await page.locator('.pipe-review-toast').innerText(), /Approved\s*·\s*Undo/);
        assert.equal(state.posts.at(-1).path, 'tasks/D61/requirements/r-spec61/approve', 'approval posts to the item');
        await page.locator('[data-review-undo]').click();
        await page.waitForFunction(() => document.querySelector('.pipe-review-count')?.textContent.includes('For you · 1 of 1'), null, {timeout: t(5000)});
        assert.equal(state.posts.at(-1).path, 'decisions/dec-1/undo', 'undo posts the decision');
        assert.equal(await page.locator('.pipe-review-toast').count(), 0, 'the toast goes once undone');

        assert.deepEqual(errors, [], 'no page errors');
        await context.close();
      }
    }

    // A touch phone has no keyboard to hint at: Esc and ⌘⏎ are not drawn where no hover is possible.
    {
      const {context, page, errors} = await openReview(browser, {width: 390, height: 844, theme: 'light', touch: true});
      const drawn = await page.locator('.pipe-review kbd').evaluateAll(els => els.filter(el => el.getClientRects().length > 0).length);
      assert.equal(drawn, 0, 'no key hints on a touch phone');
      assert.deepEqual(errors, [], 'touch phone: no page errors');
      await context.close();
    }

    // A refused approval (the work changed under the reviewer) does not approve stale work: the queue reloads.
    const {context, page, state, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'dark', refuseApprove: true});
    const reads = state.queueReads;
    await page.locator('.pipe-review-approve').click();
    await page.waitForFunction(() => document.querySelector('.pipe-review-err')?.textContent.includes('This changed'), null, {timeout: t(5000)});
    assert.ok(state.queueReads > reads, 'the queue is read again after a refusal');
    assert.equal(state.posts.length, 1, 'one approval was sent and refused');
    assert.deepEqual(errors, [], 'no page errors');
    await context.close();
  } finally {
    await browser.close();
  }
  console.log('pipeline-review: ok');
})().catch(error => { console.error(error); process.exit(1); });
