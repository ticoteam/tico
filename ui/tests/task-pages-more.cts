// Offline browser check that the lists past the Tasks page read past their first page too (task-api.ts taskPageNext):
// - a person's page holds the tasks they own 500 at a time: More reads on from where the server's last row was, so a task
//   removed live meanwhile skips nothing, and nothing is read again for a live event;
// - the task window's Related picker shows a first page of open tasks; when the server holds more, what is typed in its
//   find box is searched there (GET /api/v2/tasks?q=), and the task found is the one related;
// - a merge order (relations.after, backend/merge_order.py): the later task lists "After #N" with the earlier task's
//   stage and the earlier task "Before #M", read-only and escaped, without scrolling sideways on a phone.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, task} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Route = import('playwright').Route;
declare function liveEmit(topic: string, data: unknown): void;

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    // Sam owns 520 tasks, oldest first; 260 more open tasks are Ana's.
    const at = (i: number) => new Date(Date.UTC(2026, 9, 1, 0, i)).toISOString();
    const tasks = [
      ...Array.from({length: 520}, (_, i) => task(`s-${i + 1}`, `Chore ${i + 1}`, 'spec', {owner_id: 'human:sam', created_at: at(i)})),
      ...Array.from({length: 260}, (_, i) => task(`t-${i + 1}`, `Errand ${i + 1}`, 'spec', {owner_id: 'human:ana', created_at: at(1000 + i), relations: {}})),
    ];
    // Errand 2 merges after Errand 3 (in Merge) and before Errand 4, whose title holds markup.
    const rel = (t: (typeof tasks)[number], direction: string) => ({id: t.id, title: t.title, status: 'open', owner_id: 'bot:builder', direction, number: t.number, stage: 'Merge'});
    const [two, three, four] = [tasks[521]!, tasks[522]!, tasks[523]!];
    four.title = 'Errand <b>4</b>';
    two.relations = {after: [rel(three, 'out'), rel(four, 'in')]};
    const related: unknown[] = [];
    const {page, errors, reads} = await open(browser, {hash: '#/tasks', tasks, needs: [], beforeGoto: (pg: import('playwright').Page) =>
      pg.route('**/api/v2/tasks/*/relations', (route: Route) => {
        related.push({p: new URL(route.request().url()).pathname, body: route.request().postDataJSON()});
        return route.fulfill({contentType: 'application/json', body: JSON.stringify({task: tasks[520]})});
      })});

    // ---- a person's page
    await page.evaluate(() => { location.hash = '#/person/sam'; });
    const box = page.locator('#person-tasks');
    await box.locator('.tk-row, .v2-row, [data-open-task], a').first().waitFor();
    await page.locator('#person-task-more button').waitFor();
    const count = () => page.evaluate(() => (document.querySelector('#person-tasks')!.textContent!.match(/Chore \d+/g) || []).length);
    assert.equal(await count(), 500);
    const before = reads.length;
    // Chore 3 is taken away live: off the list in place, nothing read.
    tasks.splice(tasks.findIndex(t => t.id === 's-3'), 1);
    await page.evaluate(() => liveEmit('tasks', {id: 's-3', kind: 'delete', kinds: ['delete'], gone: true}));
    await page.waitForFunction(() => !/Chore 3\b/.test(document.querySelector('#person-tasks')!.textContent!));
    await page.waitForTimeout(300);
    assert.equal(reads.length, before, 'no list read for a live event');
    await page.locator('#person-task-more button').click();
    await page.waitForFunction(() => !document.querySelector('#person-task-more'));
    const more = reads.slice(before).find(u => u.searchParams.get('owner') === 'human:sam')!;
    assert.equal(more.searchParams.get('offset'), '499', 'reads on after the 499 rows held up to the last one the server gave');
    assert.equal(await count(), 519);
    assert.match(await box.innerText(), /Chore 520/);

    // ---- the Related picker
    await page.evaluate(() => { location.hash = '#/task/t-1'; });
    // An empty Related section is under ⋯ › Activity and links (task #409).
    await page.locator('#task-modal[open][data-task="t-1"] [data-task-more]').click();
    await page.locator('#task-modal .prop-pop').getByRole('menuitem', {name: 'Activity and links'}).click();
    await page.locator('#task-modal[open][data-task="t-1"] [data-related-add]').click();
    await page.locator('#task-modal .prop-pop [data-prop-pick="related"]').click();
    const find = page.locator('#task-modal .prop-pop .tl-menu-q');
    await find.waitFor();
    assert.ok(await page.locator('#task-modal .prop-pop .tl-mi').count() <= 200, 'a first page');
    const typed = reads.length;
    await find.fill('Errand 250');
    const hit = page.locator('#task-modal .prop-pop [data-prop-pick="t-250"]');
    await hit.waitFor();
    const searched = reads.slice(typed).find(u => u.searchParams.get('q'))!;
    assert.equal(searched.searchParams.get('q'), 'Errand 250');
    assert.equal(searched.searchParams.get('status'), 'open');
    await hit.click();
    await page.waitForTimeout(200);
    assert.deepEqual(related.at(-1), {p: '/api/v2/tasks/t-1/relations', body: {task: 't-250', kind: 'related'}});

    // ---- a merge order, at desktop width and on a phone
    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height: 844});
      await page.evaluate(() => { location.hash = '#/tasks'; });
      await page.evaluate(() => { location.hash = '#/task/t-2'; });
      // A phone shows Related, with its merge order, in the task window's Info tab (task #409).
      if (width <= 390) await page.locator('#task-modal[open][data-task="t-2"] [data-task-tab="info"]').click();
      const order = page.locator('#task-modal[open][data-task="t-2"] .task-order');
      await order.first().waitFor();
      assert.deepEqual(await order.evaluateAll(rows => rows.map(r => (r as HTMLElement).innerText.replace(/\s+/g, ' ').trim())),
        ['After #3 Errand 3 · Merge', 'Before #4 Errand <b>4</b> · Merge'], `${width}px`);
      assert.equal(await order.locator('[data-drop-rel]').count(), 0, 'set with tico task after, not removed here');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${width}px: no sideways scroll`);
    }

    assert.deepEqual(errors, []);
    console.log('PASS: a person\'s tasks read on with More; the Related picker searches the server past its first page; a merge order shows on its task.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
