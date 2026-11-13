// A thread on screen draws live updates once per frame (ui/app/thread-view.ts thvLater): events still update its data at
// once and in order, the drawing waits for the next animation frame, and only the parts a change touched are drawn
// again. Twenty edits to one message in a 250-message thread build the message list once; twenty "working" status
// changes build it zero times and redraw only the status rows; a rename or a pin redraws only the header. Random bursts
// (new, edit, delete, routing, status, rename, pin) end on exactly the markup a view drawn after every event shows. A
// draft with its selection and focus, the place someone reads, the stay-at-bottom, a visual card and a closed view are
// kept. Prints the burst bench (Measures 1 and 2 of the I20 spec): list builds and milliseconds, drawing per event (as
// before) against once per frame. The milliseconds are printed, never asserted: a wall-clock ratio on a shared CI runner
// fails at random, so each promise is held by counting list builds and draws instead. Fixtures only.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Page = import('playwright').Page;
type Json = Record<string, unknown>;
type Live = [string, Json];
// Browser-side globals the page defines (ui/app) and the counters this file adds to window.
interface View { key: number; id: string; host: HTMLElement; el: HTMLElement; gone: boolean; later: unknown; messages: Json[]; botStatus: Json[]; readSent: Map<string, number> }
declare var THV_VIEWS: Set<View>;
declare var thvRowsHTML: (view: View) => string;
declare var patchHTMLTail: (root: Element, head: string, tail: string, full?: unknown) => boolean | null;
declare var thvDraw: (view: View, opts?: Json) => void;
declare var threadViewsLive: (topic: string, event: unknown) => void;
declare var threadMount: (host: HTMLElement, opts: Json) => View;
declare var thvMarkRead: (view: View) => void;
declare const window: Window & typeof globalThis & {
  __rows: Map<number, number>; __tails: number; __draws: number; __drawMs: number; __errors: string[];
};

const T = 't-big', N = 250;
// The random bursts are the slow part (each one draws a 250-message view after every event). This script runs seeds 1-4;
// thread-redraw-bursts-1..4.cts run 5-9, 10-14, 15-19 and 20-24 through TICO_REDRAW_SEEDS, so each script stays well under the limit.
const SEEDS = (process.env.TICO_REDRAW_SEEDS ?? '1-4').split('-').map(Number) as [number, number];
const now = Date.now(), at = (sec: number) => new Date(now - sec * 1000).toISOString();
const me = {member: true, following: true, notify: 'live', last_read_seq: N - 5, unread: 5, activity: false, can_post: true, can_manage: true, pinned: false};
const thread = {id: T, title: 'Launch review', audience: 'team', audience_group_id: 'ops', last_seq: N, last_message_at: at(1), me, members: []};
const PEOPLE = ['human:ana', 'human:bea', 'bot:ops', 'bot:fin'];
const BODY = (i: number) => i % 5 === 0
  ? `**Step ${i}.** The Acme invoice run needs:\n\n- the new tax table\n- totals checked against last month\n- a note to \`finance\`\n\n> Due by Friday.`
  : `Message ${i}: the release notes for build ${i} are ready, with [the changelog](https://example.test/${i}) and two fixes.`;
const VIS = 'https://visuals.test/visuals/';
const card = {id: 'f-chart', file_id: 'f-chart', version: 1, ref: 'f-chart@1', title: 'Chart', kind: 'image', name: 'chart.png', size: 1000, content_type: 'image/png',
  url: '/api/v2/files/f-chart?v=1', removed: false, current_version: 1, src: VIS + 'tok-chart', width: 800, height: 400};
const messages = () => Array.from({length: N}, (_, i) => ({id: 'm' + (i + 1), seq: i + 1, thread_id: T, sender_id: PEOPLE[i % 4], body: BODY(i + 1),
  created_at: at((N - i) * 90), reply_to: null, routed: null, revision: 0, ...(i === 10 ? {refs: {attachments: [card]}} : {})}));
const working = (n: number) => ({actor: 'bot:ops', name: 'Ops', state: 'working', detail: 'Writing a reply… step ' + n, since: at(30), run_id: 'run1', fix: null});
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=', 'base64');

async function open(browser: import('playwright').Browser) {
  const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin + '/' === VIS) return route.fulfill({contentType: 'image/png', body: PNG});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json(['ops', 'fin'].map(name => ({name, display_name: name[0]!.toUpperCase() + name.slice(1), host: 'keeper', status: 'active',
      state: 'active', can_chat: true, schedules: [], my_access: {see: true, read: true, write: true}})));
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}, {id: 'bea', name: 'Bea', email: 'bea@acme.example'}],
      org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}]});
    // No live stream: this file hands the view its events itself.
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [thread], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [thread], dms: [], next_cursor: null});
    if (p === `/api/v2/threads/${T}/timeline`) return json({thread, breadcrumb: [], next_cursor: null, messages: messages(), bot_status: [working(0)], read_marks: {[T]: N - 5}});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false} : p.endsWith('members') ? {members: [], next_cursor: null} : {ok: true});
    if (/^\/api\/v2\/threads\/[^/]+\/bot-status$/.test(p)) return json({bots: [working(0)]});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/#/thread/${T}`);
  await page.locator(`.th-msg[data-mid="m${N}"]`).waitFor();
  await page.locator('figure.vcard').waitFor();
  // Count message-list builds per view, status-row redraws, draws, and the time spent drawing (printed only).
  await page.evaluate(() => {
    window.__rows = new Map(); window.__tails = 0; window.__draws = 0; window.__drawMs = 0;
    const rows = thvRowsHTML, tail = patchHTMLTail, draw = thvDraw;
    thvRowsHTML = view => { window.__rows.set(view.key, (window.__rows.get(view.key) || 0) + 1); return rows(view); };
    patchHTMLTail = (...a) => { window.__tails++; return tail(...a); };
    thvDraw = (...a) => { window.__draws++; const t = performance.now(); try { draw(...a); } finally { window.__drawMs += performance.now() - t; } };
  });
  // Until the page has stopped drawing on its own (the card's picture loading, the first read marks).
  for (let last = -1, n = 0; n !== last;) { last = n; await page.waitForTimeout(400); n = await page.evaluate(() => [...window.__rows.values()].reduce((a, b) => a + b, 0)); }
  return {page, errors};
}
const main = (page: Page) => page.evaluate(() => [...THV_VIEWS].find(v => v.id === 't-big' && v.host.isConnected)!.key);
const reset = (page: Page) => page.evaluate(() => { window.__rows.clear(); window.__tails = 0; window.__draws = 0; window.__drawMs = 0; });
const counts = (page: Page, key: number) => page.evaluate(k => ({rows: window.__rows.get(k) || 0, tails: window.__tails, draws: window.__draws}), key);
const frame = (page: Page) => page.evaluate(() => new Promise(r => requestAnimationFrame(() => r(null))));
// Events go in within one task, as a burst off the live stream does.
// Returns the time the events took and the list builds and draws made while they came in. Draws are counted from the
// burst's start, so a draw an earlier step left waiting does not count against it.
const burstIn = (page: Page, events: Live[]) => page.evaluate(evs => {
  const sum = () => [...window.__rows.values()].reduce((a, b) => a + b, 0), was = sum(), t = performance.now();
  window.__draws = 0;
  for (const [k, d] of evs) threadViewsLive(k, d);
  return {ms: performance.now() - t, rows: sum() - was, draws: window.__draws};
}, events);
const burst = async (page: Page, events: Live[]) => (await burstIn(page, events)).ms;
const edit = (n: number, rev: number, body: string): Live => ['messages', {id: 'm' + n, thread_id: T, root_id: T,
  message: {id: 'm' + n, seq: n, thread_id: T, sender_id: PEOPLE[(n - 1) % 4], body, created_at: at((N - n + 1) * 90), revision: rev}}];
const status = (n: number): Live => ['threads', {thread_id: T, kind: 'bot_status', bots: [working(n)]}];

// A seeded mix of every live change a thread view takes.
function randomBurst(seed: number, len: number, ids: string[], nextSeq: {n: number}): Live[] {
  let s = seed;
  const rnd = (k: number) => { s = (s * 1103515245 + 12345) % 2147483648; return s % k; };
  const out: Live[] = [];
  for (let i = 0; i < len; i++) {
    const kind = rnd(8), id = ids[rnd(ids.length)]!;
    if (kind === 0 || kind === 1) {
      const n = nextSeq.n++, mid = 'n' + n;
      ids.push(mid);
      out.push(['messages', {id: mid, thread_id: T, root_id: T, message: {id: mid, seq: n, thread_id: T, sender_id: PEOPLE[rnd(4)], body: `New ${n} from a burst`, created_at: at(0), revision: 0}}]);
      // Sometimes edited and deleted in the same burst.
      if (rnd(3) === 0) out.push(['messages', {id: mid, thread_id: T, root_id: T, message: {id: mid, seq: n, thread_id: T, sender_id: 'bot:ops', body: `New ${n}, edited`, created_at: at(0), revision: 1}}]);
      if (rnd(3) === 0) out.push(['messages', {id: mid, thread_id: T, root_id: T, deleted: true, revision: 2}]);
    } else if (kind === 2) out.push(['messages', {id, thread_id: T, root_id: T, message: {id, seq: Number(id.slice(1)), thread_id: T, sender_id: 'bot:fin', body: `Edited ${seed}.${i}`, created_at: at(600), revision: 10 + seed * 50 + i}}]);
    else if (kind === 3) out.push(['messages', {id, thread_id: T, root_id: T, deleted: true, revision: 1000 + seed * 50 + i}]);
    else if (kind === 4) out.push(['threads', {kind: 'routed', thread_id: T, message_id: id, routed: {to: [{actor: 'bot:ops', name: 'Ops'}]}}]);
    else if (kind === 5) out.push(['threads', {thread_id: T, kind: 'bot_status', bots: rnd(3) ? [working(i), {actor: 'bot:fin', name: 'Fin', state: 'up_next', detail: 'After Ops', since: at(5), run_id: null, fix: null}] : []}]);
    else if (kind === 6) out.push(['threads', {thread_id: T, thread: {title: `Launch review ${seed}.${i}`}}]);
    else out.push(['threads', {thread_id: T, kind: 'pinned', pinned: !!rnd(2)}]);
  }
  return out;
}

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  const {page, errors} = await open(browser);
  const A = await main(page);

  // ---- Measure 1 (AC-1): 20 edits to one message within one frame build the list once and show the last edit.
  const before1 = await page.evaluate(([k, evs]) => {
    window.__rows.clear(); window.__drawMs = 0;
    const view = [...THV_VIEWS].find(v => v.key === k)!, t = performance.now();
    // As before this change: each event drawn on the spot.
    for (const [kind, d] of evs) { threadViewsLive(kind, d); thvDraw(view); }
    return {ms: performance.now() - t, rows: window.__rows.get(k) || 0};
  }, [A, Array.from({length: 20}, (_, i) => edit(N - 3, i + 1, `Before ${i + 1}`))] as [number, Live[]]);
  await reset(page);
  // One draw of a changed list, the median of three so one slow draw does not set the bar.
  const one = await page.evaluate(([k, evs]) => {
    const view = [...THV_VIEWS].find(v => v.key === k)!, ms = evs.map(ev => { const t = performance.now(); threadViewsLive(ev[0], ev[1]); thvDraw(view); return performance.now() - t; });
    return ms.sort((a, b) => a - b)[1]!;
  }, [A, [100, 101, 102].map(r => edit(N - 3, r, `One draw ${r}`))] as [number, Live[]]);
  await reset(page);
  const edits = Array.from({length: 20}, (_, i) => edit(N - 3, 200 + i, `Edit number ${i + 1}`));
  const {ms: liveMs, rows: during, draws: drawsDuring} = await burstIn(page, edits);
  assert.equal(during, 0, 'nothing is drawn while the events come in');
  assert.equal(drawsDuring, 0, 'no draw runs while the events come in');
  await frame(page);
  const drawMs1 = await page.evaluate(() => window.__drawMs);
  const after1 = {...await counts(page, A), ms: liveMs + drawMs1};
  assert.equal(after1.rows, 1, 'the list is built once for the frame');
  assert.match(await page.locator(`.th-msg[data-mid="m${N - 3}"] .th-body`).innerText(), /Edit number 20/);
  assert.equal(before1.rows, 20);
  // The spec's AC-1 target (the burst costs at most about two draws) held by count, not by the clock: the 20 edits make
  // one draw and one list build, where drawing each edit made 20 of each.
  assert.equal(after1.draws, 1, 'the 20 edits are drawn once, by the frame');

  // ---- Measure 2 (AC-2): 20 status changes build no list and redraw the status rows once.
  await reset(page);
  const before2 = await page.evaluate(k => {
    const view = [...THV_VIEWS].find(v => v.key === k)!, t = performance.now();
    for (let i = 0; i < 20; i++) { view.botStatus = [{actor: 'bot:ops', name: 'Ops', state: 'working', detail: 'Before ' + i, since: new Date().toISOString(), run_id: 'run1', fix: null}]; thvDraw(view); }
    return {ms: performance.now() - t, rows: window.__rows.get(k) || 0};
  }, A);
  await reset(page);
  const rowsBefore = await page.locator('.thv-list .th-msg').evaluateAll(els => { for (const e of els) (e as HTMLElement & {keep?: number}).keep = 1; return els.length; });
  await page.locator('figure.vcard').evaluate(e => { (e as HTMLElement & {keep?: number}).keep = 1; });
  const statusMs = await burst(page, Array.from({length: 20}, (_, i) => status(i + 1)));
  await frame(page);
  const after2 = {...await counts(page, A), ms: statusMs + await page.evaluate(() => window.__drawMs)};
  assert.deepEqual({rows: after2.rows, tails: after2.tails, draws: after2.draws}, {rows: 0, tails: 1, draws: 1}, 'only the status rows are drawn, once');
  assert.match(await page.locator('.th-status .th-status-detail').innerText(), /step 20/);
  assert.equal(await page.locator('.thv-list > :last-child').getAttribute('data-th-status'), 'bot:ops', 'the status rows stay at the end of the timeline');
  assert.equal(await page.locator('.thv-list > :last-child').getAttribute('role'), 'status');
  // AC-9: no message row, and no visual card, is drawn again.
  assert.equal(await page.locator('.thv-list .th-msg').evaluateAll(els => els.filter(e => (e as HTMLElement & {keep?: number}).keep === 1).length), rowsBefore);
  assert.equal(await page.locator('figure.vcard').evaluate(e => (e as HTMLElement & {keep?: number}).keep), 1, 'the visual card keeps its node');
  // A status change after the list changed again still lands after the rows.
  await burst(page, [edit(N, 1, 'Last one, edited'), status(21)]);
  await frame(page);
  await burst(page, [status(22)]);
  await frame(page);
  assert.match(await page.locator(`.th-msg[data-mid="m${N}"] .th-body`).innerText(), /Last one, edited/);
  assert.match(await page.locator('.th-status .th-status-detail').innerText(), /step 22/);
  assert.equal(await page.locator('.th-status').count(), 1);
  // No status rows, then one again.
  await burst(page, [['threads', {thread_id: T, kind: 'bot_status', bots: []}]]);
  await frame(page);
  assert.equal(await page.locator('.th-status').count(), 0);
  await burst(page, [status(23)]);
  await frame(page);
  assert.equal(await page.locator('.thv-list > :last-child').getAttribute('data-th-status'), 'bot:ops');

  // ---- AC-3: a rename and a pin redraw the header alone.
  await reset(page);
  await burst(page, [['threads', {thread_id: T, thread: {title: 'Launch review, renamed'}}], ['threads', {thread_id: T, kind: 'pinned', pinned: true}]]);
  await frame(page);
  assert.equal((await counts(page, A)).rows, 0, 'no list build for a rename or a pin');
  assert.match(await page.locator('.thv-title').innerText(), /Launch review, renamed/);
  assert.equal(await page.locator('.thv-head [data-thv-pin]').getAttribute('aria-pressed'), 'true');
  // A change the rows show (posting closed: no Reply) draws them too.
  await burst(page, [['threads', {thread_id: T, thread: {me: {...me, can_post: false}}}]]);
  await frame(page);
  assert.equal(await page.locator('.thv-list [data-th-reply]').count(), 0);
  await burst(page, [['threads', {thread_id: T, thread: {me: {...me, pinned: true}}}]]);
  await frame(page);
  assert.ok(await page.locator('.thv-list [data-th-reply]').count() > 0);

  // A thread with no name is named by who wrote in it: a new author's message changes the header too.
  await burst(page, [['threads', {thread_id: T, thread: {title: '', audience: 'participants', dm_with: [], dm_with_count: 0}}]]);
  await frame(page);
  const untitled = await page.locator('.thv-title').innerText();
  assert.doesNotMatch(untitled, /\+1/);
  await burst(page, [['messages', {id: 'u1', thread_id: T, root_id: T, message: {id: 'u1', seq: 900, thread_id: T, sender_id: 'human:cy', body: 'A new author', created_at: at(0), revision: 0}}]]);
  await frame(page);
  assert.match(await page.locator('.thv-title').innerText(), /\+1/, `the header names the new author: ${untitled}`);
  await burst(page, [['threads', {thread_id: T, thread: {title: 'Launch review, renamed', audience: 'team'}}]]);
  await frame(page);
  // Routing alone, with no message event in the burst, draws the line under that person's message.
  assert.equal(await page.locator(`.th-msg[data-mid="m${N - 4}"] .th-routed`).count(), 0);
  await burst(page, [['threads', {kind: 'routed', thread_id: T, message_id: 'm' + (N - 4), routed: {to: [{actor: 'bot:ops', name: 'Ops'}]}}]]);
  await frame(page);
  assert.match(await page.locator(`.th-msg[data-mid="m${N - 4}"] .th-routed`).innerText(), /Asked Ops/);
  // Read marks wait for the draw that shows the messages: a mark asked for meanwhile (a tab coming back) is sent by the frame.
  const marks = await page.evaluate(([k, ev]) => {
    const view = [...THV_VIEWS].find(v => v.key === k)!;
    threadViewsLive(ev[0], ev[1]);
    thvMarkRead(view);
    return view.readSent.get('t-big') || 0;
  }, [A, ['messages', {id: 'u2', thread_id: T, root_id: T, message: {id: 'u2', seq: 901, thread_id: T, sender_id: 'bot:fin', body: 'Read after the draw', created_at: at(0), revision: 0}}]] as [number, Live]);
  assert.ok(marks < 901, `not marked read before it is drawn: ${marks}`);
  await frame(page);
  assert.equal(await page.evaluate(k => [...THV_VIEWS].find(v => v.key === k)!.readSent.get('t-big'), A), 901, 'marked read by the frame that drew it');

  // ---- AC-5: a draw someone's action makes shows at once and takes the frame's waiting draw with it.
  await reset(page);
  // In one task, so no frame comes between the event and the action.
  await page.evaluate(([k, ev]) => { threadViewsLive(ev[0], ev[1]); thvDraw([...THV_VIEWS].find(v => v.key === k)!); }, [A, edit(N - 1, 1, 'Drawn by the action')] as [number, Live]);
  assert.match(await page.locator(`.th-msg[data-mid="m${N - 1}"] .th-body`).innerText(), /Drawn by the action/, 'shown without waiting for the frame');
  await frame(page);
  assert.equal((await counts(page, A)).rows, 1, 'the waiting draw does not run again');

  // ---- AC-6: a draft with text selected keeps its words, selection and focus through 20 updates.
  const box = page.locator('.thv-composer .p-text');
  await box.click();
  await box.fill('Draft about the tax table');
  await box.evaluate((e: HTMLTextAreaElement) => e.setSelectionRange(6, 11));
  await burst(page, [...Array.from({length: 10}, (_, i) => edit(N - 2, 300 + i, `Typing burst ${i}`)), ...Array.from({length: 10}, (_, i) => status(30 + i))]);
  await frame(page);
  assert.deepEqual(await box.evaluate((e: HTMLTextAreaElement) => [e.value, e.selectionStart, e.selectionEnd, document.activeElement === e]),
    ['Draft about the tax table', 6, 11, true]);
  await box.fill('');

  // ---- AC-7: at the bottom, a burst of new messages keeps it there; scrolled up to read, the place stays.
  const scroll = page.locator('.thv-scroll');
  await scroll.evaluate(e => { e.scrollTop = e.scrollHeight; });
  const seq = {n: 1000};
  const fresh = (k: number): Live[] => Array.from({length: k}, () => { const n = seq.n++; return ['messages', {id: 'n' + n, thread_id: T, root_id: T,
    message: {id: 'n' + n, seq: n, thread_id: T, sender_id: 'bot:fin', body: BODY(5) + ' ' + n, created_at: at(0), revision: 0}}] as Live; });
  await burst(page, fresh(8));
  await frame(page);
  assert.ok(await scroll.evaluate(e => e.scrollHeight - e.scrollTop - e.clientHeight < 2), 'still at the bottom');
  await scroll.evaluate(e => { e.scrollTop = 900; });
  await burst(page, [...fresh(8), status(50)]);
  await frame(page);
  assert.equal(await scroll.evaluate(e => e.scrollTop), 900, 'what they read does not move');

  // ---- AC-4 and AC-8: random bursts end on the same markup as a view drawn after every event: same rows, order,
  // "New" line, roles and aria attributes, and the same title and pin (the page's own view has a back link and member count
// the second one leaves out).
  const B = await page.evaluate(() => {
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:0;top:0;width:900px;height:600px;opacity:0;pointer-events:none';
    document.body.append(host);
    return threadMount(host, {id: 't-big'}).key;
  });
  await page.waitForFunction(k => { const v = [...THV_VIEWS].find(x => x.key === k); return !!v && v.el.querySelector('.th-msg[data-mid="m250"]'); }, B);
  // Bring the second view to the first one's data: the same events so far, read again from the first view.
  await page.evaluate(([a, b]) => {
    const [va, vb] = [a, b].map(k => [...THV_VIEWS].find(v => v.key === k)!) as [View, View];
    for (const f of ['messages', 'botStatus', 'thread', 'readAt', 'marks', 'pending'] as const) (vb as unknown as Json)[f] = structuredClone((va as unknown as Json)[f]);
    thvDraw(vb); thvDraw(va);
  }, [A, B]);
  const snap = (k: number) => page.evaluate(key => {
    const v = [...THV_VIEWS].find(x => x.key === key)!;
    // Clocks and "5m ago" read the time of their draw, which differs between the two views by a few milliseconds; links
    // get `data-mi` from a pass after the draw (mediaInline, viewer.ts), which may not have reached the newer copy yet.
    const norm = (s: string) => s.replaceAll(`th-upd-${v.key}-`, 'th-upd-K-').replace(/(data-th-since="[^"]*">)[^<]*/g, '$1').replace(/(<time\b[^>]*>)[^<]*/g, '$1').replaceAll(' data-mi="1"', '');
    return {list: norm(v.el.querySelector('.thv-list')!.innerHTML), title: v.el.querySelector('.thv-title')!.outerHTML, pin: v.el.querySelector('[data-thv-pin]')?.outerHTML,
      placeholder: v.el.querySelector('.thv-composer .p-text')?.getAttribute('placeholder')};
  }, k);
  assert.deepEqual(await snap(B), await snap(A), 'the two views start the same');
  const ids = Array.from({length: N}, (_, i) => 'm' + (i + 1));
  for (let seed = SEEDS[0]; seed <= SEEDS[1]; seed++) {
    const events = randomBurst(seed, 1 + (seed * 7) % 12, ids, seq);
    // B: drawn after each event, as before this change. A: drawn by the frame.
    await page.evaluate(([b, evs]) => { const vb = [...THV_VIEWS].find(v => v.key === b)!; for (const [kind, d] of evs) { threadViewsLive(kind, d); thvDraw(vb); } }, [B, events] as [number, Live[]]);
    await frame(page);
    assert.equal(await page.evaluate(a => !![...THV_VIEWS].find(v => v.key === a)!.later, A), false);
    assert.deepEqual(await snap(A), await snap(B), `burst ${seed} ends where drawing each event does: ${JSON.stringify(events.map(e => e[1].kind || (e[1].deleted ? 'delete' : e[1].message ? 'message' : 'thread')))}`);
  }
  assert.ok(await page.locator('.thv-list .th-newline[role="separator"]').count() >= 1, 'the New line is drawn');

  // ---- AC-10: a view closed or off the page draws nothing for later events, and no error shows.
  await reset(page);
  await page.evaluate(b => { [...THV_VIEWS].find(v => v.key === b)!.host.remove(); }, B);
  await burst(page, [edit(N - 3, 5000, 'After the second view left'), status(60)]);
  await frame(page);
  assert.equal((await counts(page, B)).rows, 0, 'nothing is built for a view off the page');
  assert.equal(await page.evaluate(b => [...THV_VIEWS].some(v => v.key === b), B), false, 'it is closed');
  assert.equal((await counts(page, A)).rows, 1);

  assert.deepEqual(errors, []);
  console.log('thread redraw bench, 250 messages:');
  console.log(`  20 edits to one message: per event ${before1.rows} list builds, ${before1.ms.toFixed(1)} ms; per frame ${after1.rows} list build, ${after1.ms.toFixed(1)} ms; one draw ${one.toFixed(1)} ms`);
  console.log(`  20 status changes: per event ${before2.rows} list builds, ${before2.ms.toFixed(1)} ms; per frame ${after2.rows} list builds, ${after2.tails} status redraw, ${after2.ms.toFixed(1)} ms`);
  console.log('thread redraw: ok');
})().then(() => browser?.close(), async e => { console.error(e); await browser?.close(); process.exit(1); });
