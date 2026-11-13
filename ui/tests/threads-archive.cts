// Archive where a thread is seen (ui/app/thread-rail.ts, ui/app/thread-view.ts, ui/app/threads.ts): on a phone at 390x844
// a Threads row has an Archive button for a thread the viewer manages. Tapping it takes the thread off the list and its
// Catch up count with it, at once, and an Undo toast brings it back. The open thread's header has the same Archive,
// which goes back to the list; on an archived thread it says Unarchive. A thread the viewer does not manage has neither.
// On a desktop the row's Archive shows on hover and on keyboard focus.
// The page holds the live stream (GET /api/v2/events) open, and `send` answers it with the blocks the server would send:
// a task's thread leaves Threads when its task finishes, a rename does not bring it back, a post after the finish does;
// an open Catch up drops a card whose task finished; a 1:1 DM archived in another window leaves Catch up and its count.
// Screenshots go to TICO_SCREENSHOT_DIR (default: a threads-archive folder in the OS temp directory).
import type {Browser, Page, Route} from 'playwright';
// Page globals the Threads page exposes to these checks (declared here: each spec is its own module).
declare var THREADS: {gen: number; dms: Record<string, number>};
declare function toast(message: string): void;
// What the fixture serves for a thread: the fields these checks read and change.
interface Me {member: boolean; following: boolean; notify: string; last_read_seq: number; unread: number; activity: boolean; can_post: boolean; can_manage: boolean; pinned: boolean}
interface Thread {id: string; title: string; audience: string; root_id: string; path: string[]; parent_id: string | null; last_seq: number; last_unread_seq: number; last_message_at: string; me: Me; members: {actor: string; role?: string}[]; pinned_for_everyone: boolean; task_id?: string; hidden?: boolean; archived_at?: string | null; deleted?: boolean; deletable?: boolean; dm_with?: {actor: string; name: string}[]; dm_with_count?: number}
type Threads = Record<string, Thread>;
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts');
const {unrouted} = require('./support/routes.cts');

const shots = process.env.TICO_SCREENSHOT_DIR || path.join(require('node:os').tmpdir(), 'threads-archive-shots');
const at = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const me = (extra: Partial<Me> = {}): Me => ({member: true, following: true, notify: 'live', last_read_seq: 2, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false, ...extra});
const thread = (id: string, title: string, extra: Partial<Thread> = {}): Thread => ({id, title, audience: 'members', root_id: id, path: [id], parent_id: null, last_seq: 4, last_unread_seq: 4,
  last_message_at: at(5), me: me(), members: [{actor: 'human:ana'}, {actor: 'human:ben'}], pinned_for_everyone: false, ...extra});
// Each page gets fresh copies: a PATCH changes the copy the page reads.
const world = (): Threads => ({
  't-plan': thread('t-plan', 'Launch plan', {me: me({can_manage: true, unread: 2, activity: true})}),
  't-read': thread('t-read', 'Team lunch', {me: me({can_manage: false})}),
});
// The same, with a thread that belongs to a task (its row and Catch up card follow the task's state).
const liveWorld = () => ({...world(), 't-task': thread('t-task', 'Pricing', {task_id: 'task-9'})});
// TICO_ONLY=<block name> runs one of the live blocks alone, so each block's own failure can be seen.
const only = process.env.TICO_ONLY || '';
// What the server sends for a thread: `hidden` stands for its task having finished, which takes it off the list and out of
// Catch up, and its view says so (`task_hidden`, present only then).
const view = (t: Thread) => {
  const {hidden, ...rest} = t;
  return hidden ? {...rest, task_hidden: true} : rest;
};

// `threads` is the world the mock serves. `send(...blocks)` answers the newest live stream the page holds, as the server
// would (the page then reconnects, and the next send finds the new stream); `block(event, data)` makes one SSE block.
async function open(browser: Browser, {viewport = {width: 390, height: 844}, phone = true, threads = world()}: {viewport?: {width: number; height: number}; phone?: boolean; threads?: Threads} = {}) {
  const page = await browser.newPage({viewport, isMobile: phone, hasTouch: phone, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const seen: {errors: string[]; patches: Record<string, unknown>[]; deletes: string[]} = {errors: [], patches: [], deletes: []}, streams: {url: URL; route: Route}[] = [];
  let seq = 0;
  page.on('pageerror', e => seen.errors.push(e.message));
  page.on('dialog', d => d.accept());
  const visible = (t: Thread) => !t.archived_at && !t.hidden && !t.deleted;
  const rail = () => Object.values(threads).filter(t => visible(t) && t.audience !== 'personal').map(view);
  const catchUp = () => Object.values(threads).filter(t => visible(t) && t.me.unread > 0);
  const dms = () => Object.fromEntries(Object.values(threads).filter(t => visible(t) && t.audience === 'personal').map(t => [t.dm_with![0]!.actor, t.me.unread]));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    const body = () => JSON.parse(req.postData() || '{}') as Record<string, unknown>;
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben', email: 'ben@acme.example'}], org_groups: []});
    if (p === '/api/v2/events') { streams.push({url, route}); return; }        // held open until the test sends
    if (p === '/api/v2/slack/links') return json({links: [], available: false, next_cursor: null});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: rail(), dms: [], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: rail(), catch_up: catchUp().length, catch_up_ids: catchUp().map(t => t.id), dms: dms(), next_cursor: null});
    if (p === '/api/v2/threads/catch-up') return json({items: catchUp().map(t => ({thread: view(t), new_count: t.me.unread, mention: false, messages: [], more: 0})), count: catchUp().length, next_cursor: null});
    if (p === '/api/v2/threads/browse') return json({threads: [], next_cursor: null});
    if (p === '/api/v2/threads/archived') return json({threads: Object.values(threads).filter(t => t.archived_at && !t.deleted).map(view), next_cursor: null});
    const m = p.match(/^\/api\/v2\/threads\/([^/]+)(?:\/([a-z-]+))?$/);
    const t = m && threads[m[1]!];
    if (t) {
      const what = m![2];
      if (what === 'timeline') return json({thread: view(t), breadcrumb: [{id: t.id, title: t.title}], next_cursor: null,
        messages: [{id: 'x1-' + t.id, seq: 1, thread_id: t.id, sender_id: 'human:ben', body: 'Morning.', created_at: at(10), depth: 0}]});
      if (what === 'members') return json({members: [{actor: 'human:ana', role: 'owner'}, {actor: 'human:ben'}], next_cursor: null});
      if (what === 'read') return json({ok: true});
      if (what === 'calls') return json({calls: [], next_before: null, available: false});
      if (!what && method === 'PATCH') {
        const b = body(); seen.patches.push({id: t.id, ...b});
        if ('archived' in b) t.archived_at = b.archived ? at(0) : null;
        return json(view(t));
      }
      if (!what && method === 'DELETE') {
        seen.deletes.push(t.id); t.deleted = true; t.archived_at = t.archived_at || at(0);
        return json({deleted: t.id});
      }
      if (!what && method === 'GET') return json(view(t));
    }
    return json(unrouted(p));
  });
  const send = async (...blocks: string[]) => {
    const deadline = Date.now() + 8000;
    while (!streams.length) {
      if (Date.now() > deadline) throw new Error('no live stream from the page');
      await new Promise(r => setTimeout(r, 50));
    }
    const stream = streams.pop()!;
    for (const old of streams.splice(0)) await old.route.abort().catch(() => {});
    await stream.route.fulfill({contentType: 'text/event-stream', body: blocks.join('')});
  };
  const block = (event: string, data: Record<string, unknown>) => `id: ${++seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;
  return {page, seen, send, block, threads};
}
async function shot(page: Page, name: string) {
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `${name}.png`)});
}
const until = async (check: () => unknown, what: string) => {
  for (let i = 0; i < 80; i++) { if (await check()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail('timed out: ' + what);
};
const noSideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= 390);

let browser: Browser = null!;
(async () => {
  browser = await chromium.launch();

  // The Threads list on a phone: the row's Archive is shown (a touch screen always shows it), is 44px, and only for the
  // thread the viewer manages. Tapping it takes the row off the list and the Catch up count with it; Undo brings it back.
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/threads');
    const plan = page.locator('.tr-row[data-thread="t-plan"]');
    await plan.waitFor();
    const archive = page.locator('[data-tr-arch="t-plan"]');
    assert.equal(await archive.getAttribute('aria-label'), 'Archive');
    assert.equal(await archive.evaluate(el => getComputedStyle(el).opacity), '1', 'the phone always shows the row action');
    const size = (await archive.boundingBox())!;
    assert.ok(size.width >= 44 && size.height >= 44, `a 44px target: ${JSON.stringify(size)}`);
    assert.equal(await page.locator('[data-tr-arch="t-read"]').count(), 0, 'a thread the viewer does not manage has no Archive');
    assert.equal(await page.locator('[data-tr-catch] .tr-badge').textContent(), '1');
    assert.ok(await noSideways(page), 'no sideways scroll at 390px');
    await shot(page, 'threads-archive-phone-list');

    await archive.tap();
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor({state: 'detached'});
    await page.locator('[data-tr-catch] .tr-badge').waitFor({state: 'hidden'});
    assert.deepEqual(seen.patches, [{id: 't-plan', archived: true}]);
    const toast = page.locator('.toast', {hasText: 'Archived'});
    await toast.waitFor();
    await shot(page, 'threads-archive-phone-undo');

    await toast.getByRole('button', {name: 'Undo'}).tap();
    await plan.waitFor();
    await until(() => seen.patches.length === 2, 'the undo is sent');
    assert.deepEqual(seen.patches[1], {id: 't-plan', archived: false});
    await page.locator('[data-tr-catch] .tr-badge').waitFor({state: 'visible'});
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // The open thread's header: Archive on a thread the viewer manages goes back to the list; on an archived thread it
  // says Unarchive and brings the thread back in place. A thread the viewer does not manage has no Archive.
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/thread/t-read');
    await page.getByText('Morning.').waitFor();
    assert.equal(await page.locator('.thv-head [data-thv-archive]').count(), 0);

    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('Morning.').waitFor();
    const headArchive = page.locator('.thv-head [data-thv-archive]');
    assert.equal(await headArchive.textContent(), 'Archive', 'a word, not an icon');
    const size = (await headArchive.boundingBox())!;
    assert.ok(size.width >= 44 && size.height >= 44, `a 44px header target: ${JSON.stringify(size)}`);
    await shot(page, 'threads-archive-phone-thread');

    await headArchive.tap();
    await page.waitForFunction(() => location.hash === '#/threads');
    assert.deepEqual(seen.patches, [{id: 't-plan', archived: true}]);
    await page.locator('.toast', {hasText: 'Archived'}).waitFor();
    assert.equal(await page.locator('.tr-row[data-thread="t-plan"]').count(), 0);

    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('Morning.').waitFor();
    const back = page.locator('.thv-head [data-thv-archive]');
    assert.equal(await back.textContent(), 'Unarchive');
    await back.tap();
    await until(async () => (await page.locator('.thv-head [data-thv-archive]').textContent()) === 'Archive', 'Unarchive brings it back in place');
    assert.deepEqual(seen.patches[1], {id: 't-plan', archived: false});
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // On a desktop the row's Archive is hidden until the row is hovered, and shows when its button takes keyboard focus
  // (Tab from the row reaches it).
  {
    const {page, seen} = await open(browser, {viewport: {width: 1280, height: 800}, phone: false});
    await page.goto('https://tico-ui.test/#/threads');
    const row = page.locator('.tr-row[data-thread="t-plan"]');
    await row.waitFor();
    const archive = page.locator('[data-tr-arch="t-plan"]');
    const opacity = () => archive.evaluate(el => getComputedStyle(el).opacity);
    await until(async () => (await opacity()) === '0', 'the row Archive is hidden with the pointer away');
    await row.hover();
    await until(async () => (await opacity()) === '1', 'the row Archive shows on hover');
    await page.mouse.move(0, 0);
    await until(async () => (await opacity()) === '0', 'the row Archive hides when the pointer leaves');
    await row.focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.trArch), 't-plan', 'Tab from the row reaches its Archive');
    await until(async () => (await opacity()) === '1', 'the row Archive shows on keyboard focus');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A task's thread follows its task on the live stream, as it does on the list. Its closing notice reaches the thread as
  // an activity event; the thread's view then says the task hides it, so its row leaves Threads.
  // Browse threads' Archived tab lists the archived thread; Unarchive brings it back to Threads.
  if (!only || only === 'browse-archived') {
    const threads = world();
    threads['t-plan']!.archived_at = at(0);
    const {page, seen} = await open(browser, {viewport: {width: 1280, height: 800}, phone: false, threads});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('#thread-rail [data-tr-browse]').click();
    const br = page.locator('#thread-browse[open]');
    await br.locator('[data-tab="archived"]').click();
    await br.locator('li[data-thread="t-plan"] [data-unarchive]').click();
    await until(async () => (await br.locator('li[data-thread="t-plan"]').count()) === 0, 'Unarchive takes the row off the Archived list');
    assert.deepEqual(seen.patches, [{id: 't-plan', archived: false}]);
    await page.keyboard.press('Escape');
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor();
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // Delete is offered in the thread header only when the server says the thread is deletable; it asks first, sends one
  // DELETE, and the thread leaves Threads.
  if (!only || only === 'header-delete') {
    const threads = world();
    threads['t-plan']!.deletable = true;
    const {page, seen} = await open(browser, {viewport: {width: 1280, height: 800}, phone: false, threads});
    await page.goto('https://tico-ui.test/#/thread/t-plan');
    await page.getByText('Morning.').waitFor();
    await page.locator('.thv-head [data-thv-delete]').click();
    await until(async () => seen.deletes.length === 1, 'Delete sends one DELETE');
    assert.deepEqual(seen.deletes, ['t-plan']);
    await until(async () => (await page.locator('.tr-row[data-thread="t-plan"]').count()) === 0, 'a deleted thread leaves Threads');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  if (!only || only === 'live-close') {
    const {page, seen, send, block, threads} = await open(browser, {threads: liveWorld()});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tr-row[data-thread="t-task"]').waitFor();
    threads['t-task']!.hidden = true;
    await send(block('threads', {kind: 'activity', thread_id: 't-task', root_id: 't-task', message_id: 'n1', unread: false}));
    await until(async () => (await page.locator('.tr-row[data-thread="t-task"]').count()) === 0, 'a finished task leaves Threads on the live stream');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A rename of a finished task's thread does not put its row back: the view it sends still says the task hides it.
  if (!only || only === 'live-rename') {
    const {page, seen, send, block, threads} = await open(browser, {threads: liveWorld()});
    threads['t-task']!.hidden = true;
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor();
    assert.equal(await page.locator('.tr-row[data-thread="t-task"]').count(), 0, 'the finished task is not listed');
    threads['t-task']!.title = 'Pricing (renamed)';
    const gen = await page.evaluate(() => THREADS.gen);
    await send(block('threads', {kind: 'renamed', thread_id: 't-task', root_id: 't-task', thread: view(threads['t-task']!)}));
    await page.waitForFunction(was => THREADS.gen > was, gen);
    assert.equal(await page.locator('.tr-row[data-thread="t-task"]').count(), 0, 'a rename does not bring a finished task back');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A post in a finished task's thread brings its row back, unread, and Catch up counts it: activity for a row that is
  // not listed is read, and the thread's view says whether it is on the list now.
  if (!only || only === 'live-revive') {
    const {page, seen, send, block, threads} = await open(browser, {threads: liveWorld()});
    threads['t-task']!.hidden = true;
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor();
    assert.equal(await page.locator('.tr-row[data-thread="t-task"]').count(), 0, 'the finished task is not listed');
    threads['t-task']!.hidden = false;
    threads['t-task']!.me.unread = 1;
    await send(block('threads', {kind: 'activity', thread_id: 't-task', root_id: 't-task', message_id: 'm9', unread: true, unread_count: 1,
      counters: {last_seq: 5, last_unread_seq: 5}}));
    await until(async () => (await page.locator('.tr-row[data-thread="t-task"]').count()) === 1, 'a post after the finish brings its row back');
    // Launch plan's card was already counted; the revived thread's makes two.
    await page.locator('[data-tr-catch] .tr-badge').waitFor({state: 'visible'});
    assert.equal(await page.locator('[data-tr-catch] .tr-badge').textContent(), '2', 'Catch up counts the revived thread');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // An open Catch up drops a card when its task finishes: the closing notice comes as the thread's activity, and its
  // view says the task hides it. A Needs you change reads no rail (Nov 3).
  if (!only || only === 'catch-up-task') {
    const {page, seen, send, block, threads} = await open(browser, {threads: {'t-task': thread('t-task', 'Pricing', {task_id: 'task-9', me: me({unread: 2, activity: true})})}});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-tr-catch]').click();
    await page.locator('.cu-card[data-thread="t-task"]').waitFor();
    threads['t-task']!.hidden = true;
    await send(block('needs', {version: 'v2'}), block('threads', {kind: 'activity', thread_id: 't-task', root_id: 't-task', message_id: 'n1', unread: false}));
    await until(async () => (await page.locator('.cu-card[data-thread="t-task"]').count()) === 0, 'an open Catch up drops a card whose task finished');
    await page.locator('.cu-empty').waitFor();
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A 1:1 DM archived in another window leaves the viewer's Catch up card, the Catch up badge and the teammate's unread
  // count in the sidebar (the DM is not on the list, so its archive comes only as this event).
  if (!only || only === 'live-dm-archive') {
    const dmWorld = {'t-dm': {id: 't-dm', title: '', audience: 'personal', root_id: 't-dm', path: ['t-dm'], parent_id: null, last_seq: 4, last_unread_seq: 4,
      last_message_at: at(5), dm_with: [{actor: 'human:ben', name: 'Ben'}], dm_with_count: 1, members: [{actor: 'human:ana'}, {actor: 'human:ben'}],
      me: me({unread: 2, activity: true}), pinned_for_everyone: false}};
    const {page, seen, send, block, threads} = await open(browser, {viewport: {width: 1280, height: 800}, phone: false, threads: dmWorld});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tree-dm').waitFor();
    await page.locator('[data-tr-catch]').click();
    await page.locator('.cu-card[data-thread="t-dm"]').waitFor();
    assert.equal(await page.locator('#nav-threads .nav-badge').textContent(), '1');

    threads['t-dm']!.archived_at = at(0);
    await send(block('threads', {kind: 'archived', thread_id: 't-dm', root_id: 't-dm', thread: view(threads['t-dm']!)}));
    await until(async () => (await page.locator('.cu-card[data-thread="t-dm"]').count()) === 0, 'an archived DM leaves the open Catch up');
    await until(async () => page.locator('#nav-threads .nav-badge').isHidden(), 'the Catch up badge clears');
    await until(async () => (await page.locator('.tree-dm').count()) === 0, "the teammate's unread count clears");
    assert.equal(await page.evaluate(() => THREADS.dms['human:ben'] || 0), 0);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A toast clears the phone tab bar where the bar shows, and sits at its normal place where setup hides the bar (the
  // Undo toast of a phone list is the one that must be tappable above it).
  if (!only || only === 'toast-tab-bar') {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor();
    assert.equal(await page.locator('#mobile-nav').isVisible(), true, 'the tab bar shows on the Threads page');
    await page.evaluate(() => toast('Tab bar shows'));
    const shown = page.locator('.toast', {hasText: 'Tab bar shows'});
    await shown.waitFor();
    assert.equal(await shown.evaluate(el => getComputedStyle(el).bottom), '76px', 'the 64px bar and 12px above it');

    await page.evaluate(() => { document.documentElement.classList.add('in-setup'); toast('No tab bar'); });
    const plain = page.locator('.toast', {hasText: 'No tab bar'});
    await plain.waitFor();
    assert.equal(await page.locator('#mobile-nav').isVisible(), false, 'setup hides the tab bar');
    assert.equal(await plain.evaluate(el => getComputedStyle(el).bottom), '20px', 'no tab bar, so no lift');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  console.log(only ? `PASS: Threads archive, block ${only} only` : 'PASS: Threads archive: a phone row shows Archive for a managed thread (44px, always shown), taking it and its Catch up count off at once with an Undo that brings it back; the open thread\'s header archives it back to the list, and Unarchive restores it in place; a thread the viewer does not manage has neither; no sideways scroll at 390px; on a desktop the row Archive shows on hover and keyboard focus; over a live stream a close, rename or revive moves the row as the list does; an open Catch up card leaves when its task finishes; an archived 1:1 DM leaves Catch up and its teammate\'s count; a phone toast lifts above the tab bar only where the bar shows');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
