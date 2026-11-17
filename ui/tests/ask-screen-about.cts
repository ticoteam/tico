// The decision view says what an item is about (ui/app/decide-about.ts):
// - an OK that shows nothing of its own shows, on the left, who asked and its task's text as Markdown;
// - a link in that text to a place in the app (#/strategy) closes the view and opens the place there, and ▶ picks up at
//   the item it left, not the first of the walk;
// - a link to anywhere else opens in a new tab, and the view stays.
// Fixtures only (tasks-page.cts).
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
declare function decideOpen(id?: string): boolean;

const card = (page: Page) => page.locator('#need-card[open]');

/** Ana's ❓ and OK (tasks-page.cts needsOf, without its task of her own): t-1's text names the Strategy page. */
function world() {
  const tasks = fixtures();
  Object.assign(tasks.find(t => t.id === 't-1')!, {request: {text: 'Write the **Vision** on the [Strategy page](#/strategy) first. The [style guide](https://acme.example/style) has the rest.'}});
  return {tasks, needs: needsOf(tasks).filter(n => n.kind !== 'task')};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {tasks, needs} = world();
    const {page, errors} = await open(browser, {tasks, needs, viewport: {width: 1040, height: 760}});
    try {
      await page.evaluate(() => { decideOpen('r1'); });
      const text = card(page).locator('.dc-thing .dc-md');
      await text.waitFor({timeout: 5000});
      assert.equal(await text.locator('strong').innerText(), 'Vision', "the task's text, as Markdown");
      assert.match(await card(page).locator('.dc-thing .dc-who').innerText(), /^From /, 'who asked, over it');
      // Anywhere else: a new tab, and the view stays.
      assert.equal(await text.locator('a[href="https://acme.example/style"]').getAttribute('target'), '_blank');
      // A place in the app: the view closes on it, and ▶ picks up at this item.
      await text.locator('a[href="#/strategy"]').click();
      await page.waitForFunction(() => location.hash === '#/strategy');
      assert.equal(await card(page).count(), 0, 'the decision view closed');
      await page.evaluate(() => { location.hash = '#/tasks'; });
      await page.locator('#task-body .tk-row').first().waitFor({timeout: 5000});
      await page.locator('#task-play').click();
      await card(page).waitFor({timeout: 5000});
      await page.waitForFunction('typeof DEC !== "undefined" && !!DEC && DEC.at === "r1"', null, {timeout: 5000});
      assert.deepEqual(errors, []);
    } finally { await page.close(); }

    console.log("PASS: an item's task text on the left; a link in it to a place in the app opens there and ▶ picks up at the item.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
