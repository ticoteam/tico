import type * as Tasks from './tasks-page.cts';
import type * as TaskCode from './task-code.cts';
import type {Page} from 'playwright';
// Concurrent task drafts, versions, focus and authorized-place lifecycle in the real app bundle.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const {open} = require('./task-code.cts') as TaskCode.Api;
// Page globals read inside page.evaluate (types: tasks-page.cts).
declare var S: Tasks.Globals['S'];
declare var TASKS_ST: Tasks.Globals['TASKS_ST'];
declare var TASK_EDIT_DRAFTS: Tasks.Globals['TASK_EDIT_DRAFTS'];
declare var get: Tasks.Globals['get'];
declare var leavingLive: Tasks.WindowMarks['leavingLive'];
declare var propertySave: Tasks.WindowMarks['propertySave'];
declare var taskLiveStop: Tasks.Globals['taskLiveStop'];
declare var taskModal: Tasks.Globals['taskModal'];
declare var taskModalShow: Tasks.Globals['taskModalShow'];
declare var taskSaveQueued: Tasks.Globals['taskSaveQueued'];
declare var taskSources: Tasks.WindowMarks['taskSources'];
declare const window: Window & typeof globalThis & Pick<Tasks.WindowMarks, 'leavingLive' | 'propertySave' | 'taskSources'>;
const gate = () => { let resolve!: (value?: unknown) => void; const promise = new Promise<unknown>(r => { resolve = r; }); return {promise, resolve}; };
type Gate = ReturnType<typeof gate>;
(async () => {
 const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL || undefined} as import('playwright').LaunchOptions);
 const pages: Page[] = [];
 try {
  // The details editor opens from the padded edge of its phone target, including the layout breakpoint.
  for (const width of [400, 560, 760, 761, 1440]) {
   const phone = width <= 760;
   const context = await browser.newContext({viewport: {width, height: 900}, hasTouch: phone});
   try {
    const {page, errors} = await open({newPage: () => context.newPage()}, {viewport: {width, height: 900}});
    const edit = page.getByRole('button', {name: 'Edit details', exact: true});
    // A phone shows the details in the task window's Info tab (task #409).
    if (phone) await page.locator('#task-modal[open] [data-task-tab="info"]').click();
    await page.locator('details.task-orig').filter({has: edit}).evaluateAll(rows => rows.forEach(el => { (el as HTMLDetailsElement).open = true; }));
    await edit.waitFor();
    const box = (await edit.boundingBox())!;
    if (phone) {
     assert.ok(box.height >= 40 && box.width >= 40, `Edit details needs a 40 px target at ${width} px; got ${box.width}x${box.height}`);
     await edit.tap({position: {x: box.width / 2, y: box.height - 2}});
    } else {
     assert.ok(box.height < 40, 'desktop Edit details stays compact');
     await edit.click();
    }
    const details = page.getByRole('textbox', {name: 'Details', exact: true});
    await details.waitFor();
    assert.equal(await details.evaluate(el => document.activeElement === el), true, 'opening details focuses its editor');
    assert.deepEqual(errors, []);
   } finally { await context.close(); }
  }
  for (const width of [390, 1440]) {
   let readHold!: Gate | null, readStarted!: Gate, task!: TaskCode.Fixture['tasks'][number], events: Record<string, unknown>[] = [], sessions = new Map<string, string>(), writes: {viewer: string; patch: Record<string, unknown>}[] = [], hold!: Gate | null, fail = false, deny = false, inflight = 0, max = 0;
   const viewers: Page[] = [], viewerErrors: string[][] = [];
   const emit = async (data: {topic: string; body: unknown}) => {
    for (const page of viewers) if (!page.isClosed()) await page.evaluate(data => {
     for (const source of window.taskSources || []) if (!source.closed) source.dispatchEvent(new MessageEvent(data.topic, {data: JSON.stringify(data.body)}));
    }, data);
   };
   for (const [viewer, viewport] of [['ana', width], ['sam', width === 390 ? 1440 : 390]] as const) {
    const {page, data, errors} = await open(browser, {at: '#/task/t-checkout', viewport: {width: viewport, height: 900}});
    pages.push(page); viewers.push(page); viewerErrors.push(errors);
    await page.locator('#task-modal[open] textarea[aria-label="Message"]').waitFor();
    task ||= {...data.tasks[0]!, title: 'Review the launch', body: 'Initial details', links: [], children_summary: undefined};
    await page.evaluate(viewer => {
     taskLiveStop(taskModal());
     S.me.id = viewer;
     window.taskSources = [];
     window.EventSource = class extends EventTarget {
      constructor(url: string) { super(); (this as unknown as {url: string}).url = url; taskSources!.push(this); }
      close() { (this as unknown as {closed: boolean}).closed = true; }
     } as unknown as typeof EventSource;
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
     if (deny && viewer === 'ana') { deny = false; return route.fulfill({status: 403, json: {error: 'Write refused'}}); }
     if (patch.version !== task.version) return route.fulfill({status: 409, json: {error: {code: 'version_conflict', detail: 'Task changed'}}});
     Object.assign(task, patch, {version: task.version + 1});
     for (const field of Object.keys(patch).filter(k => k !== 'version')) events.push({field, actor: 'human:' + viewer, created_at: new Date().toISOString()});
     await route.fulfill({json: {task}});
    });
    await page.evaluate(async task => { await taskModalShow(task); }, task);
    await page.waitForFunction(() => !!taskModal().taskLive?.shared);
    // A phone shows the details in the task window's Info tab (task #409).
    if (viewport <= 760) await page.locator('#task-modal[open] [data-task-tab="info"]').click();
    await page.locator('details.task-orig').filter({has: page.locator('[data-task-edit-body]')}).evaluateAll(rows => rows.forEach(el => { (el as HTMLDetailsElement).open = true; }));
    assert.deepEqual(errors, []);
   }
   const [a, b] = viewers as [Page, Page];
   let listReads = 0;
   for (const page of viewers) page.on('request', request => {
    if (request.method() === 'GET' && new URL(request.url()).pathname === '/api/v2/tasks') listReads++;
   });
   const update = () => emit({topic: 'tasks', body: {task_id: task.id, kinds: ['update']}});
   const title = a.locator('#task-modal .tmodal-title');
   const fillTitle = async (value: string) => { await title.click(); await title.fill(value); };
   const next = gate(); hold = next;
   await fillTitle('First title');
   await a.waitForFunction(() => !!taskModal().taskLive!.draft.flight);
   await fillTitle('Final title');
   await title.evaluate(el => { const selection = getSelection()!; selection.setBaseAndExtent(el.firstChild!, 2, el.firstChild!, 7); });
   const caret = () => title.evaluate(el => [document.activeElement === el, getSelection()!.anchorOffset, getSelection()!.focusOffset]);
   await b.getByRole('button', {name: 'Edit details', exact: true}).click();
   await b.getByRole('textbox', {name: 'Details', exact: true}).fill('Sam changed only details');
   await b.locator('[data-task-more]').click(); await b.keyboard.press('Escape');
   await b.waitForFunction(() => !taskModal().taskLive!.draft.flight && !Object.keys(taskModal().taskLive!.draft.patch).length);
   await update();
   await a.waitForFunction(() => taskModal().taskLive!.draft.base.body === 'Sam changed only details');
   assert.equal(await title.textContent(), 'Final title');
   assert.deepEqual(await caret(), [true, 2, 7], 'live redraw preserves the actual title caret');
   next.resolve();
   await a.locator('[data-task-edit-error]:not([hidden])').waitFor();
   assert.equal(await title.textContent(), 'Final title');
   assert.deepEqual(await caret(), [true, 2, 7]);
   await a.getByRole('button', {name: 'Retry', exact: true}).click();
   await a.waitForFunction(() => !taskModal().taskLive!.draft.error && !taskModal().taskLive!.draft.flight);
   assert.equal(task.title, 'Final title'); assert.equal(task.body, 'Sam changed only details');
   assert.deepEqual(Object.keys(writes.filter(w => w.viewer === 'ana').at(-1)!.patch).sort(), ['title', 'version']);
   await a.getByText('You edited · just now', {exact: true}).waitFor();
   assert.equal(listReads, 0, 'autosave and task invalidations never reread the task list');
   deny = true;
   await fillTitle('Keep this refused draft'); await title.evaluate(el => el.blur());
   await a.locator('[data-task-edit-error]:not([hidden])').waitFor();
   await a.waitForFunction(() => !taskModal().taskLive!.reading);
   assert.equal(await a.locator('#task-modal').evaluate(d => (d as HTMLDialogElement).open), true, 'a refused write does not revoke read access');
   assert.equal(await title.textContent(), 'Keep this refused draft');
   await a.locator('[data-task-edit-retry]').click();
   await a.waitForFunction(() => !taskModal().taskLive!.draft.error && !taskModal().taskLive!.draft.flight);
   assert.equal(task.title, 'Keep this refused draft');
   await a.evaluate(() => { S.me.mover = false; });
   task.owner_id = 'human:sam'; task.requester_id = 'human:sam'; task.version++;
   await update();
   await a.waitForFunction(() => taskModal().liveTask!.owner_id === 'human:sam');
   await title.focus(); await a.keyboard.press('Enter');
   assert.equal(await title.getAttribute('contenteditable'), 'false', 'a retained title cannot re-enable editing after write rights are lost');
   await a.evaluate(() => { S.me.mover = true; });
   task.owner_id = 'bot:eng'; task.requester_id = 'human:ana'; task.version++;
   await update();
   await a.waitForFunction(() => taskModal().liveTask!.owner_id === 'bot:eng');
   // A slow successful save coalesces the later title, still sharing the dialog's property queue.
   const slow = gate(); hold = slow;
   await fillTitle('Slow title');
   await a.waitForFunction(() => !!taskModal().taskLive!.draft.flight);
   await fillTitle('Coalesced title');
   const before = writes.length;
   await a.evaluate(() => { window.propertySave = taskSaveQueued(taskModal(), taskModal().dataset.task, {due: ''}, false, 'due'); });
   assert.equal(writes.length, before, 'property writes wait behind autosave');
   slow.resolve();
   await a.evaluate(() => propertySave);
   // The property update advances the version; the draft keeps its newer words for explicit retry if needed.
   await a.waitForFunction(() => !taskModal().taskLive!.draft.flight);
   if (await a.locator('[data-task-edit-error]').isVisible()) await a.getByRole('button', {name: 'Retry', exact: true}).click();
   await a.waitForFunction(() => !taskModal().taskLive!.draft.error && !taskModal().taskLive!.draft.flight);
   assert.equal(task.title, 'Coalesced title'); assert.equal(max, 1);
   // Presence uses the scoped subscription and per-window leave; a stale stream cannot close a new task.
   await emit({topic: 'presence', body: {place: 'task:' + task.id}});
   await a.getByText('Sam is here', {exact: true}).waitFor();
   const urls = await a.evaluate(() => taskSources!.map(s => s.url));
   assert(urls.some(url => { const q = new URL(url!, 'https://tico-ui.test').searchParams; return q.get('task') === task.id && q.get('place') === 'task:' + task.id; }));
   await b.evaluate(() => taskModal().close());
   await b.waitForFunction(() => !taskModal().taskLive);
   await emit({topic: 'presence', body: {place: 'task:' + task.id}});
   await a.waitForFunction(() => !document.querySelector<HTMLElement>('[data-task-presence]')!.textContent);
   assert.equal(sessions.size, 1);
   const delayed = gate(); readHold = delayed; readStarted = gate();
   await a.evaluate(() => { window.leavingLive = taskModal().taskLive; });
   await update(); await readStarted.promise;
   const lateWrite = gate(); hold = lateWrite;
   await fillTitle('Words from a departed editor');
   await a.waitForFunction(() => !!leavingLive!.draft.flight);
   const listsBeforeGone = listReads;
   await emit({topic: 'tasks', body: {task_id: task.id, gone: true}});
   await a.waitForFunction(() => !taskModal().open && !taskModal().taskLive);
   assert.equal(await a.evaluate(id => TASKS_ST.tasks.some(task => task.id === id), task.id), false, 'gone removes the unreadable task row');
   delayed.resolve(); lateWrite.resolve();
   await a.waitForFunction(() => !leavingLive!.reading && !leavingLive!.draft.flight);
   assert.equal(await a.evaluate(id => TASKS_ST.tasks.some(task => task.id === id), task.id), false, 'a late save cannot reinsert a revoked task');
   assert.equal(listReads, listsBeforeGone, 'revocation removes one task without rereading the list');
   assert.equal(await a.evaluate(() => TASK_EDIT_DRAFTS.size), 0);
   await b.waitForFunction(async () => !((await get('/v2/presence?place=task:t-checkout', {fresh: true})) as {actors: unknown[]}).actors.length);
   assert.equal(sessions.size, 0);
   assert.deepEqual(viewerErrors.flat(), []);
   for (const page of viewers) await page.close();
  }
  console.log('Task live: phone/desktop concurrent drafts, coalescing, shared queue, conflicts, caret, presence and gone passed');
 } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
