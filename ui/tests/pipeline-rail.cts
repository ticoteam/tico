// A pipeline on the Tasks page, desktop (docs/pipelines-v3.md S7, S10), offline on the fake server of tasks-page.cts:
//  - the columns are the pipeline's work stages, in order (Done and Closed are not columns), and they share the width;
//  - the manager's rail is closed at first: the header's chat icon opens it as a column at the RIGHT of the stages
//    (never under them), its close button closes it, and open or closed is kept in this browser;
//  - a pipeline with no manager, or a phone, has no chat icon and no rail;
//  - a Late row and a Log line with a task open that task, by click and by Enter or Space (the rail is outside
//    #task-body, so the page's own row handler never sees them).
// Run: node scripts/ui-tests.cts pipeline-rail
import type {Page} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, PIPELINES, fixtures, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;

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
      // A late task and a Log line about another task: each row opens its task.
      const tasks = fixtures();
      tasks.find(t => t.id === 't-1')!.late = {level: 'told', since: new Date(Date.now() - 3 * 3600e3).toISOString()};
      const log = {log: [{at: new Date().toISOString(), text: 'Pushed past Build', why: 'checks green', task_id: 't-4', by: 'bot:pm'}], learned: []};
      const {page, errors} = await open(browser, {tasks, needs: [], hash: '#/tasks?pipeline=product&layout=columns', beforeGoto: (pg: Page) =>
        pg.route('**/api/v2/pipelines/product/log', r => r.fulfill({contentType: 'application/json', body: JSON.stringify(log)}))});
      await page.evaluate(() => {
        const w = window as unknown as {taskModalOpen: (id: string) => Promise<void>; opened: string[]};
        w.opened = [];
        w.taskModalOpen = async (id: string) => { w.opened.push(id); };
      });
      const opened = () => page.evaluate(() => (window as unknown as {opened: string[]}).opened.splice(0));
      if ((await page.locator('#task-manager').getAttribute('aria-pressed')) !== 'true') await page.locator('#task-manager').click();
      const late = page.locator('#task-rail .pm-row[data-task="t-1"]'), line = page.locator('#task-rail .pm-line[data-task="t-4"]');
      await late.waitFor();
      await line.waitFor();
      await late.click();
      assert.deepEqual(await opened(), ['t-1'], 'a Late row opens its task');
      await line.click();
      assert.deepEqual(await opened(), ['t-4'], 'a Log line with a task opens it');
      await late.focus();
      await page.keyboard.press('Enter');
      assert.deepEqual(await opened(), ['t-1'], 'Enter on a focused Late row opens it');
      await line.focus();
      await page.keyboard.press(' ');
      assert.deepEqual(await opened(), ['t-4'], 'Space on a focused Log line opens it');
      await page.locator('#task-rail .pm-tab[data-pm-tab="tasks"]').click();
      assert.deepEqual(await opened(), [], 'a tab is not a task');
      const row = page.locator('#task-rail .pm-body .tk-row[data-task]').first();
      await row.waitFor();
      const want = await row.getAttribute('data-task');
      await row.click();
      assert.deepEqual(await opened(), [want], 'a row in the Tasks tab opens its task');
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // Task #339: columns are at least 300px. At 1440×900 with the manager's panel open they no longer fit, so the
      // columns scroll sideways, and their scrollbar is on screen: the board fills the room under the header and is the
      // one scroller, so the wheel over a card scrolls the columns, not the page, and the header stays.
      const tasks = fixtures();
      for (let i = 0; i < 30; i++) tasks.push(task(`b-${i}`, `A long enough title for a two-line card, number ${i}`, 'build',
        {requester_id: i % 2 ? 'bot:cal' : 'bot:perf', late: {level: 'told', since: new Date(Date.now() - 3 * 3600e3).toISOString()}}));
      // Asker names as long as the board's longest today, on a full meta line (Late, the owner's face): none may end in
      // "…" at 300px; at 200px they did.
      const bots = [['pm', 'PM'], ['cal', 'Calendar Product Owner'], ['perf', 'Performance Owner']].map(([name, display_name]) =>
        ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true}));
      const {page, errors} = await open(browser, {tasks, bots, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator('#task-manager').click();
      await page.locator('#task-rail .pm-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `every column is 300px with the panel open at 1440: ${widths}`);
      const names = await page.locator('#task-body .tk-card .tk-asker .tk-name').evaluateAll(els =>
        els.map(e => ({text: e.textContent, cut: e.scrollWidth > e.clientWidth})));
      assert.ok(names.some(n => n.text === 'Calendar Product Owner'), `the long names are drawn: ${JSON.stringify(names.slice(0, 3))}`);
      assert.deepEqual(names.filter(n => n.cut), [], 'no asker name is cut');
      const board = await page.locator('#task-body .pv-cols').evaluate(el => ({bottom: el.getBoundingClientRect().bottom,
        sideways: el.scrollWidth > el.clientWidth, tall: el.scrollHeight > el.clientHeight, window: innerHeight}));
      assert.ok(board.sideways, 'the stages do not fit: the columns scroll sideways');
      assert.ok(board.tall, 'thirty cards are taller than the window');
      assert.ok(board.bottom <= board.window, `the columns' bottom, with their sideways scrollbar, is within the window: ${JSON.stringify(board)}`);
      const head = (await box(page, '.tl-head')).y;
      const card = await box(page, '#task-body .tk-card[data-task="b-0"]');
      await page.mouse.move(card.x + card.w / 2, card.y + card.h / 2);
      await page.mouse.wheel(0, 400);
      await page.waitForFunction(() => document.querySelector('#task-body .pv-cols')!.scrollTop > 0);
      assert.equal(await page.locator('#tasks-pane').evaluate(el => el.scrollTop), 0, 'the page itself does not scroll');
      assert.equal((await box(page, '.tl-head')).y, head, 'the header stays');
      assert.deepEqual(errors, []);
      await page.close();
    }
    for (const viewport of [{width: 1000, height: 800}, {width: 800, height: 800}]) {
      // Small windows with the panel open: still 300px columns, and the sideways scrollbar on screen.
      const {page, errors} = await open(browser, {viewport, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      await page.locator('#task-manager').click();
      await page.locator('#task-rail .pm-head').waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - 300) < 1), `300px columns at ${viewport.width}: ${widths}`);
      const bottom = await page.locator('#task-body .pv-cols').evaluate(el => el.getBoundingClientRect().bottom);
      assert.ok(bottom <= viewport.height, `the sideways scrollbar is on screen at ${viewport.width}: ${bottom}`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    {
      // At 1920×1080 with the panel closed the columns share the width, each at most 480px, as before.
      const {page, errors} = await open(browser, {viewport: {width: 1920, height: 1080}, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
      await page.locator('#task-body .pv-col').first().waitFor();
      const widths = await page.locator('#task-body .pv-col').evaluateAll(els => els.map(e => e.getBoundingClientRect().width));
      assert.ok(widths.every(w => Math.abs(w - widths[0]!) < 1 && w > 300 && w <= 480), `equal columns up to 480px: ${widths}`);
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
    console.log('pipeline columns are its work stages sharing the width; the manager rail is closed, opens at the right; its task rows open their task: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
