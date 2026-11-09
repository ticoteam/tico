// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-review.cjs
// Review mode: ▶ opens the queue; Approve moves on with a 10-second Undo; ⌘/Ctrl+⏎ approves only while the note has
// focus; send-back needs a note; a refused approval (the work changed) reloads the queue; the phone layout stacks and
// does not scroll sideways. Desktop 1440 and phone 390, dark and light.
import type * as PipelineFixtureSupport from './support/pipeline-fixture.cts';
import type {Browser} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cts') as PipelineFixtureSupport.Api;
const {t} = (() => { try { return require('./support/load.cjs') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();

// What the fake server records for a post: the item's path, and the body the page sent.
type Post = {path: string; body: {note?: string; work_version?: number; requirement_version?: number; attachment_id?: string; stage_visit_id?: string}};

const SIZES = [{width: 1440, height: 940, phone: false}, {width: 390, height: 844, phone: true}];
// Waits for something the fake server records (a claim, a read) without a fixed sleep.
async function until(check: () => boolean, ms = 5000) {
  const end = Date.now() + ms;
  while (!check()) { if (Date.now() > end) throw new Error('timed out waiting for the server to record it'); await new Promise(r => setTimeout(r, 50)); }
}

async function openReview(browser: Browser, {width, height, theme, refuseApprove = false, touch = false, claimedBy = null, validate = false}: {width: number; height: number; theme: string; refuseApprove?: boolean; touch?: boolean; claimedBy?: string | null; validate?: boolean}) {
  const context = await browser.newContext({viewport: {width, height}, serviceWorkers: 'block', ...(touch ? {isMobile: true, hasTouch: true} : {})});
  await context.addInitScript(([theme]: [string]) => {
    try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
  }, [theme] as [string]);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  const state = await install(page, {refuseApprove, claimedBy, validate});
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
        const toAssignee = (await page.locator('[data-review-send="assignee"]').boundingBox())!;
        const toManager = (await page.locator('[data-review-send="manager"]').boundingBox())!;
        // A name that wraps (the manager's, at 390 px) makes the row two lines tall; the row stays one row.
        assert.ok(Math.abs(toAssignee.y - toManager.y) < 2 && toAssignee.height <= 52, label + ': the Message buttons share one row');
        assert.equal(await page.locator('.pipe-review-sendback [data-review-send="met"]').count(), 0, label + ': Mark met is not in the row');
        const approveBox = (await page.locator('.pipe-review-approve').boundingBox())!;
        const metBox = (await page.locator('.pipe-review-met').boundingBox())!;
        assert.ok(metBox.y >= approveBox.y + approveBox.height - 1, label + ': Mark met sits under Approve');

        // Layout: no sideways scroll; on a phone the decision sits under the attachment, not beside it.
        const scroll = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
        assert.ok(scroll, 'no horizontal page scroll');
        const mainBox = (await page.locator('.pipe-review-main').boundingBox())!;
        const sideBox = (await page.locator('.pipe-review-side').boundingBox())!;
        if (size.phone) assert.ok(sideBox.y >= mainBox.y + mainBox.height - 2, 'the phone stacks the decision below');
        else assert.ok(sideBox.x >= mainBox.x + mainBox.width - 2, 'the desktop puts the decision beside');
        if (size.phone) {
          // A phone: a 44px round × to exit, ‹ › at 44px, and the stage under the count.
          const exit = (await page.locator('.pipe-review-exit').boundingBox())!;
          assert.ok(exit.width === 44 && exit.height === 44, label + ': Exit is a 44px round button');
          for (const sel of ['[data-review-prev]', '[data-review-next]']) {
            const box = (await page.locator(sel).boundingBox())!;
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
        assert.deepEqual((state.posts as Post[]).map(p => p.path), ['tasks/D52/requirements/r-shots/send-back'], 'send-back posts to the item');
        assert.equal((state.posts as Post[])[0]!.body.note, 'Shot 2 still shows the old card');

        // Approve the last item with ⌘⏎ from the note, then Undo within the toast.
        await page.locator('.pipe-review textarea[name=note]').fill('Looks right');
        await page.locator('.pipe-review textarea[name=note]').focus();
        await page.keyboard.press(process.platform === 'darwin' ? 'Meta+Enter' : 'Control+Enter');
        await page.waitForSelector('.pipe-review-toast', {timeout: t(5000)});
        assert.match(await page.locator('.pipe-review-toast').innerText(), /Approved\s*·\s*Undo/);
        assert.equal((state.posts as Post[]).at(-1)!.path, 'tasks/D61/requirements/r-spec61/approve', 'approval posts to the item');
        await page.locator('[data-review-undo]').click();
        await page.waitForFunction(() => document.querySelector('.pipe-review-count')?.textContent.includes('For you · 1 of 1'), null, {timeout: t(5000)});
        assert.equal((state.posts as Post[]).at(-1)!.path, 'decisions/dec-1/undo', 'undo posts the decision');
        assert.equal(await page.locator('.pipe-review-toast').count(), 0, 'the toast goes once undone');

        assert.deepEqual(errors, [], 'no page errors');
        await context.close();
      }
    }

    // A touch phone has no keyboard to hint at: Esc and ⌘⏎ are not drawn where no hover is possible.
    {
      const {context, page, errors} = await openReview(browser, {width: 390, height: 844, theme: 'light', touch: true});
      const drawn = await page.locator('.pipe-review kbd').evaluateAll((els: Element[]) => els.filter((el: Element) => el.getClientRects().length > 0).length);
      assert.equal(drawn, 0, 'no key hints on a touch phone');
      assert.deepEqual(errors, [], 'touch phone: no page errors');
      await context.close();
    }

    // S1: at Validate, Not validated takes the note as its reason (no native dialog). An empty note posts nothing; with a
    // note it goes to the server, the item leaves the queue, and the notice names the follow-up. Other stages do not show it.
    {
      const {context, page, state, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'light', validate: true});
      const dialogs: string[] = [];
      page.on('dialog', d => { dialogs.push(d.message()); void d.dismiss(); });
      assert.equal(await page.locator('[data-review-not-validated]').count(), 1, 'Validate shows Not validated');
      await page.locator('[data-review-not-validated]').click();
      await page.waitForSelector('.pipe-review-err', {timeout: t(3000)});
      assert.match(await page.locator('.pipe-review-err').innerText(), /Write a note first/);
      assert.equal(state.posts.length, 0, 'an empty note posts nothing');
      await page.locator('.pipe-review textarea[name=note]').fill('Screenshots never validated');
      await page.locator('[data-review-not-validated]').click();
      await until(() => (state.posts as Post[]).some(p => p.path.endsWith('/not-validated')));
      assert.deepEqual((state.posts as Post[]).map(p => p.path), ['tasks/D70/requirements/r-val70/not-validated'], 'Not validated posts to the item');
      assert.equal((state.posts as Post[])[0]!.body.note, 'Screenshots never validated', 'the note is the reason sent');
      assert.equal((state.posts as Post[])[0]!.body.work_version, 3, 'Not validated names the work version the reviewer saw');
      assert.equal((state.posts as Post[])[0]!.body.requirement_version, 1, 'and the requirement version');
      assert.equal((state.posts as Post[])[0]!.body.attachment_id, 'c1', 'and the attachment the reviewer saw');
      assert.equal((state.posts as Post[])[0]!.body.stage_visit_id, 'v-validate', 'and the stage visit');
      await page.waitForFunction(() => document.querySelector('.pipe-review-notice')?.textContent.includes('follow-up'), null, {timeout: t(5000)});
      assert.equal((await page.locator('.pipe-review-notice').innerText()).trim(), 'Back to In development · follow-up D71 filed');
      assert.equal(await page.locator('[data-review-not-validated]').count(), 0, 'the next item is not at Validate');
      assert.equal(await page.locator('.pipe-review-count').first().innerText().then((s: string) => s.trim()), 'For you · 1 of 2');
      assert.deepEqual(dialogs, [], 'no native dialog');
      assert.deepEqual(errors, [], 'not validated: no page errors');
      await context.close();
    }

    // E4: the item on show is claimed for the viewer; another reviewer's claim disables Approve and names them.
    {
      const {context, page, state, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'dark'});
      await until(() => state.claims.length === 1);
      assert.deepEqual(state.claims, ['tasks/D52/requirements/r-shots'], 'the item on show is claimed');
      assert.equal(await page.locator('.pipe-review-approve').isDisabled(), false, 'an unclaimed item can be approved');
      await context.close();
      assert.deepEqual(errors, [], 'claim: no page errors');
    }
    {
      const {context, page, state, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'light', claimedBy: 'human:bob'});
      await page.waitForSelector('.pipe-review-claim', {timeout: t(5000)});
      assert.match(await page.locator('.pipe-review-claim').innerText(), /Bob is reviewing/, 'another reviewer is named');
      assert.equal(await page.locator('.pipe-review-approve').isDisabled(), true, 'Approve waits while another reviewer holds the item');
      assert.deepEqual((state.posts as Post[]).filter(p => p.path.endsWith('/approve')), [], 'no approval is sent for a claimed item');
      assert.deepEqual(errors, [], 'claimed: no page errors');
      await context.close();
    }

    // E1 and E6: a file opens the version it was attached as; an image previews from its own address.
    {
      const {context, page, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'dark'});
      assert.equal(await page.locator('img.pipe-review-img').getAttribute('src'), 'https://example.test/shot.png', 'an image previews');
      await page.locator('[data-review-next]').click();
      await page.waitForFunction(() => document.querySelector('.pipe-review-count')?.textContent.includes('2 of 2'), null, {timeout: t(5000)});
      assert.equal(await page.locator('.pipe-review-card a').getAttribute('href'), '/api/v2/files/f1/versions/2', 'the file opens its pinned version');
      assert.match(await page.locator('.pipe-review-card').innerText(), /spec\.pdf/, 'the file is named');
      assert.deepEqual(errors, [], 'files: no page errors');
      await context.close();
    }

    // E9: each approval owns its Undo window. Approve one item, then the next four seconds later: the second toast stays
    // for its own ten seconds, so it is still there seven seconds after its approval (the first approval's timer would
    // have hidden it at ten seconds after the first).
    {
      const {context, page, state, errors} = await openReview(browser, {width: 1440, height: 940, theme: 'dark'});
      await page.locator('.pipe-review-approve').click();
      await page.waitForSelector('.pipe-review-toast', {timeout: t(5000)});
      await new Promise(r => setTimeout(r, 4000));
      await page.locator('.pipe-review-approve').click();
      await page.waitForFunction(() => document.querySelector('.pipe-review-toast')?.textContent.includes('Undo'), null, {timeout: t(5000)});
      assert.equal((state.posts as Post[]).at(-1)!.path, 'tasks/D61/requirements/r-spec61/approve', 'the second approval posts');
      await new Promise(r => setTimeout(r, 7000));
      assert.equal(await page.locator('.pipe-review-toast').count(), 1, 'the second Undo window is still open after 7 seconds');
      assert.deepEqual(errors, [], 'undo windows: no page errors');
      await context.close();
    }

    // E13: a refused approval reloads the queue for the stage it was opened on, not the whole pipeline.
    {
      const context = await browser.newContext({viewport: {width: 1440, height: 940}, serviceWorkers: 'block'});
      await context.addInitScript(() => {
        try { localStorage.setItem('tico.theme', 'dark'); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
      });
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(e.message));
      const state = await install(page, {refuseApprove: true});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('[data-col="build"] .pipe-play-chip', {timeout: t(8000)});
      await page.locator('[data-col="build"] .pipe-play-chip').click();
      await page.waitForSelector('.pipe-review .pipe-review-count', {timeout: t(5000)});
      assert.equal((await page.locator('.pipe-review-count').first().innerText()).trim(), 'For you · 1 of 1', 'the Build stage has one item');
      await page.locator('.pipe-review-approve').click();
      await page.waitForFunction(() => document.querySelector('.pipe-review-err')?.textContent.includes('This changed'), null, {timeout: t(5000)});
      assert.ok(state.queueReads >= 2, 'the queue is read again');
      assert.equal((await page.locator('.pipe-review-count').first().innerText()).trim(), 'For you · 1 of 1', 'the reload keeps the Build stage');
      assert.deepEqual(errors, [], 'stage reload: no page errors');
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
