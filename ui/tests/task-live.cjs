// Concurrent task drafts, versions, focus and authorized-place lifecycle in the real app bundle.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const {open} = require('./task-code.cjs');
const gate = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; };
(async () => {
 const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined});
 const pages = [];
 try {
  for (const width of [390, 1440]) {
   let readHold, readStarted, task, events = [], sessions = new Map(), writes = [], hold, fail = false, inflight = 0, max = 0;
   const viewers = [], viewerErrors = [];
   const emit = async data => {
    for (const page of viewers) if (!page.isClosed()) await page.evaluate(data => {
     for (const source of window.taskSources || []) if (!source.closed) source.dispatchEvent(new MessageEvent(data.topic, {data: JSON.stringify(data.body)}));
    }, data);
   };
   for (const [viewer, viewport] of [['ana', width], ['sam', width === 390 ? 1440 : 390]]) {
    const {page, data, errors} = await open(browser, {at: '#/task/t-checkout', viewport: {width: viewport, height: 900}});
    pages.push(page); viewers.push(page); viewerErrors.push(errors);
    await page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor();
    task ||= {...data.tasks[0], title: 'Review the launch', body: 'Initial details', links: [], children_summary: undefined};
    await page.evaluate(viewer => {
     taskLiveStop(taskModal());
     S.me.id = viewer;
     window.taskSources = [];
     window.EventSource = class extends EventTarget {
      constructor(url) { super(); this.url = url; taskSources.push(this); }
      close() { this.closed = true; }
     };
    }, viewer);
    await page.route('**/api/v2/presence**', async route => {
     const body = route.request().method() === 'POST' ? route.request().postDataJSON() : null;
     if (body) { if (body.gone) sessions.delete(body.session_id); else sessions.set(body.session_id, viewer); }
     const actors = [...new Set(sessions.values())].map(id => ({actor: 'human:' + id, name: id === 'ana' ? 'Ana' : 'Sam'}));
     await route.fulfill({json: {actors}});
    });
    await page.route('**/api/v2/tasks/' + task.id, async route => {
     if (route.request().method() === 'GET') { if (viewer === 'ana' && readHold) { const wait = readHold; readHold = null; readStarted.resolve(); await wait.promise; } return route.fulfill({json: {task, events, children: [], comments: []}}); }
     const patch = route.request().postDataJSON();
     writes.push({viewer, patch}); if (viewer === 'ana') { inflight++; max = Math.max(max, inflight); }
     if (viewer === 'ana' && hold) { const wait = hold; hold = null; await wait.promise; }
     if (viewer === 'ana') inflight--;
     if (fail && viewer === 'ana') { fail = false; return route.fulfill({status: 500, json: {error: 'Offline'}}); }
     if (patch.version !== task.version) return route.fulfill({status: 409, json: {error: {code: 'version_conflict', detail: 'Task changed'}}});
     Object.assign(task, patch, {version: task.version + 1});
     for (const field of Object.keys(patch).filter(k => k !== 'version')) events.push({field, actor: 'human:' + viewer, created_at: new Date().toISOString()});
     await route.fulfill({json: {task}});
    });
    await page.evaluate(async task => { await taskModalShow(task); }, task);
    await page.waitForFunction(() => !!taskModal().taskLive?.shared);
    await page.locator('details.task-orig').filter({has: page.locator('[data-task-edit-body]')}).evaluateAll(rows => rows.forEach(el => { el.open = true; }));
    assert.deepEqual(errors, []);
   }
   const [a, b] = viewers;
   let listReads = 0;
   for (const page of viewers) page.on('request', request => {
    if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/v2/tasks') listReads++;
   });
   const update = () => emit({topic: 'tasks', body: {task_id: task.id, kinds: ['update']}});
   const title = a.locator('#task-modal .tmodal-title');
   const fillTitle = async value => { await title.click(); await title.fill(value); };
   const next = gate(); hold = next;
   await fillTitle('First title');
   await a.waitForFunction(() => !!taskModal().taskLive.draft.flight);
   await fillTitle('Final title');
   await title.evaluate(el => { const selection = getSelection(); selection.setBaseAndExtent(el.firstChild, 2, el.firstChild, 7); });
   const caret = () => title.evaluate(el => [document.activeElement === el, getSelection().anchorOffset, getSelection().focusOffset]);
   await b.getByRole('button', {name: 'Edit details', exact: true}).click();
   await b.getByRole('textbox', {name: 'Details', exact: true}).fill('Sam changed only details');
   await b.locator('[data-task-more]').click(); await b.keyboard.press('Escape');
   await b.waitForFunction(() => !taskModal().taskLive.draft.flight && !Object.keys(taskModal().taskLive.draft.patch).length);
   await update();
   await a.waitForFunction(() => taskModal().taskLive.draft.base.body === 'Sam changed only details');
   assert.equal(await title.textContent(), 'Final title');
   assert.deepEqual(await caret(), [true, 2, 7], 'live redraw preserves the actual title caret');
   next.resolve();
   await a.locator('[data-task-edit-error]:not([hidden])').waitFor();
   assert.equal(await title.textContent(), 'Final title');
   assert.deepEqual(await caret(), [true, 2, 7]);
   await a.getByRole('button', {name: 'Retry', exact: true}).click();
   await a.waitForFunction(() => !taskModal().taskLive.draft.error && !taskModal().taskLive.draft.flight);
   assert.equal(task.title, 'Final title'); assert.equal(task.body, 'Sam changed only details');
   assert.deepEqual(Object.keys(writes.filter(w => w.viewer === 'ana').at(-1).patch).sort(), ['title', 'version']);
   await a.getByText('You edited · just now', {exact: true}).waitFor();
   assert.equal(listReads, 0, 'autosave and task invalidations never reread the task list');
   // A slow successful save coalesces the later title, still sharing the dialog's property queue.
   const slow = gate(); hold = slow;
   await fillTitle('Slow title');
   await a.waitForFunction(() => !!taskModal().taskLive.draft.flight);
   await fillTitle('Coalesced title');
   const before = writes.length;
   await a.evaluate(() => { window.propertySave = taskSaveQueued(taskModal(), taskModal().dataset.task, {due: ''}, false, 'due'); });
   assert.equal(writes.length, before, 'property writes wait behind autosave');
   slow.resolve();
   await a.evaluate(() => propertySave);
   // The property update advances the version; the draft keeps its newer words for explicit retry if needed.
   await a.waitForFunction(() => !taskModal().taskLive.draft.flight);
   if (await a.locator('[data-task-edit-error]').isVisible()) await a.getByRole('button', {name: 'Retry', exact: true}).click();
   await a.waitForFunction(() => !taskModal().taskLive.draft.error && !taskModal().taskLive.draft.flight);
   assert.equal(task.title, 'Coalesced title'); assert.equal(max, 1);
   // Failed writes keep only their dirty field; untouched focused details adopt the 409 current values without moving the caret.
   fail = true;
   await fillTitle('Keep these words'); await title.evaluate(el => el.blur());
   await a.locator('[data-task-edit-error]:not([hidden])').waitFor();
   await a.locator('details.task-orig').filter({has: a.locator('[data-task-edit-body]')}).evaluateAll(rows => rows.forEach(el => { el.open = true; }));
   await a.getByRole('button', {name: 'Edit details', exact: true}).click();
   const details = a.getByRole('textbox', {name: 'Details', exact: true});
   await details.evaluate(el => el.setSelectionRange(2, 5));
   task.body = 'Remote details now'; task.version++;
   await update();
   await a.waitForFunction(() => taskModal().taskLive.draft.base.body === 'Remote details now');
   assert.equal(await details.inputValue(), 'Sam changed only details', 'ordinary invalidations leave focused fields alone');
   // This deadline change has no event: the 409 read must update untouched task properties too.
   task.due_at = '2026-12-01T12:00:00Z'; task.version++;
   await a.locator('[data-task-edit-retry]').evaluate(el => el.click());
   await a.locator('[data-task-edit-current]:not([hidden])').waitFor();
   await a.waitForFunction(() => document.querySelector('#task-modal [data-prop-date]').value === '2026-12-01');
   assert.equal(await details.inputValue(), 'Remote details now');
   assert.deepEqual(await details.evaluate(el => [document.activeElement === el, el.selectionStart, el.selectionEnd]), [true, 2, 5]);
   assert.equal(await title.textContent(), 'Keep these words');
   await a.locator('[data-task-edit-retry]').evaluate(el => el.click());
   await a.waitForFunction(() => !taskModal().taskLive.draft.error && !taskModal().taskLive.draft.flight);
   assert.equal(task.body, 'Remote details now');
   const bodyWrite = gate(); hold = bodyWrite;
   await details.fill('Only my details changed');
   await a.waitForFunction(() => !!taskModal().taskLive.draft.flight);
   await title.evaluate(el => { el.click(); el.focus(); getSelection().setBaseAndExtent(el.firstChild, 2, el.firstChild, 5); });
   task.title = 'Remote title now'; task.version++;
   await update();
   await a.waitForFunction(() => taskModal().taskLive.draft.base.title === 'Remote title now');
   assert.equal(await title.textContent(), 'Keep these words');
   bodyWrite.resolve();
   await a.locator('[data-task-edit-current]:not([hidden])').waitFor();
   assert.equal(await title.textContent(), 'Remote title now');
   assert.deepEqual(await caret(), [true, 2, 5], '409 adopts the untouched title without moving its selection');
   await a.locator('[data-task-edit-retry]').evaluate(el => el.click());
   await a.waitForFunction(() => !taskModal().taskLive.draft.error && !taskModal().taskLive.draft.flight);
   assert.equal(task.title, 'Remote title now'); assert.equal(task.body, 'Only my details changed');
   assert.deepEqual(Object.keys(writes.filter(w => w.viewer === 'ana').at(-1).patch).sort(), ['body', 'version']);
   // Presence uses the scoped subscription and per-window leave; a stale stream cannot close a new task.
   await emit({topic: 'presence', body: {place: 'task:' + task.id}});
   await a.getByText('Sam is here', {exact: true}).waitFor();
   const urls = await a.evaluate(() => taskSources.map(s => s.url));
   assert(urls.some(url => { const q = new URL(url, 'https://tico-ui.test').searchParams; return q.get('task') === task.id && q.get('place') === 'task:' + task.id; }));
   await b.evaluate(() => taskModal().close());
   await b.waitForFunction(() => !taskModal().taskLive);
   await emit({topic: 'presence', body: {place: 'task:' + task.id}});
   await a.waitForFunction(() => !document.querySelector('[data-task-presence]').textContent);
   assert.equal(sessions.size, 1);
   const delayed = gate(); readHold = delayed; readStarted = gate();
   await a.evaluate(() => { window.leavingLive = taskModal().taskLive; });
   await update(); await readStarted.promise;
   const lateWrite = gate(); hold = lateWrite;
   await fillTitle('Words from a departed editor');
   await a.waitForFunction(() => !!leavingLive.draft.flight);
   await emit({topic: 'tasks', body: {task_id: task.id, gone: true}});
   await a.waitForFunction(() => !taskModal().open && !taskModal().taskLive);
   await a.evaluate(id => { TASKS_ST.tasks = TASKS_ST.tasks.filter(task => task.id !== id); }, task.id);
   delayed.resolve(); lateWrite.resolve();
   await a.waitForFunction(() => !leavingLive.reading && !leavingLive.draft.flight);
   assert.equal(await a.evaluate(id => TASKS_ST.tasks.some(task => task.id === id), task.id), false, 'a late save cannot reinsert a revoked task');
   assert.equal(await a.evaluate(() => TASK_EDIT_DRAFTS.size), 0);
   await b.waitForFunction(async () => !(await get('/v2/presence?place=task:t-checkout', {fresh: true})).actors.length);
   assert.equal(sessions.size, 0);
   assert.deepEqual(viewerErrors.flat(), []);
   for (const page of viewers) await page.close();
  }
  console.log('Task live: phone/desktop concurrent drafts, coalescing, shared queue, conflicts, caret, presence and gone passed');
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
