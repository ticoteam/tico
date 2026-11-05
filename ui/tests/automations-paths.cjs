// Paths in an automation's flow (ui/app/automations-flow.js, automations-editors.js) against the fake hub in
// support/automations-fake.cjs: a phone swipes through the paths with a dot per path (the dots follow the swipe; a tap
// on one scrolls to it), and nothing scrolls the page sideways; a path's name opens the Paths editor at that path, where
// "+ Path" and "+ Condition" add a path with its condition, saved as one PATCH. A computer shows the paths side by side
// and stops offering "+ Path" at 10.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openAutomations, PHONE, DESKTOP, sideways, shot} = require('./support/automations-fake.cjs');

// The path strip needs no scrolling, and no step name in it is cut (two lines at most, each word whole).
const fits = page => page.evaluate(() => {
  const strip = document.querySelector('.au-paths');
  const clipped = [...strip.querySelectorAll('.au-ct')].filter(t => t.scrollHeight > t.clientHeight + 1 || t.scrollWidth > t.clientWidth + 1).map(t => t.textContent);
  return {strip: strip.scrollWidth <= strip.clientWidth + 1, clipped};
});
const selectedDot = page => page.evaluate(() => [...document.querySelectorAll('.au-dots [data-dot]')].findIndex(d => d.getAttribute('aria-selected') === 'true'));

(async () => {
  const browser = await chromium.launch();
  try {
    // A phone: swipe, dots, then a new path with a condition.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: PHONE, hash: '#/automations/au_inbox'});
      await page.waitForSelector('.au-paths');
      const strip = page.locator('.au-paths');
      assert.equal(await page.locator('.au-dots [data-dot]').count(), 3);
      assert.equal(await page.locator('.au-dots').isVisible(), true);
      assert.equal(await strip.evaluate(el => getComputedStyle(el).scrollSnapType.includes('x')), true);
      assert.equal(await selectedDot(page), 0);
      const w = await strip.evaluate(el => ({col: el.children[0].getBoundingClientRect().width, box: el.clientWidth}));
      assert.ok(w.col < w.box && w.col > w.box * 0.7, 'one path fills most of the screen, the next peeks in');
      await strip.evaluate(el => { el.scrollLeft = el.children[1].offsetLeft - el.offsetLeft; });
      await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[1].getAttribute('aria-selected') === 'true');
      await page.locator('.au-dots [data-dot="other"]').click();
      await page.waitForFunction(() => document.querySelectorAll('.au-dots [data-dot]')[2].getAttribute('aria-selected') === 'true');
      assert.ok(await strip.evaluate(el => el.scrollLeft > 0));
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-paths');

      await page.locator('.au-pname[data-path="1"]').click();
      await page.waitForSelector('.au-ed .au-rb.focus[data-path-ed="1"]');
      assert.equal(await page.locator('[data-b="step.paths.1.name"]').inputValue(), 'Money');
      await page.locator('[data-op="add"][data-at="step.paths"]').click();
      assert.equal(await page.locator('[data-b="step.paths.2.name"]').inputValue(), 'Path 3');
      await page.fill('[data-b="step.paths.2.name"]', 'Route');
      await page.locator('[data-op="add"][data-at="step.paths.2.when"]').click();
      await page.fill('[data-b="step.paths.2.when.0.field"]', 'd1.bucket');
      await page.selectOption('[data-b="step.paths.2.when.0.op"]', 'is');
      await page.fill('[data-b="step.paths.2.when.0.value"]', 'route');
      await page.fill('[data-b="step.paths.2.when.0.min"]', '60');
      assert.equal(await sideways(page), false);
      await shot(page, 'phone-paths-edit');
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelector('.au-ver')?.textContent === 'v8');
      const p = writes.at(-1).body.steps[2];
      assert.equal(p.paths.length, 3);
      assert.deepEqual(p.paths[2], {name: 'Route', when: [{field: 'd1.bucket', op: 'is', value: 'route', min: 0.6}], steps: []});
      assert.equal(await page.locator('.au-dots [data-dot]').count(), 4);
      assert.match(await page.locator('.au-pth[data-col="2"]').innerText(), /Route[\s\S]*bucket = route ≥ 60%/);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer: side by side, no dots, names whole with three or four columns; "+ Path" stops at 10.
    {
      const {page, errors, writes} = await openAutomations(browser, {viewport: DESKTOP, hash: '#/automations/au_inbox'});
      await page.waitForSelector('.au-paths');
      assert.equal(await page.locator('.au-dots').isVisible(), false);
      const tops = await page.locator('.au-pth').evaluateAll(cols => cols.map(c => Math.round(c.getBoundingClientRect().top)));
      assert.equal(new Set(tops).size, 1, 'the paths sit side by side');
      await page.locator('.au-card[data-open="p1"]').click();
      await page.waitForSelector('.au-ed [data-b="step.paths.0.name"]');
      for (let n = 2; n < 10; n++) await page.locator('[data-op="add"][data-at="step.paths"]').click();
      assert.equal(await page.locator('[data-b^="step.paths."][data-b$=".name"]').count(), 10);
      assert.equal(await page.locator('[data-op="add"][data-at="step.paths"]').count(), 0);
      await page.locator('[data-op="rm"][data-at="step.paths"]').last().click();
      assert.equal(await page.locator('[data-op="add"][data-at="step.paths"]').count(), 1);
      await page.locator('.au-ed [data-op="close"]').click();
      assert.equal(writes.length, 0, 'closing without Save writes nothing');
      assert.deepEqual(await fits(page), {strip: true, clipped: []}, 'three columns: every step name readable');
      await shot(page, 'desktop-paths');
      // A third path makes four columns; they still fit without scrolling, names whole.
      await page.locator('.au-card[data-open="p1"]').click();
      await page.locator('[data-op="add"][data-at="step.paths"]').click();
      await page.locator('.au-save').click();
      await page.waitForFunction(() => document.querySelectorAll('.au-pth').length === 4);
      assert.deepEqual(await fits(page), {strip: true, clipped: []}, 'four columns: every step name readable');
      await shot(page, 'desktop-paths-4');
      assert.equal(await sideways(page), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('automations paths ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
