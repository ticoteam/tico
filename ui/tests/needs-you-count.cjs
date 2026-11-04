// Tasks counts its filtered tasks and asks/approvals, while thread attention belongs to Inbox. A paged attention
// list must not replace that count with its broader total, or scrolling and viewport size change what Needs you means.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const now = new Date().toISOString();
const task = (id, over = {}) => ({id, kind: 'task', title: 'Review the launch plan ' + id, body: 'Please review.',
  owner_id: 'human:ana', requester_id: 'bot:writer', status: 'open', lane: 'company', type_id: 'general',
  labels: [], tags: [], links: [], version: 1, created_at: now, updated_at: now, ...over});
const tasks = [
  ...Array.from({length: 6}, (_, n) => task('t-' + n)),
  task('t-wait', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'}),
  task('t-question', {kind: 'question', owner_id: 'bot:writer', open_asks: 7,
    ask: {id: 'q-task', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Which launch date?'}}),
];
const foldedWait = task('t-folded', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'});
const mergedWait = task('t-merged', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'});
const unreadCard = task('t-unread-card', {folded: [foldedWait], merged: [mergedWait.id]});
const extras = [
  {id: 'a-send', kind: 'approval', what: 'send', title: 'Send the launch email', requester_id: 'bot:writer',
    created_at: now, payload: {to: ['team@acme.example'], subject: 'Launch'}},
  {id: 'q-loose', kind: 'ask', title: 'Which vendor?', requester_id: 'bot:writer', created_at: now,
    ask: {id: 'q-loose', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Which vendor?'}},
];
const threadKinds = ['thread_ask', 'thread_mention', 'thread_dm', 'thread_held', 'thread_capped'];
const items = [...tasks, ...Array.from({length: 238}, (_, n) => ({id: 'thread-' + n, kind: threadKinds[n % threadKinds.length],
  title: 'Check the thread', requester_id: 'human:sam', created_at: now})), ...extras, unreadCard];
const kinds = rows => rows.reduce((out, it) => { out[it.kind] = (out[it.kind] || 0) + 1; return out; }, {});
const foldedIds = rows => [...new Set(rows.flatMap(it => [...(it.folded || []).map(f => f.id), ...(it.merged || [])]))].sort();
const tab = page => page.locator('#task-tab-foryou');
const count = page => tab(page).locator('.cnt').innerText();

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    const counts = [];
    for (const width of [390, 1440]) {
      let rows = items, version = 'count-v1';
      const {page, errors} = await open(browser, {viewport: {width, height: 844}, hash: '#/tasks?view=list',
        baseTasks: [...tasks, foldedWait, mergedWait, unreadCard],
        extraTasks: [task('t-other-person', {owner_id: 'human:sam'}), task('t-bot', {owner_id: 'bot:writer'}),
          task('t-done', {status: 'done'}), task('t-closed', {status: 'closed'}),
          task('t-snoozed', {holds: [{kind: 'snoozed', until: '2099-01-01T00:00:00Z'}]}),
          task('t-dev', {type_id: 'dev'})],
        beforeGoto: async page => {
          await page.route('**/api/v2/needs-you?*', route => {
            const q = new URL(route.request().url()).searchParams;
            if (q.has('ids')) {
              const wanted = new Set(q.get('ids').split(','));
              return route.fulfill({contentType: 'application/json', body: JSON.stringify({version, count: rows.length,
                items: rows.filter(it => wanted.has(it.id)),
                positions: Object.fromEntries(rows.flatMap((it, n) => wanted.has(it.id) ? [[it.id, n]] : []))})});
            }
            const offset = Number((q.get('cursor') || '').split('.').at(-1)) || 0;
            const limit = Number(q.get('limit') || 50), end = offset + limit;
            return route.fulfill({contentType: 'application/json', body: JSON.stringify({items: rows.slice(offset, end),
              count: rows.length, by_kind: kinds(rows), folded_task_ids: foldedIds(rows), version,
              next: end < rows.length ? version + '.' + end : null})});
          });
        }});
      await page.waitForFunction(() => S.v2.needsVersion === 'count-v1' && TASKS_ST.activeComplete);
      assert.equal(await page.evaluate(() => S.v2.needsCount), 249);
      assert.equal(await page.evaluate(() => !!S.v2.needsNext), true, 'the attention list still has unread pages');
      assert.equal(await page.evaluate(() => !!needsItem('t-unread-card')), false, 'the folding card is still unread');
      assert.equal(await count(page), '11', width + ': nine task cards, one approval and one loose question, without folded duplicates');
      assert.equal(await tab(page).getAttribute('aria-label'), 'Needs you, 11');
      counts.push(await count(page));

      // Explicit paging keeps this independent of where the viewport puts the scroll observer's Show more button.
      const pagedCounts = await page.evaluate(async () => {
        const counts = [];
        while (await v2NeedsMore()) { tasksRender(TASKS_ST); counts.push(document.querySelector('#task-tab-foryou .cnt').textContent); }
        return counts;
      });
      assert.ok(pagedCounts.length > 1);
      assert.ok(pagedCounts.every(n => n === '11'), width + ': paging never changes the count scope');
      await tab(page).click();
      assert.equal(await page.locator('#task-body .tl-row').count(), 11, 'the complete list agrees with its tab');
      await page.evaluate(() => { DEC_HANDLED.add('approval:a-send'); tasksRender(TASKS_ST); });
      assert.equal(await count(page), '10', 'an answer leaves the count before the server replies');
      await page.evaluate(() => { DEC_HANDLED.delete('approval:a-send'); tasksRender(TASKS_ST); });
      assert.equal(await count(page), '11', 'Undo restores the count');

      // A live decision can remove an approval beyond the pages held: update its total without rereading every page.
      await page.locator('#task-tab-list').click();
      await page.evaluate(() => { S.v2.needs = S.v2.needs.slice(0, 50); S.v2.needsNext = needsCursor(S.v2.needsVersion, 50); });
      rows = rows.filter(it => it.id !== 'a-send'); version = 'count-v2';
      await page.evaluate(async ({by_kind, folded_task_ids}) => {
        await v2NeedsChanged({base: 'count-v1', version: 'count-v2', count: 248, by_kind, folded_task_ids, changed: [], removed: ['a-send']});
        tasksRender(TASKS_ST);
      }, {by_kind: kinds(rows), folded_task_ids: foldedIds(rows)});
      assert.equal(await count(page), '10', width + ': live changes keep the whole-list extras and folding');

      // The unread card can stop representing these tasks and fold them again without ever loading into the list.
      for (const folded of [false, true]) {
        const base = version;
        version = folded ? 'count-v4' : 'count-v3';
        rows = rows.filter(it => ![foldedWait.id, mergedWait.id].includes(it.id)).map(it => it.id === unreadCard.id ? {...unreadCard,
          folded: folded ? [foldedWait] : [], merged: folded ? [mergedWait.id] : []} : it);
        if (!folded) rows = [...rows, foldedWait, mergedWait];
        await page.evaluate(async d => { await v2NeedsChanged(d); tasksRender(TASKS_ST); },
          {base, version, count: rows.length, by_kind: kinds(rows), folded_task_ids: foldedIds(rows),
            changed: [unreadCard.id, ...(!folded ? [foldedWait.id, mergedWait.id] : [])],
            removed: folded ? [foldedWait.id, mergedWait.id] : []});
        assert.equal(await page.evaluate(() => !!needsItem('t-unread-card')), false);
        assert.equal(await count(page), folded ? '10' : '12', width + ': live folding follows the whole list');
      }
      await page.setViewportSize({width: width === 390 ? 1440 : 390, height: 844});
      assert.equal(await count(page), '10', 'resizing keeps the same source');
      assert.deepEqual(errors, []);
      await page.close();
    }
    assert.deepEqual(counts, ['11', '11'], 'phone and desktop agree');
    console.log('Needs you count: phone and desktop agree through paging, resizing, live decisions and unread folding');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
