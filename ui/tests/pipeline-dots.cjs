// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-dots.cjs
// The requirement dots on a pipeline's cards (their names on tap or hover), a ticket's requirements with Mark met (its ⋯
// asks for a why), and the board's ▶ count. A ticket with no requirements gets no dots. Desktop, dark and light.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) {
      const context = await browser.newContext({viewport: {width: 1440, height: 940}, serviceWorkers: 'block'});
      await context.addInitScript(([theme]) => {
        try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
      }, [theme]);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const state = await install(page);
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#task-body [data-task-key="tD52"]', {timeout: t(8000)});

      const d52 = page.locator('[data-task-key="tD52"] .pdots');
      assert.equal(await d52.count(), 1, 'a ticket with requirements gets one dot group');
      assert.equal(await d52.getAttribute('aria-label'), '2 of 3 requirements met');
      assert.equal(await page.locator('[data-task-key="tD52"] .pdot').count(), 3);
      assert.equal(await page.locator('[data-task-key="tD17"] .pdots').count(), 0, 'a ticket without requirements has no dots');

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
      const chipBox = await chip.boundingBox(), countBox = await page.locator('[data-col="build"] header .cnt').boundingBox();
      assert.ok(Math.abs(chipBox.height - countBox.height) <= 1, 'the chip is as tall as the count');

      // Open the ticket: its requirements, and Mark met on the waiting one with a why.
      await page.locator('[data-task-key="tD52"] .bcard-open').click();
      await page.waitForSelector('.task-requirements', {timeout: t(5000)});
      assert.equal(await page.locator('.task-req').count(), 3);
      assert.match(await page.locator('.task-requirements h3').innerText(), /REQUIREMENTS · 2 OF 3/i);
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-more]').click();
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-mark] button[type=submit]').click();
      assert.match(await page.locator('.task-req[data-req-id="r-shots"] .task-req-err').innerText(), /Say why/);
      assert.equal(state.posts.length, 0, 'no mark without a why');
      await page.locator('.task-req[data-req-id="r-shots"] input[name=why]').fill('Checked shot 2 myself');
      await page.locator('.task-req[data-req-id="r-shots"] [data-req-mark] button[type=submit]').click();
      await page.waitForFunction(() => document.querySelector('.task-req[data-req-id="r-shots"]')?.dataset.state === 'met', null, {timeout: t(5000)});
      assert.deepEqual(state.posts.map(p => p.path), ['tasks/D52/requirements/r-shots/mark-met'], 'one mark-met post');
      assert.equal(state.posts[0].body.why, 'Checked shot 2 myself');
      assert.deepEqual(errors, [], 'no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-dots: ok');
})().catch(error => { console.error(error); process.exit(1); });
