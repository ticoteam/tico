// Offline browser check of a dependency in the task window (task-modal.ts taskDependsHTML; spec "A blocked task shows as
// Blocked, never Snoozed", part 3). A blocked task lists each task it waits for under "Blocked by": its number, title,
// stage and since when, with × to take it off; Add another opens the task finder and relates the task picked with
// depends_on. The task it waits for lists, under "Blocks 4 tasks", the tasks waiting for it. A blocked task a bot also
// snoozed shows no Snoozed chip. Escaped, and without sideways scroll on a phone.
// Run: npm run build:ui && node ui/tests/task-blocked.cts
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Route = import('playwright').Route;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const tasks = Array.from({length: 12}, (_, i) => task(`t-${300 + i}`, `Errand ${300 + i}`, 'spec', {owner_id: 'human:ana', relations: {}}));
    const by = (n: number) => tasks.find(t => t.number === n)!;
    const since = '2026-11-13T05:00:00Z';
    const dep = (n: number, direction: string, stage = 'Build') => ({id: by(n).id, title: by(n).title, status: 'open', owner_id: 'bot:builder',
      direction, number: n, stage, since});
    by(304).title = 'Growth <b>synced</b> sources part 1';
    // #307 depends on #304; #304 blocks #307, #308, #310 and #311.
    by(307).relations = {depends_on: [dep(304, 'out')]};
    // A bot snoozed #307 before (or after) the dependency was written: blocked wins, so the window shows no Snoozed chip.
    Object.assign(by(307), {snooze: {by_id: 'bot:builder', why: 'Waiting for part 1 to merge', created_at: since, until: '2026-11-16T00:00:00Z'}});
    by(304).relations = {depends_on: [307, 308, 310, 311].map(n => dep(n, 'in', 'Review'))};
    const related: unknown[] = [];
    const {page, errors} = await open(browser, {hash: '#/tasks', tasks, needs: [], beforeGoto: (pg: import('playwright').Page) =>
      pg.route('**/api/v2/tasks/*/relations', (route: Route) => {
        related.push({p: new URL(route.request().url()).pathname, body: route.request().postDataJSON()});
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({ok: true})});
      })});
    const text = (sel: string) => page.locator(sel).evaluateAll(rows => rows.map(r => (r as HTMLElement).innerText.replace(/\s+/g, ' ').trim()));

    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 844});
      await page.evaluate(() => { location.hash = '#/tasks'; });
      await page.evaluate(() => { location.hash = '#/task/t-307'; });
      // A phone shows one tab at a time; the dependency lives under Info.
      if (width < 600) await page.locator('#task-modal[open]').getByRole('tab', {name: 'Info'}).click();
      const blocked = page.locator('#task-modal[open][data-task="t-307"] .task-depends');
      await blocked.waitFor();
      assert.deepEqual(await text('#task-modal[open] .task-depends'),
        ['Blocked by #304 Growth <b>synced</b> sources part 1 · Build · since Nov 13 × Add another'], `${width}px: the line, its stage and since when`);
      assert.equal(await page.locator('#task-modal[open] .task-blocks').count(), 0, 'it blocks nothing');
      assert.equal(await page.locator('#task-modal[open] .tchip-snoozed').count(), 0, `${width}px: blocked, never Snoozed`);
      assert.doesNotMatch(await page.locator('#task-modal[open]').innerText(), /Snoozed|Waiting for part 1 to merge/, `${width}px: no snooze words`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}px: no sideways scroll`);
    }

    // × takes the dependency off from the blocked task's side.
    await page.locator('#task-modal[open] .task-depends [data-drop-rel]').click();
    await page.waitForTimeout(200);
    assert.deepEqual(related.at(-1), {p: '/api/v2/tasks/t-307/relations', body: {task: 't-304', kind: 'depends_on', remove: true}});
    // Add another: the task finder, and the one picked becomes a dependency.
    await page.locator('#task-modal[open] [data-depends-add]').click();
    await page.locator('#task-modal .prop-pop [data-prop-pick="t-309"]').click();
    await page.waitForTimeout(200);
    assert.deepEqual(related.at(-1), {p: '/api/v2/tasks/t-307/relations', body: {task: 't-309', kind: 'depends_on'}});
    // The + menu offers it too.
    await page.locator('#task-modal[open] [data-related-add]').click();
    assert.equal(await page.locator('#task-modal .prop-pop [data-prop-pick="depends_on"]').innerText(), 'Blocked by');
    await page.keyboard.press('Escape');

    // The task others wait for.
    await page.evaluate(() => { location.hash = '#/tasks'; });
    await page.evaluate(() => { location.hash = '#/task/t-304'; });
    await page.locator('#task-modal[open]').getByRole('tab', {name: 'Info'}).click();
    await page.locator('#task-modal[open][data-task="t-304"] .task-blocks').waitFor();
    const blocks = await text('#task-modal[open] .task-blocks');
    assert.equal(blocks.length, 1);
    assert.match(blocks[0]!, /^Blocks 4 tasks #307 Errand 307 · Review · since Nov 13 × #308 /);
    assert.equal(await page.locator('#task-modal[open] .task-depends').count(), 0, 'it waits for nothing');

    assert.deepEqual(errors, []);
    console.log('PASS: a blocked task lists what it waits for, since when, with remove and Add another; its blocker says Blocks 4 tasks.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
