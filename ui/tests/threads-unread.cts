// The Threads rail's Unread section (ui/app/thread-rail.ts, ui/app/threads.ts): each chat Catch up counts that the rail
// does not list (the rail's `unread`: a 1:1 DM with a bot or a person, a bot's Home, a task's thread) has a row under
// Threads, with the bot's or person's face and name (a task's thread: its mark and title) and the number of new messages,
// "@" for a mention. Eight rows, then "N more"; Find a thread filters them and lifts the cap; no heading when none.
// Opening a row keeps it in place, current, with no count, until another row is opened. On a desktop, Mark read (on
// hover) takes the row and its badge count off at once and opens nothing; a phone has no Mark read and 44px rows. While
// the pointer is on the list a new chat goes last and nothing moves; it re-sorts when the pointer leaves. Every title on
// the rail starts at the row's left + 38px and the counts end at one x. A server without `unread` draws the rail as before.
// Screenshots go to TICO_SCREENSHOT_DIR (default: a threads-unread folder in the OS temp directory).
import type {Browser, Page, Route} from 'playwright';
declare var THREADS: {gen: number; cu: Set<string> | null};
declare function threadsLoad(): Promise<void>;
interface Me {member: boolean; following: boolean; notify: string; last_read_seq: number; unread: number; activity: boolean; can_post: boolean; can_manage: boolean; pinned: boolean}
interface Chat {id: string; title: string; audience: string; dm_with?: {actor: string; name: string}[]; dm_with_count?: number; home_actor?: string | null;
  task_id?: string | null; new_count: number; mention: boolean; at: number; read?: boolean}
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts');
const {unrouted} = require('./support/routes.cts');

const shots = process.env.TICO_SCREENSHOT_DIR || path.join(require('node:os').tmpdir(), 'threads-unread-shots');
const at = (min: number) => new Date(Date.now() - min * 60000).toISOString();
const me = (extra: Partial<Me> = {}): Me => ({member: true, following: true, notify: 'live', last_read_seq: 0, unread: 0, activity: false, can_post: true, can_manage: false, pinned: false, ...extra});
// The one thread on the rail: Launch plan, with two new messages.
const plan = {id: 't-plan', title: 'Launch plan', audience: 'members', root_id: 't-plan', path: ['t-plan'], parent_id: null, last_seq: 4, last_unread_seq: 4,
  last_message_at: at(30), me: me({unread: 2}), members: [{actor: 'human:ana'}, {actor: 'human:ben'}], pinned_for_everyone: false};
const botChat = (slug: string, name: string, n: number, min: number): Chat =>
  ({id: 'dm-' + slug, title: 'Chat with ' + name, audience: 'personal', dm_with: [{actor: 'bot:' + slug, name}], dm_with_count: 1, new_count: n, mention: false, at: min});
// Catch up's chats off the rail, in its order: the task's mention first, then newest.
const chats = (): Chat[] => [
  {id: 't-task', title: 'Pricing review', audience: 'task', task_id: 'task-9', new_count: 1, mention: true, at: 50},
  botChat('growth', 'Growth Manager', 3, 1),
  {id: 'dm-ben', title: '', audience: 'personal', dm_with: [{actor: 'human:ben', name: 'Ben'}], dm_with_count: 1, new_count: 1, mention: false, at: 2},
  {id: 'home-seo', title: '', audience: 'home', home_actor: 'bot:seo', new_count: 2, mention: false, at: 3},
];
const twelve = (): Chat[] => Array.from({length: 12}, (_, i) => botChat('b' + i, 'Bot ' + i, i + 1, i + 1));
const item = (c: Chat) => ({id: c.id, title: c.title, name: c.title, audience: c.audience, dm_with: c.dm_with || [], dm_with_count: c.dm_with_count || 0,
  home_actor: c.home_actor ?? null, new_count: c.new_count, mention: c.mention, task_id: c.task_id ?? null, last_message_at: at(c.at)});
const view = (c: Chat) => ({id: c.id, title: c.title, audience: c.audience, root_id: c.id, path: [c.id], parent_id: null, last_seq: 3, last_unread_seq: 3,
  last_message_at: at(c.at), me: me({unread: c.read ? 0 : c.new_count}), members: [], pinned_for_everyone: false, task_id: c.task_id ?? null,
  dm_with: c.dm_with, dm_with_count: c.dm_with_count, ...(c.home_actor ? {home: {actor: c.home_actor}} : {})});

async function open(browser: Browser, {viewport = {width: 1280, height: 800}, phone = false, world = chats(), unreadField = true} = {}) {
  const page = await browser.newPage({viewport, isMobile: phone, hasTouch: phone, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const seen: {errors: string[]; readAll: string[]; rails: number} = {errors: [], readAll: [], rails: 0}, streams: Route[] = [];
  let seq = 0;
  page.on('pageerror', e => seen.errors.push(e.message));
  const unread = () => world.filter(c => !c.read).sort((a, b) => Number(b.mention) - Number(a.mention) || a.at - b.at);
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json(['growth', 'seo', ...Array.from({length: 13}, (_, i) => 'b' + i)].map(name => ({name, display_name: name === 'growth' ? 'Growth Manager' : name === 'seo' ? 'SEO Owner' : name.toUpperCase(),
      host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [], my_access: {see: true, read: true, write: true}})));
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'ben', name: 'Ben', email: 'ben@acme.example'}], org_groups: []});
    if (p === '/api/v2/events') { streams.push(route); return; }        // held open until the test sends
    if (p === '/api/v2/slack/links') return json({links: [], available: false, next_cursor: null});
    if (p === '/api/v2/threads/rail') {
      seen.rails++;
      const ids = [plan.id, ...unread().map(c => c.id)];
      return json({pinned: [], threads: [plan], catch_up: ids.length, catch_up_ids: ids, dms: {}, next_cursor: null, ...(unreadField ? {unread: unread().map(item)} : {})});
    }
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [plan], dms: [], bot_dms: {}, next_cursor: null});
    if (p === '/api/v2/threads/catch-up') return json({items: [], count: 0, next_cursor: null});
    if (p === '/api/v2/threads/browse') return json({threads: [], next_cursor: null});
    const m = p.match(/^\/api\/v2\/threads\/([^/]+)(?:\/([a-z-]+))?$/);
    const c = m && (m[1] === plan.id ? null : world.find(x => x.id === m[1]));
    const t = m && (m[1] === plan.id ? plan : c ? view(c) : null);
    if (t) {
      const what = m![2];
      if (what === 'timeline') return json({thread: t, breadcrumb: [{id: t.id, title: t.title}], next_cursor: null,
        messages: [{id: 'x1-' + t.id, seq: 1, thread_id: t.id, sender_id: 'human:ben', body: 'Morning.', created_at: at(10), depth: 0}]});
      if (what === 'members') return json({members: [{actor: 'human:ana', role: 'owner'}], next_cursor: null});
      if (what === 'read') { if (c) c.read = true; return json({ok: true}); }
      if (what === 'read-all') { seen.readAll.push(t.id); if (c) c.read = true; return json({threads: 1}); }
      if (what === 'calls') return json({calls: [], next_before: null, available: false});
      if (!what && method === 'GET') return json(t);
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
    for (const old of streams.splice(0)) await old.abort().catch(() => {});
    await stream.fulfill({contentType: 'text/event-stream', body: blocks.join('')});
  };
  const block = (event: string, data: Record<string, unknown>) => `id: ${++seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;
  return {page, seen, send, block, world};
}
async function shot(page: Page, name: string) {
  fs.mkdirSync(shots, {recursive: true});
  await page.screenshot({path: path.join(shots, `${name}.png`)});
}
const until = async (check: () => unknown, what: string) => {
  for (let i = 0; i < 100; i++) { if (await check()) return; await new Promise(r => setTimeout(r, 50)); }
  assert.fail('timed out: ' + what);
};
const rows = (page: Page) => page.locator('[data-list="unread"] .tr-row').evaluateAll(els => els.map(a => (a as HTMLElement).dataset.thread));
const names = (page: Page) => page.locator('[data-list="unread"] .tr-title').allTextContents();
const badge = (page: Page) => page.locator('[data-tr-catch] .tr-badge').textContent();

let browser: Browser = null!;
(async () => {
  browser = await chromium.launch();

  // Desktop: the rows, in Catch up's order, with faces, names and counts; titles and counts line up with Threads.
  {
    const {page, seen} = await open(browser);
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-list="unread"] .tr-row').first().waitFor();
    assert.equal(await page.locator('[data-sec="unread"]').isVisible(), true);
    assert.deepEqual(await rows(page), ['t-task', 'dm-growth', 'dm-ben', 'home-seo'], 'the mention first, then newest');
    assert.deepEqual(await names(page), ['Pricing review', 'Growth Manager', 'Ben', 'SEO Owner']);
    assert.deepEqual(await page.locator('[data-list="unread"] .tr-count').allTextContents(), ['@', '3', '1', '2']);
    assert.equal(await badge(page), '5', 'the badge counts the thread on the rail and the four chats under Unread');
    // A bot's and a person's chat show their avatar; the task's thread its mark.
    assert.equal(await page.locator('[data-thread="dm-growth"] .tr-mark .nav-icon').count(), 0);
    assert.equal(await page.locator('[data-thread="t-task"] .tr-mark .th-mark').count(), 1);
    // Unread is below Threads.
    const threadsTop = (await page.locator('[data-list="threads"]').boundingBox())!.y, unreadTop = (await page.locator('[data-list="unread"]').boundingBox())!.y;
    assert.ok(unreadTop > threadsTop, 'Unread goes below Threads');
    // Every title starts at the row's left + 38px, and the counts end at one x.
    const geometry = await page.evaluate(() => [...document.querySelectorAll('#thread-rail .tr-row')].map(a => {
      const row = a.getBoundingClientRect(), title = a.querySelector('.tr-title')!.getBoundingClientRect(), count = a.querySelector('.tr-count')?.getBoundingClientRect();
      return {title: Math.round(title.left - row.left), count: count ? Math.round(count.right) : null};
    }));
    assert.deepEqual([...new Set(geometry.map(g => g.title))], [38], JSON.stringify(geometry));
    assert.equal(new Set(geometry.map(g => g.count).filter(x => x !== null)).size, 1, JSON.stringify(geometry));
    await shot(page, 'threads-unread-desktop');

    // Mark read shows on hover, takes the row and one off the badge at once, and opens nothing.
    // A mention row has no Mark read: reading does not settle an open mention, so the row would come back.
    assert.equal(await page.locator('[data-tr-read="t-task"]').count(), 0);
    const route = await page.evaluate(() => location.hash);
    await page.locator('[data-thread="dm-ben"]').hover();
    const read = page.locator('[data-tr-read="dm-ben"]');
    assert.equal(await read.getAttribute('title'), 'Mark read');
    await until(async () => (await read.evaluate(el => getComputedStyle(el).opacity)) === '1', 'Mark read shows on hover');
    await shot(page, 'threads-unread-hover');
    await read.click();
    await page.locator('[data-list="unread"] [data-thread="dm-ben"]').waitFor({state: 'detached'});
    assert.equal(await badge(page), '4');
    assert.equal(await page.evaluate(() => location.hash), route, 'Mark read opens nothing');
    await until(() => seen.readAll.includes('dm-ben'), 'Mark read reads the chat on the server');

    // Opening a row: current, no count, and it keeps its place once read, until another row is opened.
    await page.locator('[data-list="unread"] [data-thread="dm-growth"]').click();
    await page.waitForFunction(() => location.hash === '#/thread/dm-growth');
    const opened = page.locator('[data-list="unread"] [data-thread="dm-growth"]');
    await until(async () => (await opened.getAttribute('class'))?.includes('cur'), 'the opened row is current');
    assert.equal(await opened.locator('.tr-count').count(), 0, 'its count goes');
    assert.ok(!(await opened.getAttribute('class'))!.includes('unread'));
    await page.evaluate(() => { THREADS.cu?.delete('dm-growth'); });
    await page.evaluate(() => threadsLoad());
    await page.locator('[data-tr-catch]').hover();
    assert.deepEqual(await rows(page), ['t-task', 'dm-growth', 'home-seo'], 'read, it stays in its place while open');
    await page.locator('[data-list="unread"] [data-thread="home-seo"]').click();
    await page.waitForFunction(() => location.hash === '#/thread/home-seo');
    await until(async () => !(await rows(page)).includes('dm-growth'), 'it leaves once another row is opened');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // Twelve chats: eight rows and "4 more", which shows all twelve; Find a thread filters them and lifts the cap.
  {
    const {page, seen} = await open(browser, {world: [...twelve(), {id: 'home-seo', title: '', audience: 'home', home_actor: 'bot:seo', new_count: 1, mention: false, at: 30}]});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-list="unread"] .tr-row').first().waitFor();
    assert.equal((await rows(page)).length, 8);
    const more = page.locator('[data-tr-unread-more]');
    assert.equal(await more.textContent(), '5 more');
    await page.fill('#tr-find', 'seo');
    assert.deepEqual(await names(page), ['SEO Owner'], 'Find keeps the SEO Owner row and hides the others');
    assert.equal(await more.isVisible(), false);
    await page.fill('#tr-find', '');
    await more.click();
    assert.equal((await rows(page)).length, 13);
    assert.equal(await more.isVisible(), false);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // While the pointer is on the list a new chat goes last and nothing moves; it re-sorts when the pointer leaves.
  {
    const {page, seen, send, block, world} = await open(browser);
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-list="unread"] .tr-row').first().waitFor();
    const before = await rows(page);
    await page.locator('[data-list="unread"] [data-thread="home-seo"]').hover();
    world.push(botChat('fresh', 'Bug Triage', 1, 0));
    await send(block('threads', {kind: 'activity', thread_id: 'dm-fresh', root_id: 'dm-fresh', message_id: 'f1', unread: true, unread_count: 1,
      thread: {id: 'dm-fresh', title: 'Chat with Bug Triage', audience: 'personal', dm_with: [{actor: 'bot:b12', name: 'Bug Triage'}], dm_with_count: 1}}));
    const t0 = Date.now();
    await until(async () => (await rows(page)).includes('dm-fresh'), 'the new chat shows without a reload');
    assert.ok(Date.now() - t0 < 5000, 'within 5 seconds');
    assert.deepEqual(await rows(page), [...before, 'dm-fresh'], 'held: the new chat goes last and nothing moves');
    await page.mouse.move(900, 400);
    await until(async () => (await rows(page))[1] === 'dm-fresh', 'it re-sorts when the pointer leaves');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // Phone: 44px rows and no Mark read.
  {
    const {page, seen} = await open(browser, {viewport: {width: 390, height: 844}, phone: true});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-list="unread"] .tr-row').first().waitFor();
    const heights = await page.locator('[data-list="unread"] .tr-row').evaluateAll(els => els.map(a => Math.round(a.getBoundingClientRect().height)));
    assert.deepEqual([...new Set(heights)], [44]);
    assert.equal(await page.locator('[data-tr-read]').count(), 0, 'a phone has no Mark read');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'no sideways scroll at 390px');
    await shot(page, 'threads-unread-phone');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // A touch tablet (wider than a phone, no hover): every count shows, and no Mark read covers it, so a tap on a row's right
  // edge opens the chat.
  {
    const {page, seen} = await open(browser, {viewport: {width: 1024, height: 768}, phone: true});
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('[data-list="unread"] .tr-row').first().waitFor();
    assert.deepEqual(await page.locator('[data-list="unread"] .tr-count').allTextContents(), ['@', '3', '1', '2']);
    const covered = await page.locator('[data-list="unread"] .tr-count').evaluateAll(els => els.map(el => {
      const box = el.getBoundingClientRect(), top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      return top === el || el.contains(top) ? '' : String(top?.className || top?.tagName);
    }));
    assert.deepEqual(covered, ['', '', '', ''], 'nothing covers a count');
    await shot(page, 'threads-unread-tablet');
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  // Nothing off the rail unread: no Unread heading. A server without `unread`: the rail as before, no error.
  for (const opts of [{world: []}, {unreadField: false}]) {
    const {page, seen} = await open(browser, opts);
    await page.goto('https://tico-ui.test/#/threads');
    await page.locator('.tr-row[data-thread="t-plan"]').waitFor();
    assert.equal(await page.locator('[data-sec="unread"]').isVisible(), false);
    assert.equal(await page.locator('[data-list="unread"] .tr-row').count(), 0);
    assert.equal(await page.locator('[data-tr-unread-more]').isVisible(), false);
    assert.deepEqual(seen.errors, []);
    await page.close();
  }

  console.log('PASS: Threads Unread: every chat Catch up counts off the rail has a row under Threads (face, name, count or @, mention first); titles at +38px and counts aligned; Mark read on hover takes it off at once and opens nothing; an opened row stays current in place until another opens; 8 rows then "N more"; Find filters and lifts the cap; a new chat goes last while the pointer is on the list and re-sorts after; phone rows 44px with no Mark read; no heading when none; a server without `unread` draws the rail as before');
})().catch(e => { console.error(e); process.exitCode = 1; }).finally(() => browser?.close());
