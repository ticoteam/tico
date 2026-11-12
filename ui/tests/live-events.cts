// Live events (ui/app/live.ts, GET /api/v2/events), offline: the fixture holds each stream open until the test sends
// what changed, as the server does when a write commits.
//  - the Assistant's chat follows its conversation on the page's one stream and reads its snapshot once per change, not
//    on a timer;
//  - the Needs-you count beside a bot follows the `needs` topic: the event names what changed, and only that is read;
//  - a pipeline on the Tasks page puts each event's task in place (changed, new, moved out of the view) and reads no
//    list for it; a bot's page is sent only its tasks', a page with no task on it (the Assistant's) none;
//  - each reconnect resumes from the last change number;
//  - the 2-minute refresh catches the list up with a changed_after delta from its cursor.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {assistantView} = require('./support/assistant-chat.cts') as import('./support/assistant-chat.cts').Api;
const {threadsFake} = require('./support/threads-fake.cts') as import('./support/threads-fake.cts').Api;
const {task, brief, PIPELINES} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cts') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();   // load.cjs arrives with #254
type Route = import('playwright').Route;

declare const S: {route: string; v2: {needs: {id: string; kind: string; line?: string}[]; needsVersion: string; needsNext: string; needsCount: number; needsBots: unknown; status: Record<string, unknown>}};
declare const LIVE: {es: {url: string; topics: string[]} | null; after: string | null; taskKey: unknown};
declare const V2C: {rendered: boolean; liveOff: boolean; reading: boolean; readAgain: boolean};
declare const LIVE_SOON: Map<string, unknown>;
declare const TASKS_ST: {loading: boolean; cursor: string};
declare const BOT: {slug: string} | null;
declare const THV_VIEWS: Iterable<{host: Element; state: string}>;
declare let TASK_CHAT: {id: string} | null;
declare let v2Get: () => Promise<unknown>;
declare function v2NeedsMend(x: unknown): Promise<unknown>;
declare function needsMeCount(bot: string): number;
declare function refresh(force: boolean): Promise<void>;
declare function liveUrl(): string;
declare function liveTaskKey(scope: unknown): unknown;
declare function liveTaskScope(): unknown;
declare function liveEventsConnect(): void;

const now = new Date().toISOString();
// The server's tasks (v3 Task, as GET /api/v2/tasks/{id} and a `tasks` event's `task` carry it), by id.
const SERVER = new Map([['t1', task('t1', 'Draft the release notes', 'spec', {owner_id: 'bot:ops'})]]);
const block = (seq: number, event: string, data: Record<string, unknown>) => `id: ${seq}\nevent: ${event}\ndata: ${JSON.stringify({seq, ...data})}\n\n`;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  // Counts the live event streams each document opens, so send() knows the newest one has reached the route.
  await page.addInitScript(() => {
    const Native = window.EventSource;
    (window as unknown as {EventSource: unknown}).EventSource = class extends Native {
      constructor(url: string | URL, ...rest: [EventSourceInit?]) {
        super(url, ...rest);
        if (new URL(url, location.href).pathname === '/api/v2/events') (window as unknown as {__liveStreams?: number}).__liveStreams = ((window as unknown as {__liveStreams?: number}).__liveStreams || 0) + 1;
      }
    };
  });
  const made = new Map<string, number>();   // per document: event streams that reached the route
  const errors: string[] = [], streams: {url: URL; route: Route; document: string}[] = [], reads: {snapshot: number; tasks: number; changed: (string | null)[]; needs: string[]} = {snapshot: 0, tasks: 0, changed: [], needs: []};
  page.on('pageerror', e => errors.push(e.message));
  const threads = threadsFake({bot: 'ops'});
  let snapshot = {messages: [{id: 'm1', sender_id: 'human:ana', body: 'Draft the release notes', created_at: now}], execution: null};
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json([{name: 'assistant', display_name: 'Assistant', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []},
      {name: 'ops', display_name: 'Ops', host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/v2/events') {                                                        // held open until the test sends
      const document = route.request().headers().referer!;
      made.set(document, (made.get(document) || 0) + 1);
      streams.push({url, route, document}); return;
    }
    const answer = threads(p, url, route.request().method());
    if (answer !== undefined) return json(answer);
    if (p === '/api/v2/assistant') return json(assistantView('c-ops', snapshot));
    if (p === '/api/v2/conversations/c-ops/snapshot') { reads.snapshot++; return json(snapshot); }
    if (p === '/api/v2/tasks' && url.searchParams.get('changed_after')) {   // backend/task_list_routes.py: a delta from the cursor
      reads.changed.push(url.searchParams.get('changed_after'));
      return json({tasks: [brief(task('t3', 'Send the release mail', 'spec', {owner_id: 'bot:ops'}))], gone: ['t1'], cursor: '21.a', next_offset: null});
    }
    if (p === '/api/v2/tasks') { reads.tasks++; return json({tasks: [...SERVER.values()].map(brief), next_offset: null, cursor: '12.a'}); }
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    if (p === '/api/v2/needs-you') {
      reads.needs.push(url.search);
      // A NeedsYou item (backend/views.py needs_items): the task is Ana's to work, and Ops asked for it.
      if (url.searchParams.get('ids') === 't9') return json({count: 1, version: 'v1', positions: {t9: 0}, items: [{kind: 'task', id: 't9', task_id: 't9',
        requirement_id: null, line: 'Approve the notes', missing: null, since: now,
        task: brief(task('t9', 'Approve the notes', 'spec', {owner_id: 'human:ana', requester_id: 'bot:ops'}))}]});
      return json(url.searchParams.get('version') === 'v0' ? {count: 0, version: 'v0', unchanged: true} : {items: [], version: 'v0'});
    }
    return json(unrouted(p));
  });
  // The stream the page holds now, once the newest EventSource has reached the route (not yet answered). Navigation
  // and thread subscriptions can leave canceled routes queued; a reconnect can reopen the same URL.
  const holding = async (match: (url: URL) => boolean) => {
    const deadline = Date.now() + t(8000);
    const document = new URL(page.url()); document.hash = '';
    for (;;) {
      const [active, started] = await page.evaluate((): [string | undefined, number] => [LIVE.es?.url, (window as unknown as {__liveStreams?: number}).__liveStreams || 0]);
      const stream = streams.filter(s => s.document === document.href && s.url.href === active && match(s.url)).at(-1);
      if (stream && (made.get(document.href) || 0) >= started) return stream;
      if (Date.now() > deadline) throw new Error('no stream: ' + streams.map(s => s.url.search).join(' | '));
      await new Promise(r => setTimeout(r, 50));
    }
  };
  // Deliver only to the page's active stream. A block may be a function of the stream's URL (its `after`): the server's
  // answer depends on where the page resumes.
  const send = async (match: (url: URL) => boolean, ...blocks: (string | ((url: URL) => string))[]) => {
    const stream = await holding(match);
    streams.splice(streams.indexOf(stream), 1);
    for (const old of streams.splice(0)) await old.route.abort().catch(() => {});   // superseded ones the page closed
    const body = blocks.map(b => typeof b === 'function' ? b(stream.url) : b).join('');
    await stream.route.fulfill({contentType: 'text/event-stream', body});
    return stream.url;
  };
  const following = (url: URL) => url.searchParams.get('conversation') === 'c-ops';

  await page.goto('https://tico-ui.test/#/assistant');
  await page.waitForFunction(() => V2C?.rendered && V2C.liveOff);
  const opened = reads.snapshot;

  // The chat: one stream for the page, following this conversation; a change to it reads the snapshot once.
  snapshot = {messages: [...snapshot.messages, {id: 'm2', sender_id: 'bot:assistant', body: 'The notes are in the doc.', created_at: now}], execution: null};
  const first = await send(following, block(10, 'ready', {}), block(11, 'messages', {id: 'm2', conversation_id: 'c-ops', message: snapshot.messages[1]}));
  assert.deepEqual(first.searchParams.get('topics')!.split(',').sort(), ['bots', 'messages', 'needs', 'runs', 'threads'], 'no task changes on a page with no task');
  assert.equal(first.searchParams.get('party'), null);
  await page.getByText('The notes are in the doc.').waitFor();
  // Settled: no snapshot read queued, running or asked for again.
  await page.waitForFunction(() => !LIVE_SOON.has('chat:c-ops') && !V2C.reading && !V2C.readAgain, null, {timeout: t(5000)});
  assert.equal(reads.snapshot, opened + 1, 'one read for one change, and none on a timer');

  // Needs you: the count beside the bot follows the topic, and the reconnect resumes after change 11.
  const fullReads = reads.needs.filter(q => !q.includes('ids=')).length;
  const second = await send(following, block(12, 'needs', {count: 1, version: 'v1', base: 'v0', changed: ['t9'], removed: []}));
  assert.equal(second.searchParams.get('after'), '11');
  assert.equal(first.searchParams.get('needs'), 'changes');
  assert.equal(first.searchParams.get('output'), 'compact', 'run steps as compact signals: no screen reads their contents');
  await page.locator('.cnt.needs', {hasText: '1'}).first().waitFor();
  assert.equal(reads.needs.filter(q => !q.includes('ids=')).length, fullReads, 'only the changed item is read');
  // Only some pages held: a changed item past them stays unread, the count moves, the next page starts after them.
  const mended = await page.evaluate(async () => {
    const real = v2Get, held = {needs: S.v2.needs, version: S.v2.needsVersion, next: S.v2.needsNext, count: S.v2.needsCount, bots: S.v2.needsBots};
    v2Get = async () => ({version: 'p2', count: 5, positions: {a: 0, z: 4}, items: [{id: 'a', kind: 'task', line: 'A2'}, {id: 'z', kind: 'task'}]});
    Object.assign(S.v2, {needs: [{id: 'a', kind: 'task'}, {id: 'b', kind: 'task'}], needsVersion: 'p1', needsNext: 'p1.2', needsCount: 4});
    try {
      await v2NeedsMend({version: 'p2', base: 'p1', count: 5, changed: ['a', 'z'], removed: [], bots: {'bot:ops': 3}});
      return {ids: S.v2.needs.map(x => x.id), line: S.v2.needs[0]!.line, next: S.v2.needsNext, count: S.v2.needsCount, ops: needsMeCount('ops')};
    } finally {
      v2Get = real;
      Object.assign(S.v2, {needs: held.needs, needsVersion: held.version, needsNext: held.next, needsCount: held.count, needsBots: held.bots});
    }
  });
  assert.deepEqual(mended, {ids: ['a', 'b'], line: 'A2', next: 'p2.2', count: 5, ops: 3});

  // A pipeline on the Tasks page: each event's task goes in place (changed, new, moved out); no list read follows one.
  await page.evaluate(() => { location.hash = '#/tasks?pipeline=product'; });
  await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body')?.textContent?.includes('Draft the release notes'), null, {timeout: t(8000)})
    .catch(async e => { throw new Error(e.message + ' ' + JSON.stringify(errors) + (await page.evaluate(() => document.querySelector<HTMLElement>('#main')?.innerText.slice(0, 400)))); });
  // What backend/events.py _tasks sends for one task: its id, the change's kind(s) and the task as the viewer reads it.
  const changed = (seq: number, t: ReturnType<typeof task>, kind: string) => { SERVER.set(t.id, t); return block(seq, 'tasks', {id: t.id, kind, kinds: [kind], task: t}); };
  const listed = reads.tasks;
  const third = await send(() => true, changed(13, task('t1', 'Draft the final release notes', 'spec', {owner_id: 'bot:ops', version: 4}), 'update'));
  assert.equal(third.searchParams.get('after'), '12');
  assert.equal(third.searchParams.get('party'), null, 'a task list is sent every task');
  // A page with no task on it is sent no task changes; one open task there, only its own.
  assert.deepEqual(await page.evaluate(() => {
    const route = S.route, open = TASK_CHAT;
    S.route = '#/settings';
    const none = new URL(liveUrl(), location.href).searchParams.get('topics')!.split(',');
    TASK_CHAT = {id: 't9'};
    const one = new URL(liveUrl(), location.href).searchParams;
    S.route = route; TASK_CHAT = open; LIVE.taskKey = liveTaskKey(liveTaskScope());
    return [none.includes('tasks'), one.get('task'), one.get('topics')!.includes('tasks')];
  }), [false, 't9', true]);
  assert.equal(await page.evaluate(() => {
    const route = S.route;
    S.route = '#/meetings';
    const topics = new URL(liveUrl(), location.href).searchParams.get('topics')!;
    S.route = route; LIVE.taskKey = liveTaskKey(liveTaskScope());
    return topics.includes('tasks');
  }), true, 'meeting details follow tasks so new links can arrive');
  await page.locator('#task-body', {hasText: 'Draft the final release notes'}).waitFor();
  const fourth = await send(() => true, changed(14, task('t2', 'Check the release links', 'spec', {owner_id: 'bot:ops'}), 'insert'));
  assert.equal(fourth.searchParams.get('after'), '13');
  await page.locator('#task-body', {hasText: 'Check the release links'}).waitFor();
  // Done: no longer an open task of the pipeline, so its row leaves.
  await send(() => true, changed(15, task('t2', 'Check the release links', 'done', {owner_id: 'bot:ops', version: 4}), 'update'));
  await page.waitForFunction(() => !document.querySelector('#task-body')?.textContent?.includes('Check the release links'));
  await page.waitForFunction(() => !LIVE_SOON.has('tasks'));
  assert.equal(reads.tasks, listed, 'no list read follows a task event');
  // The 2-minute refresh catches up from the list's cursor: what changed comes in, what left goes, the cursor moves.
  await page.evaluate(() => refresh(false));
  await page.locator('#task-body', {hasText: 'Send the release mail'}).waitFor();
  await page.waitForFunction(() => !document.querySelector('#task-body')?.textContent?.includes('Draft the final release notes'));
  assert.deepEqual(reads.changed, ['12.a']);
  assert.equal(reads.tasks, listed, 'the catch-up is a delta, not a full read');
  assert.equal(await page.evaluate(() => TASKS_ST.cursor), '21.a');

  // An already-open status tooltip follows wait start and clear, including a first turn with no stored row. Each width
  // opens the bot page fresh. The server's changes for this page, in order (`changes`), and what it sends a stream that
  // starts at `after` (backend/events.py): its `ready` frame with the newest change number, then every change after
  // `after`. Every stream starts with `ready`, so a page holds the cursor it resumes from.
  const changes: {seq: number; text: string}[] = [];
  const head = () => changes.at(-1)?.seq ?? 16;
  const resend = (url: URL) => {
    const after = url.searchParams.get('after');
    return block(head(), 'ready', {}) + (after == null ? '' : changes.filter(c => c.seq > Number(after)).map(c => c.text).join(''));
  };
  let seq = 17;
  const publish = (event: string, data: Record<string, unknown>) => { const text = block(seq++, event, data); changes.push({seq: seq - 1, text}); return text; };
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 820});
    await page.goto(`https://tico-ui.test/?tooltip=${width}#/bot/ops`);
    await page.locator('.bot-ident [data-tip-bot="ops"]').waitFor();
    // The page's stream for this bot. A fresh page reconnects twice as it settles: when the thread subscribes
    // (`thread=`) and when the Threads list's first read lands and adds the `threads` topic (threads.ts threadsLoad ->
    // live.ts liveTopicAdd), each 30 ms later (liveRestart). Answering the stream before the second has replaced it
    // answers one the page has closed, and the stream that follows is never answered. Once both are in, it holds the
    // stream it keeps.
    await page.waitForFunction(() => LIVE.es?.url.includes('thread=') && LIVE.es.topics.includes('threads'));
    // The thread's first render can scroll; a scroll intentionally dismisses a tooltip.
    // Open it after that render and the thread subscription have settled.
    await page.waitForFunction(() => BOT?.slug === 'ops' && [...THV_VIEWS]
      .filter(v => v.host.isConnected).every(v => v.state !== 'loading'));
    await send(url => url.searchParams.get('party') === 'bot:ops', resend);
    await page.waitForFunction(() => LIVE.after != null);
    await page.locator('.bot-ident [data-tip-bot="ops"]').focus();
    await page.locator('#bot-tip:not([hidden])').waitFor();
    assert.doesNotMatch(await page.locator('#bot-tip').innerText(), /Waiting for.*ChatGPT/);
    const waiting = 'Waiting for the ChatGPT login (held by Scribe on #7 Count the words since 19:06Z; position 2)';
    const cursor = await page.evaluate(() => LIVE.after);
    const change = publish('bots', {bot: 'ops', status: {bot: 'ops', state: 'waiting_bot', focus: waiting,
      waiting, changed_at: now, open_tasks: 1, needs_human: 0}});
    // The page replaces its connection before it reads the change: the old stream carries it and is then closed, so
    // the change reaches the page only through the stream it resumes on.
    const held = await holding(url => url.searchParams.get('party') === 'bot:ops');
    streams.splice(streams.indexOf(held), 1);
    await page.evaluate(() => liveEventsConnect());
    await held.route.fulfill({contentType: 'text/event-stream', body: change});
    const onBotPage = await send(url => url.searchParams.get('party') === 'bot:ops', resend);
    assert.equal(onBotPage.searchParams.get('after'), String(cursor), 'the reconnect resumes from the last change the page read');
    await page.waitForFunction((text: string) => !document.querySelector<HTMLElement>('#bot-tip')!.hidden
      && document.querySelector('#bot-tip')!.textContent!.includes(text) && !LIVE_SOON.has('bots'), waiting).catch(async error => {
      console.error('tooltip live state', await page.evaluate(() => ({width: innerWidth,
        hidden: document.querySelector<HTMLElement>('#bot-tip')?.hidden, text: document.querySelector('#bot-tip')?.textContent,
        status: S.v2.status.ops, stream: LIVE.es?.url, cursor: LIVE.after, queued: [...LIVE_SOON.keys()]})));
      throw error;
    });
    assert.equal(onBotPage.searchParams.get('party'), 'bot:ops', "a bot's page is sent its own tasks' changes, not every task's");
    await send(() => true, publish('bots', {bot: 'ops', status: null}));
    await page.waitForFunction(() => !document.querySelector<HTMLElement>('#bot-tip')!.hidden
      && !document.querySelector('#bot-tip')!.textContent!.includes('Waiting for the ChatGPT') && !LIVE_SOON.has('bots'));
    assert.equal(await page.evaluate(() => S.v2.status.ops), undefined);
  }
  assert.deepEqual(errors, []);
  console.log('live events: chat, Needs you and a pipeline follow one stream; reconnects resume: ok');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
