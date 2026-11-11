// A pipeline on the Tasks page, desktop (docs/pipelines-v3.md S7, S10), offline on the fake server of tasks-page.cts:
//  - the columns are the pipeline's work stages, in order (Done and Closed are not columns), and they share the width;
//  - the manager's rail is closed at first: the header's chat icon opens it as a column at the RIGHT of the stages
//    (never under them), its close button closes it, and open or closed is kept in this browser;
//  - a pipeline with no manager, or a phone, has no chat icon and no rail.
// Run: node scripts/ui-tests.cts pipeline-rail
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, PIPELINES} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

const box = (page: Page, sel: string) => page.locator(sel).evaluate(el => {
  const r = el.getBoundingClientRect();
  return {x: r.x, y: r.y, w: r.width, h: r.height, hidden: (el as HTMLElement).hidden};
});

(async () => {
  const product = PIPELINES.find(p => p.id === 'product')!;
  (product as {manager_id: string | null}).manager_id = 'bot:pm';
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      const cols = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => [e.getAttribute('aria-label'), e.getBoundingClientRect().width]));
      assert.deepEqual(cols.map(c => c[0]), ['Spec', 'Build', 'Review'], 'one column per work stage, in order; no Done or Closed');
      assert.ok(cols.every(c => Number(c[1]) > 260), `columns share the width: ${JSON.stringify(cols)}`);
      const pane = await box(page, '#tasks-pane');

      // Closed at first: no rail under the stages, the chat icon in the header.
      assert.equal((await box(page, '#task-rail')).hidden, true, 'the rail is closed by default');
      const icon = page.locator('#task-manager');
      assert.equal(await icon.isVisible(), true, 'the chat icon shows for a pipeline with a manager');
      assert.equal(await icon.getAttribute('aria-pressed'), 'false');

      // Opened: a column at the right, the full height beside the stages.
      await icon.click();
      await page.locator('#task-rail .pm-head').waitFor();
      const rail = await box(page, '#task-rail'), list = await box(page, '#tasks-pane');
      assert.equal(rail.hidden, false);
      assert.ok(rail.x >= list.x + list.w - 1, `the rail is at the right of the stages: ${JSON.stringify({rail, list})}`);
      assert.ok(Math.abs(rail.y - list.y) < 2 && rail.h > 400, `the rail stands beside the stages, not under them: ${JSON.stringify(rail)}`);
      assert.ok(rail.w >= 280 && rail.w <= 360, `rail width ${rail.w}`);
      assert.ok(list.w < pane.w, 'the stages give it room');
      assert.equal(await icon.getAttribute('aria-pressed'), 'true');

      // Kept: a reload opens it again; its close button closes it.
      await page.reload();
      await page.locator('#task-rail .pm-head').waitFor();
      await page.locator('#task-rail [data-pm-hide]').click();
      assert.equal((await box(page, '#task-rail')).hidden, true, 'closed by its button');
      assert.equal(await page.evaluate(() => document.querySelector('.tasks-page')!.classList.contains('has-rail')), false);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {needs: [], hash: '#/tasks?pipeline=ops'});
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'no manager, no chat icon');
      assert.equal((await box(page, '#task-rail')).hidden, true);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      const {page, errors} = await open(browser, {viewport: {width: 390, height: 844}, needs: [], hash: '#/tasks?pipeline=product'});
      assert.equal(await page.locator('#task-manager').isVisible(), false, 'a phone has no chat icon');
      assert.equal((await box(page, '#task-rail')).hidden, true, 'nor a rail');
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('pipeline columns are its work stages sharing the width; the manager rail is closed, opens at the right: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
