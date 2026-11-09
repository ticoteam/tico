// Data safety: pending edits and reads stay with the task/dialog that owns them.
import type {Browser, Page} from 'playwright';
interface TaskRow {id: string; [key: string]: unknown}
interface CommentRow {id: string; refs: {task?: string; target?: unknown; questions?: unknown; [key: string]: unknown}; [key: string]: unknown}
interface FileRow {n?: number; current_version?: number; versions: {n: number; ask?: unknown; [key: string]: unknown}[]; [key: string]: unknown}
interface Fixture {tasks: TaskRow[]; comments: CommentRow[]; files: FileRow[]}
interface Visit {page: Page; data: Fixture; errors: string[]}
interface TaskDialog extends HTMLDialogElement {returnContext: unknown; taskOpening: unknown; askDrafts: Map<string, unknown>; drawSeq: number; taskFiles: FileRow[]}
interface WinState {readDone: string[]; taskCloseDelivered: Promise<Event>; releaseRows: () => void; reopening: Promise<void>; openingState: {load: unknown; back: unknown; peek: unknown}}
declare const readDone: string[];
declare const taskCloseDelivered: Promise<Event>;
declare const reopening: Promise<void>;
declare const openingState: {load: unknown; back: unknown; peek: unknown};
declare const releaseRows: () => void;
declare let loadHarnessRows: () => Promise<void>;
declare const S: {emps?: unknown[]};
declare const TASKS_ST: {tasks?: unknown[]; peek?: string};
declare const TASK_MODAL_LOAD: unknown;
declare const TASK_CHAT: unknown;
declare const TF_CACHE: Map<string, FileRow[]>;
declare function taskPeek(): TaskDialog;
declare function taskModal(): TaskDialog;
declare function taskModalShow(task: TaskRow, d?: TaskDialog): Promise<void>;
declare function taskChatRead(chat: unknown): void;
declare function taskChatLive(chat: unknown): void;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path = require('node:path') as typeof import('node:path');
const {open} = require('./task-code.cjs') as {open: (browser: Browser, opts?: Record<string, unknown>) => Promise<Visit>};
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const gate = () => { let resolve!: (value?: unknown) => void; const promise = new Promise<unknown>(r => resolve = r); return {promise, resolve}; };
const shots = process.env.TICO_DETAIL_SHOTS;
const shot = (page: Page, name: string) => shots ? page.screenshot({path: path.join(shots, name + '.png')}) : Promise.resolve();
async function visit(browser: Browser) {
  const v = await open(browser, {viewport: {width: 390, height: 844}, rich: true, at: '#/bot/eng/tasks'});
  v.page.setDefaultTimeout(t(30000));
  // readDone: the path of every answer the app has read in full (its handler runs in the same turn)
  await v.page.evaluate(() => {
    const send = fetch; (window as unknown as WinState).readDone = [];
    window.fetch = async (...args) => {
      const r = await send(...args), text = r.text.bind(r);
      r.text = async () => { try { return await text(); } finally { readDone.push(new URL(r.url).pathname); } };
      return r;
    };
  });
  return v;
}
const reads = (page: Page, p: string) => page.evaluate((p: string) => readDone.filter(x => x === p).length, p);
async function show(page: Page) {
  await page.locator('[data-task-detail="t-checkout"] [data-task-detail-open]').click();
  await page.locator('#task-modal .task-comments .ask').waitFor();
}
(async () => {
 const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
 try {
  // Native close() queues its close event. Keep the next opening in loadHarnessRows until that real event
  // has reached every close listener, then let it render. No synthetic events or timing guesses.
  for (const [peek, width] of [[false, 1440], [true, 1440], [true, 390]] as [boolean, number][]) for (const sameTask of [true, false]) {
   const {page, data, errors} = await open(browser, {at: peek ? '#/tasks' : '#/bot/eng', viewport: {width, height: 900}, rich: true});
   await page.waitForFunction((peek: boolean) => S.emps?.length && (!peek || TASKS_ST?.tasks?.length), peek);
   await page.evaluate(async ({task, peek}: {task: TaskRow; peek: boolean}) => {
    const d = peek ? taskPeek() : taskModal();
    if (peek) TASKS_ST.peek = 't' + task.id;
    await taskModalShow(task, d);
   }, {task: data.tasks[0]!, peek});
   const id = peek ? '#task-peek' : '#task-modal';
   await page.locator(id + ' textarea[aria-label="Message"]').waitFor();
   await page.evaluate(({task, peek}: {task: TaskRow; peek: boolean}) => {
    const d = peek ? taskPeek() : taskModal(), original = loadHarnessRows;
    (window as unknown as WinState).taskCloseDelivered = new Promise<Event>(resolve => d.addEventListener('close', resolve, {once: true}));
    loadHarnessRows = () => new Promise<void>(resolve => { (window as unknown as WinState).releaseRows = () => { loadHarnessRows = original; resolve(); }; });
    d.close();
    if (peek) TASKS_ST.peek = 't' + task.id;
    (window as unknown as WinState).reopening = taskModalShow(task, d);
    (window as unknown as WinState).openingState = {load: TASK_MODAL_LOAD, back: d.returnContext, peek: TASKS_ST?.peek};
   }, {task: data.tasks[sameTask ? 0 : 1]!, peek});
   await page.evaluate(() => taskCloseDelivered.then(() => true));
   const kept = await page.evaluate((peek: boolean) => {
    const d = peek ? taskPeek() : taskModal();
    return {closed: !d.open, load: TASK_MODAL_LOAD === openingState.load,
     back: d.returnContext === openingState.back, peek: TASKS_ST?.peek === openingState.peek};
   }, peek);
   await page.evaluate(async () => { releaseRows(); await reopening; });
   assert.equal(await page.locator(id).evaluate(d => (d as HTMLDialogElement).open), true,
    `queued native close must not cancel ${peek ? 'peek' : 'modal'} reopening (${sameTask ? 'same' : 'other'} task)`);
   assert.deepEqual(kept, {closed: true, load: true, back: true, peek: true}, 'old close preserves the pending opening and navigation');
   await page.locator(id + ' textarea[aria-label="Message"]').waitFor();
   assert.equal(await page.locator(id).getAttribute('data-task'), data.tasks[sameTask ? 0 : 1]!.id);
   // An ordinary close still invalidates this opening and discards its drafts.
   assert.equal(await page.evaluate(async (peek: boolean) => {
    const d = peek ? taskPeek() : taskModal(), opening = d.taskOpening;
    d.askDrafts.set('test', 'draft');
    const closed = new Promise<Event>(resolve => d.addEventListener('close', resolve, {once: true}));
    d.close(); await closed;
    return !d.open && d.taskOpening !== opening && d.askDrafts.size === 0 && (!peek || TASKS_ST.peek === '');
   }, peek), true);
   // Closing an already visible dialog while it switches tasks still cancels that pending draw.
   await page.evaluate(async ({task, other, peek}: {task: TaskRow; other: TaskRow; peek: boolean}) => {
    const d = peek ? taskPeek() : taskModal();
    if (peek) TASKS_ST.peek = 't' + task.id;
    await taskModalShow(task, d);
    const original = loadHarnessRows;
    let release!: (value?: void) => void;
    loadHarnessRows = () => new Promise(resolve => { release = resolve; });
    if (peek) TASKS_ST.peek = 't' + other.id;
    const switching = taskModalShow(other, d);
    const closed = new Promise<Event>(resolve => d.addEventListener('close', resolve, {once: true}));
    d.close(); await closed;
    loadHarnessRows = original; release(); await switching;
   }, {task: data.tasks[0]!, other: data.tasks[1]!, peek});
   assert.equal(await page.locator(id).evaluate(d => (d as HTMLDialogElement).open), false, 'a newer close cancels a pending task switch');
   assert.deepEqual(errors, []); await page.close();
  }
  // The native close can arrive before reopening starts, while the Back traversal it queued is still pending.
  {
   const {page, data, errors} = await open(browser, {at: '#/bot/eng', rich: true});
   await page.waitForFunction(() => S.emps?.length);
   await page.evaluate((task: TaskRow) => taskModalShow(task), data.tasks[0]!);
   await page.evaluate(async (task: TaskRow) => {
    const d = taskModal();
    const popped = new Promise<Event>(resolve => window.addEventListener('popstate', resolve, {once: true}));
    const closed = new Promise<Event>(resolve => d.addEventListener('close', resolve, {once: true}));
    d.close(); await closed;
    await taskModalShow(task);
    await popped;
   }, data.tasks[0]!);
   assert.equal(await page.locator('#task-modal').evaluate(d => (d as HTMLDialogElement).open), true, 'an older Back traversal must not close the reopened task');
   assert.equal(await page.evaluate(() => !!history.state?.taskModal), true, 'the reopened task owns its history entry');
   await page.evaluate(async () => {
    const popped = new Promise<Event>(resolve => window.addEventListener('popstate', resolve, {once: true}));
    history.back(); await popped;
   });
   assert.equal(await page.locator('#task-modal').evaluate(d => (d as HTMLDialogElement).open), false, 'Back still closes the new opening');
   assert.deepEqual(errors, []); await page.close();
  }
  {
   const {page, data, errors} = await visit(browser);
   data.comments[1]!.refs.target = {file: 'f-notes', version: 2};
   data.files[0]!.versions[0]!.ask = {questions: data.comments[1]!.refs.questions, by: 'bot:eng'};
   await show(page);
   const draft = page.locator('#task-modal .task-comments .ask');
   await draft.locator('[data-label="Tuesday"]').click(); await draft.locator('.ask-other').fill('After the morning release');
   await page.locator('#task-modal [data-tf-file="f-notes"]').click();
   await page.locator('#task-modal [data-tf-view] .ask-other').fill('File-specific draft');
   // the task redraws under the open file (a label change), by script: the file's modal covers the task
   await page.locator('#task-modal [data-drop-label="checkout"]').dispatchEvent('click');
   await page.waitForFunction(() => (document.querySelector('#task-modal') as TaskDialog).dataset.version === '3' && document.querySelector('#task-modal .ask'));
   assert.equal(await draft.locator('[data-label="Tuesday"]').getAttribute('aria-pressed'), 'true');
   assert.equal(await draft.locator('.ask-other').inputValue(), 'After the morning release');
   assert.equal(await page.locator('#task-modal [data-tf-view] .ask-other').inputValue(), 'File-specific draft');
   await shot(page, 'after-answer-draft');
   await page.keyboard.press('Escape'); await page.locator('#task-modal [data-tf-view]').waitFor({state: 'hidden'});
   await page.route('**/api/v2/tasks/t-checkout/comments', r => r.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: {detail: 'Service unavailable'}})}));
   await page.locator('#task-modal .task-chat form textarea').fill('Please check the receipt total.');
   await page.locator('#task-modal .task-chat button[type=submit]').click();
   await page.locator('#task-modal [data-task-chat-status]', {hasText: 'Not saved'}).waitFor();
   assert.equal(await draft.locator('[data-label="Tuesday"]').innerText(), 'Tuesday');
   assert.equal(await page.locator('#task-modal .task-chat form textarea').inputValue(), 'Please check the receipt total.');
   await shot(page, 'after-comment-failure');
   // Losing comment permission clears the pending answer and removes its controls.
   await page.route('**/api/v2/tasks/t-checkout', r => r.fulfill({contentType: 'application/json', body: JSON.stringify({task: data.tasks[0]!, comments: data.comments, can_comment: false})}));
   await page.locator('#task-modal [data-tf-file="f-notes"]').click();
   await page.locator('#task-modal [data-tf-view] button.ask-opt').first().waitFor();
   await page.evaluate(() => taskChatRead(TASK_CHAT));
   assert.equal(await draft.locator('button.ask-opt').count(), 0);
   assert.equal(await page.locator('#task-modal [data-tf-view] button.ask-opt').count(), 0);
   assert.equal(await page.evaluate(() => (document.querySelector('#task-modal') as TaskDialog).askDrafts.size), 0);
   assert.deepEqual(errors, []); await page.close();
  }
  {
   const {page, data, errors} = await visit(browser), held = gate(), reached = gate(); let requests = 0;
   const old: FileRow[] = JSON.parse(JSON.stringify(data.files)); old[0]!.versions = old[0]!.versions.filter(v => v.n === 1); old[0]!.current_version = 1;
   await page.route('**/api/v2/tasks/t-checkout/files', async r => {
    const n = ++requests; if (n === 1) { reached.resolve(); await held.promise; }
    await r.fulfill({contentType: 'application/json', body: JSON.stringify({files: n === 1 ? old : data.files})});
   });
   await show(page); await reached.promise;
   // A change to the task arrives on the live stream while the first read is still out: the files are read again.
   await page.evaluate(() => taskChatLive(TASK_CHAT));
   await page.locator('#task-modal [data-tf-file="f-notes"] .tf-meta', {hasText: 'v2'}).waitFor({timeout: t(12000)});
   await page.locator('#task-modal [data-tf-file="f-notes"]').click();
   await page.locator('#task-modal [data-tf-view]', {hasText: 'Receipt check version 2.'}).waitFor();
   const delivered = page.waitForResponse(r => r.url().endsWith('/tasks/t-checkout/files')); held.resolve(); await delivered;
   await page.waitForFunction((n: number) => readDone.filter(x => x === '/api/v2/tasks/t-checkout/files').length >= n, requests);   // the stale answer was read
   assert.equal(await page.evaluate(() => (document.querySelector('#task-modal') as TaskDialog).taskFiles[0]!.current_version), 2);
   assert.equal(await page.evaluate(() => TF_CACHE.get('t-checkout')![0]!.current_version), 2);
   assert.match(await page.locator('#task-modal [data-tf-view]').innerText(), /Receipt check version 2/);
   await shot(page, 'after-file-response-order');
   assert.deepEqual(errors, []); await page.close();
  }
  for (const remove of [false, true]) {
   const {page, errors} = await visit(browser); await show(page);
   const held = gate(), reached = gate();
   await page.route('**/api/v2/tasks/t-checkout/links', async r => { reached.resolve(); await held.promise; await r.fulfill({contentType: 'application/json', body: '{}'}); });
   if (remove) await page.locator('#task-modal [data-drop-link="d1"]').click();
   else {
    await page.locator('#task-modal [data-link-add]').click();
    await page.locator('#task-modal [data-modal-link] input').fill('https://acme.example/spec');
    await page.locator('#task-modal [data-modal-link] input').press('Enter');
   }
   await reached.promise; await page.locator('#task-modal [data-modal-close]').click();
   await page.waitForFunction(() => !(document.querySelector('#task-modal') as TaskDialog).open && !history.state?.taskModal);
   if (remove) await show(page); // Same task, different opening: the old save must not redraw it.
   const seq = await page.evaluate(() => (document.querySelector('#task-modal') as TaskDialog).drawSeq);
   const fetched = page.waitForResponse(r => r.url().endsWith('/tasks/t-checkout') && r.request().method() === 'GET');
   const before = await reads(page, '/api/v2/tasks/t-checkout');
   held.resolve(); await fetched;
   await page.waitForFunction((n: number) => readDone.filter(x => x === '/api/v2/tasks/t-checkout').length > n, before);   // the old save's read-back was handled
   assert.equal(await page.evaluate(() => (document.querySelector('#task-modal') as TaskDialog).open), remove);
   assert.equal(await page.evaluate(() => (document.querySelector('#task-modal') as TaskDialog).drawSeq), seq);
   if (!remove) await shot(page, 'after-link-dismissal');
   assert.deepEqual(errors, []); await page.close();
  }
  console.log('Task detail: queued close/reopen, drafts, comment failure, file ordering, phone preview and link opening ownership passed');
 } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
