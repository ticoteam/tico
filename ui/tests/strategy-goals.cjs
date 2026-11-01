// Strategy › Goals (ui/app/goals.js goalSheetOpen, ui/goals-kpis.js goalsRender) against the fake hub in
// support/strategy-fake.cjs (the Acme example): no Strategy line on top; "+ Goal" in the head; tapping a goal opens it
// on its own (title, owner, colour, metric, target, due, Remove), a sheet on a phone and a side panel on a computer;
// a long press on a line (phone) or its "+" (pointer) adds a goal for that owner, a group's for its lead; the name still
// opens the owner's panel. A member gets only their own goals to set and reads the rest.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {openStrategy, PHONE, DESKTOP, sideways, shot} = require('./support/strategy-fake.cjs');

const HASH = '#/strategy/goals';
const goalWrites = writes => writes.filter(w => w.path.startsWith('/api/v2/goals')).map(w => [w.path, w.body]);
const longPress = (page, selector) => page.evaluate(async sel => {
  const el = document.querySelector(sel), r = el.getBoundingClientRect();
  const touch = new Touch({identifier: 1, target: el, clientX: r.x + 40, clientY: r.y + r.height / 2});
  el.dispatchEvent(new TouchEvent('touchstart', {touches: [touch], targetTouches: [touch], changedTouches: [touch], bubbles: true}));
  await new Promise(done => setTimeout(done, 650));
  el.dispatchEvent(new TouchEvent('touchend', {touches: [], targetTouches: [], changedTouches: [touch], bubbles: true}));
  el.click();                                  // the tap that ends the press opens nothing more
}, selector);

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // A phone, the owner.
    {
      const {page, errors, writes} = await openStrategy(browser, {viewport: PHONE, hash: HASH});
      await page.locator('#goal-tree .gt-row[data-goal="g-co"]').waitFor();
      assert.equal(await page.locator('#goal-strategy, .gst').count(), 0, 'no Strategy line');
      const plus = await page.locator('#goal-new').boundingBox(), h1 = await page.locator('.rd-strat-head h1').boundingBox();
      assert(Math.abs((plus.y + plus.height / 2) - (h1.y + h1.height / 2)) < 12, '+ Goal sits beside the title');
      assert.equal(await page.locator('.gt-add').first().isVisible(), false, 'no "+" without a pointer');
      assert.equal(await sideways(page), false, 'no sideways scroll');
      await shot(page, 'phone-goals');

      // Tap a goal: its sheet, at the bottom.
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-goal').click();
      const sheet = page.locator('#goal-sheet[open]');
      await sheet.waitFor();
      assert.equal(await page.locator('#goal-panel[open]').count(), 0, 'the goal, not the owner panel');
      assert.equal(await sheet.locator('.gs-h').innerText(), 'Goal');
      const box = await sheet.boundingBox();
      assert(Math.abs(box.y + box.height - 844) <= 2 && box.width >= 388, 'a sheet at the bottom');
      assert.equal(await sheet.locator('select[name="owner"]').inputValue(), 'company');
      assert.equal(await sheet.locator('input[name="metric"]').inputValue(), 'Paying customers');
      assert.equal(await sheet.locator('input[name="target"]').inputValue(), '100');
      assert.equal(await sheet.locator('input[name="due"]').inputValue(), '2026-12-31');
      assert.equal(await sheet.locator('select[name="status"]').count(), 1, 'the colour');
      assert.equal(await sheet.locator('[data-goal-remove]').count(), 1);
      assert.equal(await page.evaluate(() => { const b = document.querySelector('#goal-sheet .tmodal-body'); return b.scrollWidth > b.clientWidth + 1; }), false, 'nothing sideways in the sheet');
      await shot(page, 'phone-goal-sheet');
      // A target goes with its date: clearing the date saves nothing and asks for it.
      await sheet.locator('input[name="title"]').fill('Reach 120 paying customers');
      await sheet.locator('input[name="target"]').fill('120');
      await sheet.locator('input[name="due"]').fill('');
      await sheet.locator('button[type="submit"]').click();
      assert.equal(await page.evaluate(() => document.activeElement?.name), 'due');
      assert.deepEqual(goalWrites(writes), []);
      await sheet.locator('input[name="due"]').fill('2027-03-31');
      await sheet.locator('button[type="submit"]').click();
      await page.locator('#goal-sheet[open]').waitFor({state: 'detached'}).catch(() => {});
      await page.waitForFunction(() => !document.querySelector('#goal-sheet')?.open);
      assert.deepEqual(goalWrites(writes), [
        ['/api/v2/goals/g-co', {title: 'Reach 120 paying customers'}],
        ['/api/v2/goals/g-co/kpis/k-cust', {kind: 'improve', target: 120, deadline: '2027-03-31', baseline: 20, baseline_at: '2026-09-01'}]]);
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-title', {hasText: 'Reach 120'}).waitFor();

      // Long-press Ben's line: a new goal for him, with a new measure.
      writes.length = 0;
      await longPress(page, '#goal-tree .gt-row[data-owner="human:ben"]:not(.gt-cont)');
      await sheet.waitFor();
      assert.equal(await sheet.locator('.gs-h').innerText(), 'New goal');
      assert.equal(await page.locator('#goal-panel[open]').count(), 0, 'the press opened only the sheet');
      assert.equal(await sheet.locator('select[name="owner"]').inputValue(), 'human:ben');
      await sheet.locator('input[name="title"]').fill('Close 10 deals');
      await sheet.locator('input[name="metric"]').fill('Deals closed');
      await sheet.locator('input[name="target"]').fill('10');
      await sheet.locator('input[name="due"]').fill('2026-12-31');
      await sheet.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-sheet')?.open);
      assert.deepEqual(goalWrites(writes), [
        ['/api/v2/goals', {title: 'Close 10 deals', owner_id: 'human:ben'}],
        ['/api/v2/goals/g-3/kpis', {name: 'Deals closed', kind: 'improve', target: 10, deadline: '2026-12-31'}]]);

      // Remove it from its sheet.
      writes.length = 0;
      await page.locator('#goal-tree .gt-row[data-goal="g-3"] .gt-goal').click();
      await sheet.locator('[data-goal-remove]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-sheet')?.open);
      assert.deepEqual(goalWrites(writes), [['/api/v2/goals/g-3/status', {status: 'dropped', note: ''}]]);
      await page.waitForFunction(() => !document.querySelector('#goal-tree [data-goal="g-3"]'));

      // The name still opens the owner's panel.
      await page.locator('#goal-tree .gt-row[data-owner="company"] .gt-name').click();
      await page.locator('#goal-panel[open]').waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A computer, the owner: "+" on hover, a group's for its lead; "+ Goal" for the company; a side panel.
    {
      const {page, errors, writes} = await openStrategy(browser, {viewport: DESKTOP, hash: HASH});
      await page.locator('#goal-tree .gt-row[data-goal="g-co"]').waitFor();
      await shot(page, 'desktop-goals');
      const sales = page.locator('#goal-tree .gt-group[data-group="sales"]');
      await sales.hover();
      await sales.locator('[data-goal-add-group]').click();
      const sheet = page.locator('#goal-sheet[open]');
      assert.equal(await sheet.locator('select[name="owner"]').inputValue(), 'human:ben', "a group's goal is its lead's");
      await sheet.locator('[data-gs-close]').click();
      const row = page.locator('#goal-tree .gt-row[data-owner="bot:sdr"]');
      await row.hover();
      await shot(page, 'desktop-goals-hover');
      await row.locator('[data-goal-add]').click();
      assert.equal(await sheet.locator('select[name="owner"]').inputValue(), 'bot:sdr');
      assert.equal(await page.locator('#goal-panel[open]').count(), 0);
      await sheet.locator('[data-goal-cancel]').click();

      await page.locator('#goal-new').click();
      assert.equal(await sheet.locator('select[name="owner"]').inputValue(), 'human:ana');
      await sheet.locator('select[name="owner"]').selectOption('company');
      assert.equal(await sheet.locator('select[name="parent"]').isDisabled(), true, 'a company goal supports nothing');
      await sheet.locator('input[name="title"]').fill('Ship the Acme mobile app');
      const box = await sheet.boundingBox();
      assert(box.x + box.width >= 1438 && box.height >= 898 && box.width <= 482, 'a side panel on the right');
      await shot(page, 'desktop-goal-sheet');
      await sheet.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-sheet')?.open);
      assert.deepEqual(goalWrites(writes), [['/api/v2/goals', {title: 'Ship the Acme mobile app', owner_id: 'company'}]]);

      // Hand Sales Lead's goal to the SDR, and Details opens the owner's panel.
      writes.length = 0;
      await page.locator('#goal-tree .gt-row[data-goal="g-sales"] .gt-goal').click();
      await sheet.locator('select[name="owner"]').selectOption('bot:sdr');
      await sheet.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector('#goal-sheet')?.open);
      assert.deepEqual(goalWrites(writes), [['/api/v2/goals/g-sales', {owner_id: 'bot:sdr'}]]);
      await page.locator('#goal-tree .gt-row[data-owner="bot:sdr"][data-goal="g-sales"] .gt-goal').click();
      await sheet.locator('[data-gs-details]').click();
      await page.locator('#goal-panel[open][data-owner="bot:sdr"]').waitFor();
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A member: their own goals only; anyone else's goal reads, with nothing to save.
    for (const viewport of [PHONE, DESKTOP]) {
      const {page, errors, writes} = await openStrategy(browser, {viewport, hash: HASH, viewer: 'cara'});
      await page.locator('#goal-tree .gt-row[data-goal="g-co"]').waitFor();
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-goal').click();
      const sheet = page.locator('#goal-sheet[open]');
      assert.equal(await sheet.locator('button[type="submit"], [data-goal-remove]').count(), 0, 'nothing to save');
      assert.equal(await sheet.locator('input[name="title"]').getAttribute('readonly'), '');
      assert.equal(await sheet.locator('select[name="owner"]').isDisabled(), true);
      await sheet.locator('[data-goal-cancel]').click();
      await page.locator('#goal-new').click();
      assert.deepEqual(await sheet.locator('select[name="owner"] option').evaluateAll(os => os.map(o => o.value)), ['human:cara']);
      if (viewport === PHONE) await shot(page, 'phone-goal-sheet-member');
      await sheet.locator('[data-goal-cancel]').click();
      assert.equal(await page.locator('#goal-tree .gt-row[data-owner="human:ben"] [data-goal-add]').count(), 0, "no + on someone else's line");
      assert.deepEqual(goalWrites(writes), []);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('strategy goals ok');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
