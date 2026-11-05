// The whole personal plate across boards: tab and both navigation badges agree before paging.
// Full-task rows count too; Catch up walks only answerable cards. Threads and updates have their own pages.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./tasks-page.cjs');
const now = new Date().toISOString();
const task = (id, over = {}) => ({id, kind: 'task', title: 'Review the launch plan ' + id, body: 'Please review.',
  person_ask: 'Can you approve the launch plan?', owner_id: 'human:ana', requester_id: 'bot:writer', status: 'open',
  lane: 'company', type_id: 'general', labels: [], tags: [], links: [], version: 1, created_at: now, updated_at: now, ...over});
const tasks = [
  ...Array.from({length: 6}, (_, n) => task('t-' + n, {type_id: ['dev', 'roadmap'][n] || 'general'})),
  task('t-wait', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'}),
  task('t-question', {kind: 'question', owner_id: 'bot:writer',
    ask: {id: 'q-task', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Can we launch on Thursday?'}}),
  task('t-declined', {kind: 'declined', status: 'declined', owner_id: 'bot:writer', requester_id: 'human:ana'}),
];
const foldedWait = task('t-folded', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'});
const mergedWait = task('t-merged', {kind: 'waiting', owner_id: 'bot:writer', status: 'waiting', waiting_on: 'human:ana'});
const unreadCard = task('t-unread-card', {folded: [foldedWait], merged: [mergedWait.id]});
const wholeTask = task('t-whole', {person_ask: '', body: 'Review the attachments and write a report.',
  title: 'Assigned, not started: Review the attachments', owner_id: 'bot:writer', requester_id: 'human:ana',
  assigned_not_started: true, note: 'Assigned to Writer, but no run has started.'});
const extras = [
  {id: 'a-send', kind: 'approval', what: 'send', title: 'Send the launch email', requester_id: 'bot:writer',
    created_at: now, payload: {to: ['team@acme.example'], subject: 'Launch'}},
  {id: 'q-loose', kind: 'ask', title: 'Which vendor?', requester_id: 'bot:writer', created_at: now,
    ask: {id: 'q-loose', sender_id: 'bot:writer', recipient_id: 'human:ana', body: 'Which vendor?'}},
  {id: 'stale', kind: 'stale', title: 'Stale tickets', created_at: now, rows: []},
  {id: 'tidy', kind: 'memory_tidy', title: 'Nightly memory tidy', created_at: now},
];
const threadKinds = ['thread_ask', 'thread_mention', 'thread_dm', 'thread_held', 'thread_capped'];
const items = [...tasks, foldedWait, mergedWait, ...Array.from({length: 238}, (_, n) => ({id: 'thread-' + n, kind: threadKinds[n % threadKinds.length],
  title: 'Check the thread', requester_id: 'human:sam', created_at: now})), ...extras, unreadCard,
  wholeTask, tasks[0], {id: 'update', kind: 'update', title: 'Launch progress'}];
const kinds = rows => rows.reduce((out, it) => { out[it.kind] = (out[it.kind] || 0) + 1; return out; }, {});
const foldedIds = rows => [...new Set(rows.flatMap(it => [...(it.folded || []).map(f => f.id), ...(it.merged || [])]))].sort();
const types = ['general', 'dev', 'roadmap'].map(id => ({id, name: id, steps: []}));
const count = page => page.locator('#task-tab-foryou .cnt').innerText();
const badges = page => page.locator('[data-nav="tasks"] [data-needs-badge]').evaluateAll(bs => bs.map(b => ({text: b.textContent, hidden: b.hidden})));

const plateCount = rows => {
  const folded = new Set(foldedIds(rows));
  const keys = rows.filter(it => ['task', 'question', 'waiting', 'declined', 'approval', 'ask', 'stale', 'memory_tidy'].includes(it.kind)
    && !(['task', 'question', 'waiting', 'declined'].includes(it.kind) && folded.has(it.id)))
    .map(it => (['task', 'question', 'waiting', 'declined'].includes(it.kind) ? 'task' : it.kind) + ':' + it.id);
  return new Set(keys).size;
};
const snapshot = (rows, version, limit = 50) => ({items: rows.slice(0, limit), version, count: rows.length,
  plate_count: plateCount(rows), by_kind: kinds(rows), folded_task_ids: foldedIds(rows),
  next: rows.length > limit ? version + '.' + limit : null});

// Hold one response after its request arrives, so each race has an explicit completion order.
async function holdNeedsRead(page, snapshot, more = false) {
  let arrived, release;
  const started = new Promise(done => { arrived = done; });
  const gate = new Promise(done => { release = done; });
  const body = JSON.stringify(snapshot);
  await page.route('**/api/v2/needs-you?*', async route => {
    arrived();
    await gate;
    await route.fulfill({contentType: 'application/json', body});
  }, {times: 1});
  const read = page.evaluate(more => more ? v2NeedsMore() : v2NeedsRead(), more);
  await started;
  return async () => { release(); return read; };
}

(async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const width of [390, 1440]) {
      let rows = items, version = 'count-v1', wholeReads = 0, cursorReads = 0, idsReads = 0, failCursor = false;
      const {page, errors} = await open(browser, {viewport: {width, height: 844}, types,
        hash: '#/tasks?view=list&type=roadmap&tag=absent', baseTasks: [...tasks, foldedWait, mergedWait, unreadCard, wholeTask],
        beforeGoto: async page => {
          await page.route('**/api/v2/needs-you?*', route => {
            const q = new URL(route.request().url()).searchParams;
            const facts = {version, count: rows.length, plate_count: plateCount(rows)};
            const json = body => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
            if (q.has('ids')) {
              idsReads++;
              const wanted = new Set(q.get('ids').split(','));
              return json({...facts, items: rows.filter(it => wanted.has(it.id)),
                positions: Object.fromEntries(rows.flatMap((it, n) => wanted.has(it.id) ? [[it.id, n]] : []))});
            }
            if (q.has('cursor')) {
              cursorReads++;
              if (failCursor) return route.fulfill({status: 503, contentType: 'application/json', body: '{}'});
            } else {
              wholeReads++;
              if (q.get('version') === version) return json({...facts, unchanged: true});
            }
            const offset = Number((q.get('cursor') || '').split('.').at(-1)) || 0;
            const limit = Number(q.get('limit') || 50), end = offset + limit;
            return json({...facts, items: rows.slice(offset, end), by_kind: kinds(rows), folded_task_ids: foldedIds(rows),
              next: end < rows.length ? version + '.' + end : null});
          });
        }});
      await page.waitForFunction(() => S.v2.needsVersion === 'count-v1' && TASKS_ST.activeComplete);
      await page.evaluate(() => tasksRender(TASKS_ST));
      assert.equal(cursorReads, 0, 'a refresh reads one bounded page, without walking the list');
      assert.equal(await page.evaluate(() => S.v2.needs.length), 50);
      assert.equal(await page.evaluate(() => needsAll()), false);
      assert.equal(await count(page), '15', 'eleven tasks and four other plate items across all boards, before later pages load');
      assert.deepEqual(await badges(page), [{text: '15', hidden: false}, {text: '15', hidden: false}]);
      assert.equal(await page.evaluate(() => needsQueueItems().some(it => it.id === 't-folded' || it.id === 't-merged')), false,
        'folded tasks stay out even while their parent card is unread');
      assert.equal(await page.locator('#tasks-pane [data-go-through]:visible').count(), 0, 'Catch up is on Needs you');
      const beforeRefresh = wholeReads;
      await page.evaluate(() => v2NeedsRead());
      assert.equal(wholeReads, beforeRefresh + 1);
      assert.equal(cursorReads, 0, 'an unchanged refresh never reads later pages');
      assert.equal(await count(page), '15');

      // A failed explicit page leaves the held rows and whole-list count intact.
      failCursor = true;
      assert.equal(await page.evaluate(() => v2NeedsMore()), false);
      assert.equal(await page.evaluate(() => S.v2.needs.length), 50);
      assert.equal(await count(page), '15');
      failCursor = false;

      // A pending answer on a held plate item leaves every number immediately; Undo restores it.
      await page.evaluate(() => { DEC_HANDLED.add('task:t-0'); needsListsRedraw(); });
      assert.equal(await count(page), '14');
      assert.deepEqual(await badges(page), [{text: '14', hidden: false}, {text: '14', hidden: false}]);
      await page.evaluate(() => { DEC_HANDLED.delete('task:t-0'); needsListsRedraw(); });
      assert.equal(await count(page), '15');

      // A decision on an unread page updates the number without rereading that page or the whole list.
      const base = version, before = wholeReads, beforeCursors = cursorReads;
      rows = rows.filter(it => it.id !== 'a-send'); version = 'count-v2';
      await page.evaluate(d => v2NeedsChanged(d),
        {base, version, count: rows.length, plate_count: 14, by_kind: kinds(rows), changed: [], removed: ['a-send']});
      assert.equal(wholeReads, before);
      assert.equal(cursorReads, beforeCursors);
      assert.equal(await count(page), '14');
      assert.equal(await page.evaluate(() => S.v2.needs.length), 50);

      // A changed row is mended by id. The ids response carries the whole-list plate count too.
      const changedBase = version;
      const beforeMend = await holdNeedsRead(page, snapshot(rows, version));
      rows = rows.map(it => it.id === 't-1' ? {...it, title: 'Latest launch plan'} : it); version = 'count-v3';
      await page.evaluate(d => v2NeedsChanged(d),
        {base: changedBase, version, count: rows.length, plate_count: 14, by_kind: kinds(rows), changed: ['t-1'], removed: []});
      assert.equal(await beforeMend(), null, 'a read started before a live mend cannot replace it');
      assert.equal(idsReads, 1);
      assert.equal(wholeReads, before);
      assert.equal(await page.evaluate(() => needsItem('t-1').title), 'Latest launch plan');
      assert.equal(await count(page), '14');

      // Load later pages explicitly, keeping the same number throughout.
      for (let n = 0; n < 10 && !await page.evaluate(() => needsAll()); n++) {
        const beforePage = n === 0 ? await holdNeedsRead(page, snapshot(rows, version)) : null;
        assert.equal(await page.evaluate(() => v2NeedsMore()), true);
        if (beforePage) {
          assert.equal(await beforePage(), null, 'a read started before paging cannot discard the added page');
          assert.equal(await page.evaluate(() => S.v2.needs.length), 100);
        }
        assert.equal(await count(page), '14');
      }
      assert.equal(await page.evaluate(() => needsAll()), true);
      await page.locator('#task-tab-foryou').click();
      assert.equal(await page.locator('#task-group .v').innerText(), 'Asked by');
      const asks = page.locator('#task-body [data-group="asks"]');
      assert.equal(await asks.locator('.tl-gname').textContent(), 'Approvals and questions');
      assert.equal(await asks.locator('.tl-gcount').innerText(), '3', 'only the loose question, stale check and memory tidy');
      assert.equal(await asks.locator('.tl-row').count(), 3);
      assert.equal(await page.locator('#task-body .tl-row').count(), 14, 'one row per plate item');
      assert.equal(await page.locator('#task-body [data-task-key="tt-0"]').count(), 1, 'duplicate task only once');
      const declined = page.locator('#task-body [data-task-key="tt-declined"]');
      assert.equal(await declined.count(), 1, 'a recent decline stays on its requester\'s plate');
      assert.equal(await declined.getAttribute('data-need-card'), null, 'a decline opens the full task');
      const whole = page.locator('#task-body [data-task-key="tt-whole"]');
      assert.equal(await page.evaluate(() => taskNeedsViewer(needsItem('t-whole'))), false,
        'the escalation has no question, ownership or wait on the manager');
      assert.equal(await whole.count(), 1, 'Asked by includes the counted bot-owned escalation that opens the full task');
      assert.equal(await whole.getAttribute('data-need-card'), null);
      await page.locator('#task-group').click();
      await page.locator('[data-pick-group="need"]').click();
      assert.equal(await page.locator('#task-body .tl-row').count(), 14, 'What it takes shows the same counted items');
      assert.equal(await whole.count(), 1);
      await page.locator('#task-group').click();
      await page.locator('[data-pick-group="requester"]').click();
      assert.equal(await page.locator('.tl-type-search').isHidden(), true);
      assert.equal(await page.locator('.tl-filters').isHidden(), true);
      assert.equal(await page.evaluate(() => TASKS_ST.type), 'roadmap');
      assert.deepEqual(await badges(page), [{text: '14', hidden: false}, {text: '14', hidden: false}]);
      const catchUp = page.locator('#tasks-pane [data-go-through]:visible');
      assert.equal(await catchUp.innerText(), 'Catch up');
      await catchUp.click();
      assert.equal(await page.evaluate(() => decideLive(DEC).length), 12, 'Catch up skips the full-task rows');
      assert.equal(await page.evaluate(() => decideLive(DEC).includes('task:t-whole')), false);
      assert.equal(await page.evaluate(() => decideLive(DEC).includes('task:t-declined')), false);
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !DEC && !history.state?.needCard);

      // Non-card items use the same immediate answer/Undo count adjustment.
      await page.evaluate(() => { DEC_HANDLED.add('task:t-whole'); needsListsRedraw(); });
      assert.equal(await count(page), '13');
      assert.equal(await asks.locator('.tl-gcount').innerText(), '3', 'a full-task change leaves the extras count alone');
      assert.equal(await page.locator('#task-body .tl-row').count(), 13);
      assert.deepEqual(await badges(page), [{text: '13', hidden: false}, {text: '13', hidden: false}]);
      await page.evaluate(() => { DEC_HANDLED.delete('task:t-whole'); needsListsRedraw(); });
      assert.equal(await count(page), '14');

      for (const theme of ['light', 'dark']) {
        await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, theme + ': no sideways page scroll');
        if (process.env.TICO_UI_SHOTS) {
          await page.waitForFunction(() => {
            const count = document.querySelector('#task-tab-foryou .cnt');
            return count && !count.classList.contains('m-ticking') && !count.getAnimations({subtree: true}).length;
          });
          await page.screenshot({path: require('node:path').join(process.env.TICO_UI_SHOTS,
            `needs-you-${width === 390 ? 'phone' : 'desktop'}-${theme}.png`)});
        }
      }
      await page.setViewportSize({width: width === 390 ? 1440 : 390, height: 844});
      assert.equal(await count(page), '14');

      // Paging started before a save must not invalidate its refresh, even if the old page lands first.
      for (const newerFirst of [false, true]) {
        rows = [wholeTask, ...items.filter(it => threadKinds.includes(it.kind)).slice(0, 50)];
        version = 'count-page-base-' + newerFirst;
        await page.evaluate(d => needsTake(d), snapshot(rows, version));
        assert.equal(await count(page), '1');
        assert.equal(await page.evaluate(() => needsAll()), false);
        const older = await holdNeedsRead(page, {...snapshot(rows, version), items: rows.slice(50), next: null}, true);
        rows = rows.filter(it => it.id !== wholeTask.id); version = 'count-page-saved-' + newerFirst;
        const newer = await holdNeedsRead(page, snapshot(rows, version));
        if (newerFirst) {
          assert.ok(await newer());
          assert.equal(await older(), false);
        } else {
          assert.equal(await older(), false, 'an older page is discarded once the post-save read starts');
          assert.ok(await newer(), 'the old page cannot invalidate the post-save read');
        }
        assert.equal(await page.evaluate(() => S.v2.needsVersion), version);
        assert.equal(await count(page), '0');
        assert.equal(await page.locator('#task-body [data-task-key="tt-whole"]').count(), 0);
        assert.deepEqual(await badges(page), [{text: '0', hidden: true}, {text: '0', hidden: true}]);
      }

      // With no live event, the newest overlapping read drops completed full-task work in either response order.
      for (const newerFirst of [false, true]) {
        rows = [wholeTask]; version = 'count-overlap-base-' + newerFirst;
        await page.evaluate(() => v2Refresh());
        assert.equal(await count(page), '1');
        const older = await holdNeedsRead(page, snapshot(rows, 'count-overlap-older-' + newerFirst));
        rows = []; version = 'count-overlap-newer-' + newerFirst;
        const newer = await holdNeedsRead(page, snapshot(rows, version));
        if (newerFirst) {
          assert.ok(await newer());
          assert.equal(await older(), null, 'an older response cannot overwrite a newer successful read');
        } else {
          assert.ok(await older());
          assert.ok(await newer(), 'an older successful read cannot invalidate the newer read');
        }
        assert.equal(await page.evaluate(() => S.v2.needsVersion), version);
        assert.equal(await count(page), '0');
        assert.equal(await page.locator('#task-body .tl-row').count(), 0, 'the completed full-task row leaves with its count');
        assert.deepEqual(await badges(page), [{text: '0', hidden: true}, {text: '0', hidden: true}]);
      }

      // A list holding only full-task work has a row and count, but no Catch up button.
      rows = [wholeTask]; version = 'count-whole-task';
      await page.evaluate(() => v2Refresh());
      assert.equal(await count(page), '1');
      assert.equal(await page.locator('#task-body .tl-row').count(), 1);
      assert.equal(await page.locator('#tasks-pane [data-go-through]:visible').count(), 0);
      assert.deepEqual(await badges(page), [{text: '1', hidden: false}, {text: '1', hidden: false}]);

      // The badges still update off Tasks; zero hides them and removes Catch up.
      await page.evaluate(() => { location.hash = '#/updates'; });
      await page.waitForFunction(() => S.route === '#/updates');
      rows = items.filter(it => threadKinds.includes(it.kind)); version = 'count-empty';
      await page.evaluate(() => v2Refresh());
      assert.deepEqual(await badges(page), [{text: '0', hidden: true}, {text: '0', hidden: true}]);
      await page.evaluate(() => { location.hash = '#/tasks?view=foryou'; });
      await page.waitForFunction(() => TASKS_ST?.view === 'foryou' && !TASKS_ST.loading
        && document.querySelector('#task-tab-foryou .cnt')?.textContent === '0');
      assert.equal(await count(page), '0');
      assert.equal(await page.locator('#task-body .tl-row').count(), 0);
      assert.equal(await page.locator('#tasks-pane [data-go-through]:visible').count(), 0);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log('Needs you: whole plate count before paging, requester escalations, full-task rows, ordered reads, live mending, Undo, desktop and phone badges, zero');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
