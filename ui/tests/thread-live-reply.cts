// A bot's reply streams into the thread as it is written (ui/app/thread-view.ts thvLiveStep): one live row per run,
// under the bot's status row, from the run's text deltas and messages on the `runs` topic. The row grows with each
// delta, rendered as the message's markdown, redrawn at most once per short window however many deltas come; it keeps
// to the bottom only for a reader already there; the posted message takes its place in the same frame; a run that ends
// with no message loses its row; thinking and a run already under way when the page opened show nothing. Fixtures only.
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Json = Record<string, unknown>;
declare var liveEmit: (topic: string, data: unknown) => void;
declare var patchHTMLTail: (root: Element, head: string, tail: string, full?: unknown) => boolean | null;
declare const window: Window & typeof globalThis & {__tails: number};

const T = 't-live', N = 40;
const now = Date.now(), at = (sec: number) => new Date(now - sec * 1000).toISOString();
const me = {member: true, following: true, notify: 'live', last_read_seq: N, unread: 0, activity: false, can_post: true, can_manage: true, pinned: false};
const thread = {id: T, title: 'Renewals', audience: 'team', audience_group_id: 'ops', last_seq: N, last_message_at: at(1), me, members: []};
const messages = () => Array.from({length: N}, (_, i) => ({id: 'm' + (i + 1), seq: i + 1, thread_id: T, sender_id: i % 2 ? 'bot:ops' : 'human:ana',
  body: `Message ${i + 1} about the Acme renewal and its dates.`, created_at: at((N - i) * 60), reply_to: null, routed: null, revision: 0}));
const working = {actor: 'bot:ops', name: 'Ops', state: 'working', detail: 'Writing a reply', since: at(5), run_id: 'run1', fix: null};

const run = (id: string, more: Json) => ({run_id: id, conversation_id: T, bot_id: 'ops', ...more});
const delta = (id: string, seq: number, text: string, kind = 'text') => run(id, {output: {id: 900 + seq, seq, kind: 'delta', created_at: at(0), payload: {text, delta_kind: kind}}});

let browser: import('playwright').Browser | null = null;
(async () => {
  browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined) as string});
  const page = await browser.newPage({viewport: {width: 1100, height: 700}, serviceWorkers: 'block', reducedMotion: 'reduce'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', async route => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', registered: true});
    if (p === '/api/employees') return json([{name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: [],
      my_access: {see: true, read: true, write: true}}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana', email: 'ana@acme.example'}], org_groups: [{id: 'ops', name: 'Ops team', my_access: {see: true, read: true, write: true}}]});
    // No live stream: this file hands the page its events itself.
    if (p === '/api/v2/events') return route.abort();
    if (p === '/api/v2/threads/rail') return json({pinned: [], threads: [thread], catch_up: 0, next_cursor: null, dms: {}});
    if (p === '/api/v2/threads' && method === 'GET') return json({attention: [], threads: [thread], dms: [], next_cursor: null});
    if (p === `/api/v2/threads/${T}/timeline`) return json({thread, breadcrumb: [], next_cursor: null, messages: messages(), bot_status: [working], read_marks: {[T]: N}});
    if (/^\/api\/v2\/threads\/[^/]+\/(read|members|calls)$/.test(p)) return json(p.endsWith('calls') ? {calls: [], next_before: null, available: false} : p.endsWith('members') ? {members: [], next_cursor: null} : {ok: true});
    if (/^\/api\/v2\/threads\/[^/]+\/bot-status$/.test(p)) return json({bots: [working]});
    if (p === '/api/v2/rule-holds') return json({holds: [], next_cursor: null});
    return json(unrouted(p));
  });
  await page.goto(`https://tico-ui.test/#/thread/${T}`);
  await page.locator(`.th-msg[data-mid="m${N}"]`).waitFor();
  await page.locator('[data-th-status="bot:ops"]').waitFor();
  const emit = (topic: string, data: unknown) => page.evaluate(([t, d]) => liveEmit(t as string, d), [topic, data] as [string, unknown]);
  const live = page.locator('.th-live[data-th-live="run1"]');
  const scroll = page.locator('.thv-scroll');
  await scroll.evaluate(e => { e.scrollTop = e.scrollHeight; });

  // ---- The row starts with the run's first words, under the bot's status row, and grows with each delta.
  await emit('runs', run('run1', {state: 'running'}));
  await emit('runs', delta('run1', 1, 'The Acme renewal '));
  await live.waitFor();
  assert.equal(await page.locator('.th-live').count(), 1);
  assert.equal(await live.locator('.th-who').innerText(), 'Ops');
  assert.ok(await page.evaluate(() => {
    const status = document.querySelector('[data-th-status="bot:ops"]')!, row = document.querySelector('.th-live')!;
    return !!(status.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING);
  }), 'the live row is under the status row');
  assert.equal(await live.getAttribute('aria-hidden'), 'true', 'a screen reader hears the posted reply, not each piece');
  await emit('runs', delta('run1', 2, 'is due **Friday**'));
  await page.waitForFunction(() => document.querySelector('.th-live strong')?.textContent === 'Friday');
  assert.equal((await live.locator('.th-body').innerText()).trim(), 'The Acme renewal is due Friday', 'drawn as the message markdown');
  // Thinking is not the reply.
  await emit('runs', delta('run1', 3, 'Should I mention the price?', 'thought'));
  await page.waitForTimeout(250);
  assert.ok(!(await live.innerText()).includes('price'), 'thinking is not shown');

  // ---- Fifty deltas in one burst redraw the row once or twice, not fifty times; at the bottom, the view keeps to it.
  await page.evaluate(() => { window.__tails = 0; const tail = patchHTMLTail; patchHTMLTail = (...a) => { window.__tails++; return tail(...a); }; });
  await page.evaluate(evs => { for (const d of evs) liveEmit('runs', d); },
    Array.from({length: 50}, (_, i) => delta('run1', 10 + i, i ? ` and line ${i}.\n\n` : ', with the new tax table.\n\n')));
  await page.waitForFunction(() => document.querySelector('.th-live')?.textContent?.includes('line 49.'));
  await page.waitForTimeout(250);
  const tails = await page.evaluate(() => window.__tails);
  assert.ok(tails >= 1 && tails <= 2, `the burst is drawn once or twice, not per delta (${tails})`);
  assert.ok(await scroll.evaluate(e => e.scrollHeight - e.scrollTop - e.clientHeight < 2), 'a reader at the bottom stays there as the reply grows');
  // Scrolled up to read, the place stays as the reply grows.
  await scroll.evaluate(e => { e.scrollTop = e.scrollHeight - e.clientHeight - 300; });
  const top = await scroll.evaluate(e => e.scrollTop);
  await page.evaluate(evs => { for (const d of evs) liveEmit('runs', d); }, Array.from({length: 5}, (_, i) => delta('run1', 70 + i, ` More ${i}.\n\n`)));
  await page.waitForFunction(() => document.querySelector('.th-live')?.textContent?.includes('More 4.'));
  await page.waitForTimeout(150);
  assert.equal(await scroll.evaluate(e => e.scrollTop), top, 'what they read does not move');
  await scroll.evaluate(e => { e.scrollTop = e.scrollHeight; });

  // ---- An assistant message ends the piece the deltas typed, with its own (scrubbed) text; a tool call then the next
  // piece starts a new paragraph.
  await emit('runs', run('run1', {output: {id: 990, seq: 90, kind: 'message', created_at: at(0), payload: {text: 'The Acme renewal is due **Friday**. ••••', final: false, item_id: 'a1'}}}));
  await emit('runs', run('run1', {output: {id: 991, seq: 91, kind: 'tool', created_at: at(0), bytes: 40, compact: true}}));
  await emit('runs', delta('run1', 92, 'Filed the task.'));
  await page.waitForFunction(() => document.querySelector('.th-live')?.textContent?.includes('Filed the task.'));
  const paras = await live.locator('.th-body p').allInnerTexts();
  assert.deepEqual(paras, ['The Acme renewal is due Friday. ••••', 'Filed the task.'], 'the message replaced what the deltas typed');

  // ---- The posted reply, which names its run, takes the row's place in the same frame, as the last row of the list.
  const reply = {id: 'm-reply', seq: N + 1, thread_id: T, sender_id: 'bot:ops', body: 'The Acme renewal is due **Friday**. ••••\n\nFiled the task.',
    created_at: at(0), revision: 0, refs: {turn_id: 'run1'}};
  const swap = await page.evaluate(m => new Promise<Json>(resolve => {
    liveEmit('runs', {run_id: 'run1', conversation_id: 't-live', bot_id: 'ops', output: {id: 993, seq: 93, kind: 'message', created_at: m.created_at,
      payload: {text: m.body, final: true}}});
    liveEmit('messages', {id: m.id, thread_id: 't-live', root_id: 't-live', message: m});
    const before = {live: !!document.querySelector('.th-live'), msg: !!document.querySelector('.th-msg[data-mid="m-reply"]')};
    // The view's own frame was asked for first, so this runs after it, in the same frame.
    requestAnimationFrame(() => {
      const rows = [...document.querySelectorAll('.thv-list .th-msg')];
      resolve({before, live: !!document.querySelector('.th-live'), msg: !!document.querySelector('.th-msg[data-mid="m-reply"]'),
        last: (rows[rows.length - 1] as HTMLElement | undefined)?.dataset.mid ?? ''});
    });
  }), reply);
  assert.deepEqual(swap, {before: {live: true, msg: false}, live: false, msg: true, last: 'm-reply'});
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.th-live').count(), 0, 'no live row comes back for the delivered run');

  // ---- A run that ends without a message loses its row.
  await emit('runs', run('run2', {state: 'running'}));
  await emit('runs', delta('run2', 1, 'Checking the Acme contract dates now'));
  await page.locator('.th-live[data-th-live="run2"]').waitFor();
  await emit('runs', run('run2', {state: 'failed'}));
  await page.locator('.th-live[data-th-live="run2"]').waitFor({state: 'detached', timeout: 5000});

  // ---- A run already under way when the page opened draws nothing, even once its state moves.
  await emit('runs', delta('run3', 40, 'halfway through a sentence'));
  await emit('runs', run('run3', {state: 'running'}));
  await emit('runs', delta('run3', 41, ' and the rest of it'));
  await page.waitForTimeout(300);
  assert.equal(await page.locator('.th-live').count(), 0, 'no row for a run seen only mid-way');

  assert.deepEqual(errors, []);
  console.log('thread live reply: ok');
})().then(() => browser?.close(), async e => { console.error(e); await browser?.close(); process.exit(1); });
