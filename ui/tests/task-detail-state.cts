// Data safety: pending edits and reads stay with the task/dialog that owns them.
import type {Browser, Page} from 'playwright';
interface TaskRow {id: string; [key: string]: unknown}
interface CommentRow {id: string; refs: {task?: string; target?: unknown; questions?: unknown; [key: string]: unknown}; [key: string]: unknown}
interface FileRow {n?: number; current_version?: number; versions: {n: number; ask?: unknown; [key: string]: unknown}[]; [key: string]: unknown}
interface Fixture {tasks: TaskRow[]; comments: CommentRow[]; files: FileRow[]}
interface Visit {page: Page; data: Fixture; errors: string[]}
interface TaskDialog extends HTMLDialogElement {drawSeq: number; taskFiles: FileRow[]}
interface WinState {readDone: string[]}
declare const readDone: string[];
declare const S: {emps?: unknown[]};
declare const TASK_CHAT: unknown;
declare const TF_CACHE: Map<string, FileRow[]>;
declare function v2Get(path: string): Promise<unknown>;
declare function taskModal(): TaskDialog;
declare function taskModalShow(task: TaskRow, d?: TaskDialog): Promise<void>;
declare function taskChatLive(chat: unknown): void;
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const path = require('node:path') as typeof import('node:path');
const {open} = require('./task-code.cts') as {open: (browser: Browser, opts?: Record<string, unknown>) => Promise<Visit>};
const {t} = (() => { try { return require('./support/load.cts') as import('./support/load.cts').Api; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
const gate = () => { let resolve!: (value?: unknown) => void; const promise = new Promise<unknown>(r => resolve = r); return {promise, resolve}; };
const shots = process.env.TICO_DETAIL_SHOTS;
const shot = (page: Page, name: string) => shots ? page.screenshot({path: path.join(shots, name + '.png')}) : Promise.resolve();
async function visit(browser: Browser) {
  const v = await open(browser, {viewport: {width: 390, height: 844}, rich: true, at: '#/tasks'});
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
  // As a row opens it: the task in hand, straight into its window (taskModalOpen would drop it if a Back lands meanwhile).
  await page.evaluate(async () => { const r = await v2Get('/v2/tasks/t-checkout') as {task: TaskRow}; await taskModalShow(r.task); });
  await page.locator('#task-modal[open] .task-comments .tcomment').first().waitFor();
}
(async () => {
 const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
 try {
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
   const {page, errors} = await visit(browser);
   await show(page);
   // A comment that fails to save stays in the box, marked Not saved.
   await page.route('**/api/v2/tasks/t-checkout/comments', r => r.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({error: {detail: 'Service unavailable'}})}));
   await page.locator('#task-modal .task-chat form textarea').fill('Please check the receipt total.');
   await page.locator('#task-modal .task-chat button[type=submit]').click();
   await page.locator('#task-modal [data-task-chat-status]', {hasText: 'Not saved'}).waitFor();
   assert.equal(await page.locator('#task-modal .task-chat form textarea').inputValue(), 'Please check the receipt total.');
   await shot(page, 'after-comment-failure');
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
   // A phone shows the task's files in its Info tab (task #409).
   await page.locator('#task-modal[open]').getByRole('tab', {name: 'Info'}).click();
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
   // A phone shows Links in the task window's Info tab; adding one starts from ⋯ › Activity and links (task #409).
   await page.locator('#task-modal[open] [data-task-tab="info"]').click();
   if (!remove) { await page.locator('#task-modal [data-task-more]').click(); await page.locator('#task-modal .prop-pop').getByRole('menuitem', {name: 'Activity and links'}).click(); }
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
  console.log('Task detail: close/reopen history, comment failure, file ordering, phone preview and link opening ownership passed');
 } finally { await browser.close(); }
})().catch(e => {console.error(e); process.exit(1);});
