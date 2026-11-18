// Flow on a 390 px phone, on an @ in a task with nine documents (ui/app/decide.ts decideTabsPaint, task-docs.ts
// docTabsHTML/docTabsKeys, prop-menu.ts): the tab row folds as the task window's does: Chat, the newest document and
// ⋯ for the rest. ⋯ opens the documents menu with Flow's own parts loaded (not the task window's), and a document
// picked takes the document place, selected. No page errors. Fixtures only (tasks-page.cts).
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open, fixtures, needsOf} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
type Need = import('./tasks-page.cts').Need;
declare function decideOpen(id?: string): boolean;

const AT = '2026-11-18T09:00:00Z';
const PRS = ['pr4233 cce40e0', 'pr4222 8f530e1', 'pr4196 4ddc9e9', 'pr4193 6b8267d', 'pr4082 17428ac', 'pr3913 4f3d9a2', 'pr3780 f654a67', 'pr3765 b1b128e'];
const doc = (name: string, i: number) => ({name, kind: 'markdown', file_id: `file-q${i}`, version: 1, by: 'bot:writer', at: AT, versions: [{n: 1, by: 'bot:writer', at: AT}]});
const DOCS = [doc('Review queue', 9), ...PRS.map((pr, i) => doc(`Queue 2026 11 18 ${pr}`, i))];

function world() {
  const tasks = fixtures();
  Object.assign(tasks.find(t => t.id === 't-1')!, {documents: DOCS});
  const ask = needsOf(tasks).find(n => n.kind === 'ask')!;
  const said = 'Ana, can you look at the queue?';
  const mention: Need = {kind: 'mention', id: 'm-sam', message_id: 'm-sam', conversation_id: 'c-t-1', root_id: 'c-t-1', task_id: 't-1',
    from_id: 'human:sam', line: said, since: AT, task: {...ask.task, documents: DOCS} as NonNullable<Need['task']>};
  return {tasks, needs: [mention], comments: {'t-1': [{id: 'm-sam', kind: 'say', sender_id: 'human:sam', body: said, created_at: AT, refs: {task: 't-1'}}]}};
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const {page, errors} = await open(browser, {...world(), viewport: {width: 390, height: 844}, theme: 'dark'});
    try {
      await page.evaluate(() => { decideOpen('m-sam'); });
      const row = page.locator('#need-card[open] .dc-tabs .doc-tabs');
      await row.waitFor();
      const shown = () => row.locator('[role="tab"]:not([hidden])').allInnerTexts().then(t => t.map(x => x.trim()));
      assert.deepEqual(await shown(), ['Chat', 'Review queue'], 'Chat and the newest document');
      assert.equal(await row.locator('[data-doc-more]').getAttribute('aria-label'), '9 documents', '⋯ for the rest');
      assert.equal(await row.evaluate(r => r.scrollWidth <= r.clientWidth + 1), true, 'the row fits: it does not scroll');
      await row.locator('[data-doc-more]').click();
      const menu = page.locator('#need-card .prop-pop:popover-open');
      await menu.waitFor();
      assert.deepEqual(await menu.locator('.tl-mi').allInnerTexts().then(t => t.map(x => x.replace('✓', '').trim())), ['Review queue', ...PRS], '⋯ lists every document by its short name');
      await menu.locator('.tl-mi', {hasText: 'pr4082 17428ac'}).click();
      await menu.waitFor({state: 'hidden'});
      assert.deepEqual(await shown(), ['Chat', 'pr4082 17428ac'], 'the one picked takes the document place');
      assert.equal(await row.getByRole('tab', {name: 'pr4082 17428ac'}).getAttribute('aria-selected'), 'true', 'selected');
      assert.deepEqual(errors, [], 'no page errors');
    } finally { await page.close(); }
    console.log('PASS: Flow on a phone folds nine documents behind ⋯, and ⋯ opens and picks with only Flow\'s parts loaded.');
  } finally { await browser.close(); }
})().catch((e: unknown) => { console.error(e); process.exit(1); });
