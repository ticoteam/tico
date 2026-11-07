// Run with Playwright available: NODE_PATH=/path/to/node_modules node ui/tests/pipeline-rail.cjs
// The manager rail beside a pipeline (log with errors first, Late, hide and remember, chat), the phone's one-stage view
// with its chips, bottom bar and sheet, Move anyway on a refused drag, and Attach to on a ticket. 1440 and 390 wide,
// dark and light.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {install} = require('./support/pipeline-fixture.cjs');
const {t} = (() => { try { return require('./support/load.cjs'); } catch { return {t: ms => ms}; } })();

const overflow = page => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const theme of ['dark', 'light']) for (const width of [1440, 390]) {
      const phone = width < 600, label = `${theme} ${width}`;
      const context = await browser.newContext({viewport: {width, height: phone ? 844 : 940}, serviceWorkers: 'block', hasTouch: phone});
      await context.addInitScript(([theme]) => {
        try { localStorage.setItem('tico.theme', theme); localStorage.setItem('tico.tasks.type', 'dev-tickets'); } catch {}
      }, [theme]);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      const state = await install(page, {manager: true, refuseMove: true});
      await page.goto('http://tico-ui.test/#/tasks?type=dev-tickets&view=board');
      await page.waitForSelector('#pipe-rail', {state: 'attached', timeout: t(8000)});
      await page.waitForFunction(() => document.querySelector('.pr-body .pr-late'), null, {timeout: t(8000)});
      assert.ok(await page.evaluate(() => document.querySelector('.tasks-page').classList.contains('has-rail')), label);


      const stepAge = async () => {
        // The cards past their stage's limit say so; others show nothing.
        await page.waitForSelector('[data-task-key="tD17"] [data-step-age]', {timeout: t(5000)});
        assert.equal(await page.locator('[data-task-key="tD52"] [data-step-age]').count(), 0, label + ': on time shows no time');
      };
      if (!phone) {
        await stepAge();
        // The rail: the manager, when it ran, three tabs, the Late list, the log with the failed run before the newer ok one.
        assert.ok((await page.locator('.pr-head b').innerText()).length > 0, 'the manager is named');
        assert.match(await page.locator('.pr-ran').innerText(), /Ran \d\d:\d\d/);
        assert.deepEqual(await page.locator('.pr-tab').allInnerTexts().then(a => a.map(s => s.replace(/\s+\d+$/, '').trim())), ['Log', 'Tasks', 'Learned']);
        assert.match(await page.locator('.pr-late').first().innerText(), /D17/);
        const lines = await page.locator('.pr-log .pr-line').allInnerTexts();
        assert.match(lines[0], /unreachable/, 'errors first');
        assert.equal(await page.locator('.pr-chat').count(), 1, 'chat with the manager is in the rail');
        // Hide to a strip and remember it as a user pref; a fresh load reads it back.
        await page.locator('[data-rail-hide]').click();
        await page.waitForFunction(() => document.querySelector('#pipe-rail').classList.contains('hidden') || getComputedStyle(document.querySelector('.pr-panel')).display === 'none');
        assert.equal(state.prefs['pipeline.rail_hidden'], true, 'hide is remembered');
        await page.reload();
        await page.waitForSelector('#pipe-rail', {state: 'attached', timeout: t(8000)});
        await page.waitForFunction(() => getComputedStyle(document.querySelector('.pr-panel')).display === 'none', null, {timeout: t(5000)});
        await page.locator('[data-rail-show]').click();
        await page.waitForFunction(() => getComputedStyle(document.querySelector('.pr-panel')).display !== 'none');
        assert.equal(state.prefs['pipeline.rail_hidden'], false);
      } else {
        // The phone: one stage with its chips, the rail as a bar, the sheet from the bar.
        assert.ok(await page.locator('.pchips .pchip').count() >= 3, label + ': a chip for each stage');
        assert.equal(await page.locator('#task-body .bcol').count(), 1, label + ': one stage at a time');
        const chip = await page.locator('.pchips .pchip').first().boundingBox();
        const reach = await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.closest('.pchip') !== null, [chip.x + chip.width / 2, chip.y - 5]);
        assert.ok(chip.height >= 32 && reach, label + ': chips reach a 44px target');
        await page.locator('.pchips .pchip').nth(1).click();
        await page.waitForFunction(() => document.querySelector('.pchip.on')?.dataset.pipeChip === 'build');
        await stepAge();
        assert.ok(await page.locator('[data-task-key="tD52"]').count(), label + ': Build shows D52');
        assert.ok(await page.locator('.pchip-dot').count() >= 1, label + ': a dot where something waits on you');
        const bar = page.locator('#pipe-rail-bar');
        assert.ok(await bar.isVisible(), label + ': the bar shows');
        assert.match(await bar.innerText(), /1 late/);
        assert.ok((await bar.boundingBox()).height >= 44, label + ': the bar is a 44px target');
        await bar.click();
        await page.waitForFunction(() => document.querySelector('#pipe-rail')?.classList.contains('sheet-open'));
        await page.locator('.pr-late').first().waitFor({state: 'visible'});
        assert.match(await page.locator('.pr-late').first().innerText(), /D17/);
        await page.locator('.pr-x[data-rail-close]').click();
        await page.waitForFunction(() => !document.querySelector('#pipe-rail').classList.contains('sheet-open'));
      }
      assert.ok(await overflow(page) <= 0, label + ': no horizontal scroll');

      // Move anyway: a refused drag asks for a reason, then sends the move again with override.
      await page.evaluate(() => taskBoardDrop(TASKS_ST, {key: 'tD52', target: {dataset: {dropStep: 'review1'}}, from: {}, prev: null, next: null, card: document.body}));
      await page.waitForSelector('dialog.pipe-override', {timeout: t(5000)});
      await page.locator('dialog.pipe-override button[type=submit]').click();
      assert.equal(await page.locator('dialog.pipe-override').count(), 1, label + ': no reason, no move');
      assert.equal(state.moves.length, 1);
      await page.locator('dialog.pipe-override input[name=why]').fill('Shots reviewed on a call');
      await page.locator('dialog.pipe-override button[type=submit]').click();
      await page.waitForFunction(() => !document.querySelector('dialog.pipe-override'));
      await new Promise(r => setTimeout(r, 200));
      const retry = state.moves.at(-1);
      assert.equal(retry.override_requirements, true, label + ': the retry carries override');
      assert.equal(retry.note, 'Shots reviewed on a call');
      assert.equal(state.moves.filter(m => !m.override_requirements).length, 1, label + ': one refused move first');

      // Attach to: a link on the Screenshots requirement lists it.
      await page.evaluate(() => { location.hash = '#/tasks?type=dev-tickets&view=board'; });
      await page.evaluate(() => { const task = TASKS_ST.tasks.find(x => x.id === 'D52'); taskModalShow(task); });
      await page.waitForSelector('.task-requirements [data-req-attach-open]', {timeout: t(5000)});
      await page.locator('[data-req-attach-open]').click();
      await page.locator('[data-req-attach="r-shots"]').click();
      await page.locator('.task-req-attach-form input[name=url]').fill('https://example.test/shot.png');
      await page.locator('.task-req-attach-form button[type=submit]').click();
      await page.waitForSelector('.task-req-files li', {timeout: t(5000)});
      assert.deepEqual(state.attachments.at(-1), {source: 'link', url: 'https://example.test/shot.png'}, label + ': the link is attached');
      assert.ok(await overflow(page) <= 0, label + ': no horizontal scroll with the ticket open');

      assert.deepEqual(errors, [], label + ': no page errors');
      await context.close();
    }
  } finally {
    await browser.close();
  }
  console.log('pipeline-rail: ok');
})().catch(error => { console.error(error); process.exit(1); });
