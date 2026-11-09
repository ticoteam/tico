// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-dots.cjs
// The requirement dots on a pipeline's cards (their names on tap or hover), a ticket's requirements with Mark met (its ⋯
// asks for a why), and the board's ▶ count. A ticket with no requirements gets no dots. Desktop, dark and light.
import type * as PipelineFixtureSupport from './support/pipeline-fixture.cts';
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cts') as PipelineFixtureSupport.Api;
const {t} = (() => { try { return require('./support/load.cjs') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({viewport: {width: 1440, height: 940}, serviceWorkers: 'block'});
      await context.addInitScript(([theme]: [string]) => {
        try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
      }, [theme] as [string]);
      const page = await context.newPage();
      const errors: string[] = [];
      page.on('pageerror', e => errors.push(e.message));
      const state = await install(page);
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#task-body [data-task-key="tD52"]', {timeout: t(8000)});

      const d52 = page.locator('[data-task-key="tD52"] .pdots');
      assert.equal(await d52.count(), 1, 'a ticket with requirements gets one dot group');
      assert.equal(await d52.getAttribute('aria-label'), '2 of 3 requirements met');
      assert.equal(await page.locator('[data-task-key="tD52"] .pdot').count(), 3);
      assert.equal(await page.locator('[data-task-key="tD17"] .pdots').count(), 0, 'a ticket without requirements has no dots');
      // E5: a requirement the stage does not have yet (the server's "none") is a hollow missing dot, not an invisible one.
      assert.equal(await page.locator('[data-task-key="tD61"] .pdot-missing').count(), 1, 'a missing requirement is drawn hollow');

      // Tap a dot: the names open on that card, and the card does not open the ticket.
      await page.locator('[data-task-key="tD52"] .pdot').nth(2).click();
      assert.ok(await page.locator('[data-task-key="tD52"] .pdots[data-open]').count(), 'a tap opens the names');
      const tip = await page.locator('[data-task-key="tD52"] .pdots-tip').innerText();
      assert.match(tip, /Screenshots · waiting · You/, 'the next actor is named, "You" for the viewer');
      assert.equal(await page.locator('.task-requirements').count(), 0, 'the tap does not open the ticket');
      await page.mouse.click(5, 5);
      assert.equal(await page.locator('[data-task-key="tD52"] .pdots[data-open]').count(), 0, 'a tap elsewhere closes the names');

      // The board's ▶ count is the tickets waiting on Ana: D52 and D61 at the pipeline level.
      assert.equal((await page.locator('.tl-head .pipe-play').first().innerText()).trim(), '2 for you');
      // A column's ▶ is a compact chip as tall as the column's count, named for its tickets.
      const chip = page.locator('[data-col="build"] .pipe-play-chip');
      assert.equal((await chip.innerText()).trim(), '1');
      assert.equal(await chip.getAttribute('aria-label'), '1 for you');
      const chipBox = (await chip.boundingBox())!, countBox = (await page.locator('[data-col="build"] header .cnt').boundingBox())!;
      assert.ok(Math.abs(chipBox.height - countBox.height) <= 1, 'the chip is as tall as the count');

      // Open the ticket: its requirements, and Mark met on the waiting one with a why.
      await page.locator('[data-task-key="tD52"] .bcard-open').click();
      await page.waitForSelector('.task-requirements', {timeout: t(5000)});
      assert.equal(await page.locator('.task-req').count(), 3);
      // E8: the ticket's requirements and their attachments come in one request, not one per attachment requirement.
      assert.equal(state.requirementReads.D52, 1, 'one request reads the ticket');
      assert.equal(state.attachmentReads, 0, 'no request per requirement for its attachments');
      // E7: a met requirement says who passed it, and under which version when the rule has changed since.
      assert.match(await page.locator('.task-req[data-req-id="r-spec"]').innerText(), /Met under v1 · You/, 'who passed it (the viewer is named You), under which version');
      // E6: an attachment's link and its picture open the version it was attached as, not the file's latest.
      assert.equal(await page.locator('.task-req[data-req-id="r-shots"] .task-req-thumb').getAttribute('href'), '/api/v2/files/f9/versions/3', 'the link opens the pinned version');
      assert.equal(await page.locator('.task-req[data-req-id="r-shots"] img').getAttribute('src'), '/api/v2/files/f9/versions/3', 'the picture is the pinned version');
      assert.match(await page.locator('.task-requirements h3').innerText(), /REQUIREMENTS · 2 OF 3/i);
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-more]').click();
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-mark] button[type=submit]').click();
      assert.match(await page.locator('.task-req[data-req-id="r-shots"] .task-req-err').innerText(), /Say why/);
      assert.equal(state.posts.length, 0, 'no mark without a why');
      await page.locator('.task-req[data-req-id="r-shots"] input[name=why]').fill('Checked shot 2 myself');
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-mark] button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector<HTMLElement>('.task-req[data-req-id="r-shots"]')?.dataset.state === 'met', null, {timeout: t(5000)});
      assert.deepEqual(state.posts.map(p => p.path), ['tasks/D52/requirements/r-shots/mark-met'], 'one mark-met post');
      assert.equal((state.posts[0] as {body: {why: string}}).body.why, 'Checked shot 2 myself');
      assert.deepEqual(errors, [], 'no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-dots: ok');
})().catch(error => { console.error(error); process.exit(1); });
