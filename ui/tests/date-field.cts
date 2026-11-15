// The date field (ui/app/format.ts dateField, ui/styles/forms.css .df-*; style guide, Fields > Date) in its first home,
// the goal sheet's Due (ui/app/goals.ts), against the fake hub in support/strategy-fake.cts. On a computer the field reads
// "Dec 31, 2026" while its value stays 2026-12-31; a click opens the calendar on that month with the day chosen; the
// down arrow takes the keys into it, arrows move a day or a week, Page Down a month, Enter picks (the page hears one
// change) and the focus is back on the field; Esc closes the calendar and not the sheet; a typed day is taken on Tab, a
// typed non-day puts the last one back; Save posts the day picked. On a phone the field stays the native date input.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
import type * as StrategyFakeSupport from './support/strategy-fake.cts';
const {openStrategy, PHONE, DESKTOP} = require('./support/strategy-fake.cts') as StrategyFakeSupport.Api;

const HASH = '#/strategy/goals';
// What the field shows (the native value getter), apart from the day the page reads (its `value`, read in the page:
// Playwright's inputValue reads the native one too).
const shown = (el: HTMLInputElement) => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.get!.call(el) as string;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors, writes} = await openStrategy(browser, {viewport: DESKTOP, hash: HASH});
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-goal').click();
      const sheet = page.locator('#goal-sheet[open]');
      await sheet.waitFor();
      const due = sheet.locator('input[name="due"]');
      await page.waitForFunction(() => document.querySelector('#goal-sheet input[name="due"]')?.getAttribute('type') === 'text');
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2026-12-31', 'the page reads the day');
      assert.equal(await due.evaluate(shown), 'Dec 31, 2026', 'the field shows it in words, with the year');
      await due.evaluate(el => {
        const w = window as unknown as {changes: string[]};
        w.changes = [];
        el.addEventListener('change', () => w.changes.push((el as HTMLInputElement).value));
      });

      // A click opens the calendar on the day's month, the day chosen.
      await due.click();
      const pop = page.locator('.df-pop:popover-open');
      await pop.waitFor();
      assert.equal(await pop.locator('.df-month').innerText(), 'December 2026');
      assert.equal(await pop.locator('[aria-selected="true"]').getAttribute('data-df-day'), '2026-12-31');
      const box = (await pop.boundingBox())!, field = (await due.boundingBox())!;
      assert.ok(box.y >= field.y + field.height - 1 || box.y + box.height <= field.y + 1, 'the calendar sits under (or over) the field, not on it');

      // Keys: down into the grid, then a day back, a week up, a month on; Enter picks.
      await page.keyboard.press('ArrowDown');
      const active = () => page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset['dfDay'] || document.activeElement?.getAttribute('name') || '');
      assert.equal(await active(), '2026-12-31');
      await page.keyboard.press('ArrowLeft');
      await page.keyboard.press('ArrowUp');
      assert.equal(await active(), '2026-12-23');
      await page.keyboard.press('PageDown');
      assert.equal(await active(), '2027-01-23');
      assert.equal(await pop.locator('.df-month').innerText(), 'January 2027');
      await page.keyboard.press('Enter');
      assert.equal(await pop.count(), 0, 'Enter closes the calendar');
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2027-01-23');
      assert.equal(await due.evaluate(shown), 'Jan 23, 2027');
      assert.equal(await active(), 'due', 'the focus is back on the field');
      assert.equal(await due.evaluate(el => new FormData((el as HTMLInputElement).form!).get('due')), '2027-01-23', 'FormData reads the day, not the words');
      assert.deepEqual(await page.evaluate(() => (window as unknown as {changes: string[]}).changes), ['2027-01-23'], 'one change, once picked');

      // Esc closes the calendar, not the sheet.
      await page.keyboard.press('ArrowDown');
      await pop.waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await pop.count(), 0);
      assert.equal(await sheet.count(), 1, 'the sheet stays open');
      assert.equal(await active(), 'due');

      // Typed days: taken on leaving the field; a non-day puts the last one back.
      await due.fill('3/15/2027');
      await page.keyboard.press('Tab');
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2027-03-15');
      assert.equal(await due.evaluate(shown), 'Mar 15, 2027');
      await due.fill('someday');
      await page.keyboard.press('Tab');
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2027-03-15');
      assert.equal(await due.evaluate(shown), 'Mar 15, 2027');
      assert.deepEqual(await page.evaluate(() => (window as unknown as {changes: string[]}).changes), ['2027-01-23', '2027-03-15']);

      // The mouse: next month, then a day.
      await due.click();
      await pop.locator('[data-df-step="1"]').click();
      assert.equal(await pop.locator('.df-month').innerText(), 'April 2027');
      await pop.locator('[data-df-day="2027-04-02"]').click();
      assert.equal(await pop.count(), 0);
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2027-04-02');

      // A click outside closes it and keeps the day.
      await due.click();
      await pop.waitFor();
      await sheet.locator('input[name="title"]').click();
      assert.equal(await pop.count(), 0);
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2027-04-02');

      // Save posts the day.
      writes.length = 0;
      await sheet.locator('button[type="submit"]').click();
      await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#goal-sheet')?.open);
      assert.ok(writes.some(w => JSON.stringify(w.body).includes('"deadline":"2027-04-02"')), `Save posts the day (${JSON.stringify(writes.map(w => [w.path, w.body]))})`);
      assert.deepEqual(errors, []);
      await page.close();
    }

    // A phone keeps the native picker.
    {
      const {page, errors} = await openStrategy(browser, {viewport: PHONE, hash: HASH});
      await page.locator('#goal-tree .gt-row[data-goal="g-co"] .gt-goal').click();
      const due = page.locator('#goal-sheet[open] input[name="due"]');
      await due.waitFor();
      await page.waitForTimeout(100);   // the field's scan runs on the next frame
      assert.equal(await due.getAttribute('type'), 'date', 'a phone keeps the native picker');
      assert.equal(await due.evaluate(el => (el as HTMLInputElement).value), '2026-12-31');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('date-field: words in the field, the day in its value; click, keys, typing and Esc; native on a phone');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error(e); process.exit(1); });
