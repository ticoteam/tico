// Lists fed by the live stream (GET /api/v2/events) put each event's row in place and never read the whole list because
// an event came (Nov 3). Offline: the stream is held open and its events are handed to the page's handlers (liveEmit), as
// the stream would. Each list is counted: no list read after many events, a row changes in place, a row that no longer
// belongs leaves, and the 2-minute refresh catches up with a changed_after delta from the list's cursor.
//  - a bot's page: its Open rows and its filter tabs (Assigned to others), decided by each tab's rows client-side;
//  - a person's page: the tasks they own, open, snoozed and finished;
//  - My dashboard: My tasks from the reader's task events, Needs you from the list the app holds, Unread threads from
//    read and activity events; the place is not read again for any of them;
//  - a read that lands late (Codex review 1): a first read or a catch-up held back by a slow server, a removal coming
//    while it is out, then the older answer: the row stays gone.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
const {task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
const {t}: {t: (ms: number) => number} = (() => { try { return require('./support/load.cts') as {t: (ms: number) => number}; } catch { return {t: (ms: number) => ms}; } })();
type Route = import('playwright').Route;
type Task = ReturnType<typeof task>;

declare const LIVE_SOON: Map<string, unknown>;
declare function liveEmit(topic: string, data: unknown): void;
declare function refresh(force: boolean): Promise<void>;
declare function botNeedsChanged(slug: string): void;
declare const BOT_TASK_FILTERED: Map<string, {tabs: Record<string, {next: string | null}>}>;
declare const S: {v2: {needs: ({id: string} & Record<string, unknown>)[]; needsCount?: number; needsVersion?: string | null; needsNext?: string | null}};
declare let v2Get: (path: string) => Promise<unknown>;
declare function v2NeedsMend(d: unknown): Promise<unknown>;
declare function needsTake(d: unknown): void;
declare function v2NeedsChanged(d: unknown): Promise<boolean>;
type Order = (a: {id: string}, b: {id: string}) => number;
declare const taskQueueOrder: Order, taskFinishedOrder: Order;
declare function taskRowsPut(rows: {id: string}[], t: {id: string; [k: string]: unknown} | null, id: string, has: (t: {id: string}) => boolean, order?: Order): boolean;
declare const THREADS: {rail: boolean | null; threads: {id: string; title?: string; [k: string]: unknown}[]; pinned: {id: string}[]};
declare function threadsPut(view: unknown): void;

const BOTS = [['ops', 'Ops'], ['pm', 'PM']].map(([name, display_name]) => ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true, schedules: []}));
// The server's tasks by id (as GET /api/v2/tasks/{id} and a `tasks` event's `task` carry them).
const SERVER = new Map<string, Task>();
const put = (x: Task) => { SERVER.set(x.id, x); return x; };
put(task('t1', 'Draft the release notes', 'spec', {owner_id: 'bot:ops'}));
put(task('t2', 'Check the release links', 'spec', {owner_id: 'bot:ops'}));
put(task('t3', 'Pick the release day', 'spec', {owner_id: 'bot:pm', requester_id: 'bot:ops'}));
// What backend/events.py _tasks sends for one task: its id, the change's kind(s) and the task as the viewer reads it, or
// `gone` when it was deleted.
const event = (x: Task, kind = 'update') => ({id: x.id, kind, kinds: [kind], task: put(x)});
// My dashboard as backend/cards.py draws it for Ana: My tasks (all of hers shown), Needs attention, Unread threads.
const dashCard = (template: string, heading: string, data: unknown, settings = {}) => ({id: null, item: null, size: 'medium', made_by: null,
  card: {id: null, template, title: '', heading, name: heading, settings: {team: '', ...settings}, team_name: '', hidden: false, made_by: null, data}});
const HOME_PLACE = {place: 'person:ana', kind: 'person', saved: false, can_edit: true, pins: [
  dashCard('my_tasks', 'My tasks', {count: 2, tasks: [{id: 't10', title: 'Sign the lease', status: 'open', due_at: '2026-11-20', updated_at: '2026-11-09T10:00:00Z'},
    {id: 't11', title: 'Plan the offsite', status: 'open', due_at: null, updated_at: '2026-11-09T09:00:00Z'}]}, {count: 5}),
  dashCard('needs_attention', 'Needs attention', {count: 0, items: []}),
  dashCard('unread_threads', 'Unread threads', {count: 2, threads: [{id: 'th1', title: 'Launch', unread: 1, activity: true, updated_at: '2026-11-09T10:00:00Z', audience: 'everyone'},
    {id: 'th2', title: 'Hiring', unread: 2, activity: true, updated_at: '2026-11-09T09:00:00Z', audience: 'everyone'}]}, {count: 5}),
]};
const gone = (id: string, seq?: number) => { SERVER.delete(id); return {...(seq ? {seq} : {}), id, kind: 'delete', kinds: ['delete'], gone: true}; };
// A read the server is slow to answer: the next request `match` names is answered as it was read, once `go()` is called.
type Held = {match: (p: string, q: URLSearchParams) => boolean; seen: Promise<void>; arrived: () => void; gate: Promise<void>; go: () => void};
let holding: Held | null = null;
const holdNext = (match: Held['match']): Held => {
  let arrived = () => {}, go = () => {};
  const seen = new Promise<void>(done => { arrived = done; }), gate = new Promise<void>(done => { go = done; });
  return holding = {match, seen, arrived, gate, go};
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  // Every list read the page makes, by kind; a delta (changed_after) is counted apart with its cursor.
  const reads = {tasks: 0, tabs: 0, places: 0, changed: [] as string[], streams: [] as string[]};
  let delta: {tasks: Task[]; gone: string[]} = {tasks: [], gone: []};
  await page.route('**/*', async (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const held = holding?.match(p, q) ? holding : null;
    if (held) holding = null;
    // The answer is what the server read when the request came; a held one is sent only once the test lets it go.
    const json = async (body: unknown) => {
      const text = JSON.stringify(body);
      if (held) { held.arrived(); await held.gate; }
      return route.fulfill({contentType: 'application/json', body: text});
    };
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}, {id: 'kim', name: 'Kim'}]});
    if (p === '/api/v2/status') return json({bots: BOTS.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, version: 'v0'});
    if (p === '/api/v2/events') { reads.streams.push(url.search); return; }               // held open: events come by liveEmit
    if (decodeURIComponent(p) === '/api/v2/places/person:me') { reads.places++; return json(HOME_PLACE); }
    if (p === '/api/v2/tasks' && q.get('changed_after')) {                               // backend/task_list_routes.py
      reads.changed.push(q.get('changed_after')!);
      const out = {tasks: delta.tasks.map(x => brief(put(x))), gone: delta.gone, cursor: '30.a', next_offset: null};
      delta.gone.forEach(id => SERVER.delete(id));
      return json(out);
    }
    if (p === '/api/v2/tasks') {
      reads.tasks++;
      const owner = q.get('owner'), status = (q.get('status') || 'open').split(',');
      const rows = [...SERVER.values()].filter(x => (!owner || x.owner_id === 'bot:' + owner || x.owner_id === owner) && (status.includes('all') || status.includes(String(x.status))));
      return json({tasks: rows.map(brief), next_offset: null, cursor: '12.a'});
    }
    if (p === '/api/v2/bots/ops/task-tabs') return json({bot: 'ops', saved: false, personal: false, can_edit: false, widgets: null,
      tabs: [{builtin: 'needs', name: 'Needs you'}, {builtin: 'active', name: 'Open'}, {builtin: 'blocked', name: 'Waiting'},
        {name: 'Assigned to others', show_count: true, conditions: [{field: 'asked_by', value: 'this_bot'}, {field: 'assigned_to', value: 'others'}, {field: 'status', value: ['open']}]}]});
    if (p === '/api/v2/bots/ops/task-tabs/tasks') {                                      // backend/bot_task_tabs.py, counted for Ana
      reads.tabs++;
      const all = [...SERVER.values()], open = (x: Task) => x.status === 'open';
      const tabs = {needs: [], active: all.filter(x => x.owner_id === 'bot:ops' && open(x)), blocked: [],
        c3: all.filter(x => x.requester_id === 'bot:ops' && !['human:ana', 'bot:ops'].includes(String(x.owner_id)) && open(x))};
      const names: Record<string, string> = {needs: 'Needs you', active: 'Open', blocked: 'Waiting', c3: 'Assigned to others'};
      return json({bot: 'ops', tabs: Object.entries(tabs).map(([key, rows]) => ({key, name: names[key], count: rows.length, ids: rows.map(x => x.id), next: null})),
        tasks: all.map(brief)});
    }
    return json(unrouted(p));
  });
  const settled = () => page.waitForFunction(() => !LIVE_SOON.has('tasks'), null, {timeout: t(5000)});
  const emit = (topic: string, ...data: unknown[]) => page.evaluate(([topic, data]) => { for (const d of data as unknown[]) liveEmit(topic as string, d); }, [topic, data] as const);
  const text = async (sel: string) => (await page.locator(sel).textContent()) || '';

  // ---- a bot's page
  await page.goto('https://tico-ui.test/#/bot/ops/tasks');
  await page.locator('#t-open', {hasText: 'Check the release links'}).waitFor({timeout: t(8000)})
    .catch(async e => { throw new Error(e.message + ' ' + JSON.stringify(errors) + await page.evaluate(() => document.querySelector<HTMLElement>('#main')?.innerText.slice(0, 400))); });
  await page.locator('#t-c3', {hasText: 'Pick the release day'}).waitFor({state: 'attached'});
  const [listed, tabbed] = [reads.tasks, reads.tabs];
  // Twenty changes to one task: its row follows them in place.
  await emit('tasks', ...Array.from({length: 20}, (_, i) => event(task('t1', `Draft the release notes v${i + 1}`, 'spec', {owner_id: 'bot:ops', version: 4 + i}))));
  await page.locator('#t-open', {hasText: 'Draft the release notes v20'}).waitFor();
  // Done: off Open. Handed to the PM: on Assigned to others, its count one more.
  await emit('tasks', event(task('t2', 'Check the release links', 'done', {owner_id: 'bot:ops'})),
    event(task('t4', 'Book the launch room', 'spec', {owner_id: 'bot:pm', requester_id: 'bot:ops'}), 'insert'));
  await page.locator('#t-c3', {hasText: 'Book the launch room'}).waitFor({state: 'attached'});
  await page.waitForFunction(() => !document.querySelector('#t-open')?.textContent?.includes('Check the release links'));
  assert.equal((await text('#cnt-c3')).trim(), '2');
  // Gone (deleted) and taken back by Ana: out of every list.
  await emit('tasks', gone('t1'), event(task('t3', 'Pick the release day', 'spec', {owner_id: 'human:ana', requester_id: 'bot:ops'})));
  await page.waitForFunction(() => !document.querySelector('#t-open')?.textContent?.includes('Draft the release notes')
    && !document.querySelector('#t-c3')?.textContent?.includes('Pick the release day'));
  // A tab with more pages: a task that joins it and may already be on a later page leaves its count; a new one adds.
  await page.evaluate(() => { BOT_TASK_FILTERED.get('ops')!.tabs['c3']!.next = 'p2'; });
  await emit('tasks', event(task('t31', 'Order the launch cake', 'spec', {owner_id: 'bot:pm', requester_id: 'bot:ops'})));
  await settled();
  assert.equal((await text('#cnt-c3')).trim(), '1');
  await emit('tasks', event(task('t32', 'Print the launch flyers', 'spec', {owner_id: 'bot:pm', requester_id: 'bot:ops'}), 'insert'));
  await page.waitForFunction(() => document.querySelector('#cnt-c3')?.textContent?.trim() === '2');
  await page.evaluate(() => { BOT_TASK_FILTERED.get('ops')!.tabs['c3']!.next = null; });
  // Needs you changing draws the tab from the list the app holds; nothing is read for it. An ask of the bot's comes: on
  // the tab, its count one more. Answered: off it again.
  const ask = brief(put(task('t30', 'Approve the launch copy', 'spec', {owner_id: 'bot:ops'})));
  await page.evaluate(it => { S.v2.needs = [it]; S.v2.needsCount = 1; botNeedsChanged('ops'); },
    {id: 'ask1', kind: 'ask', task_id: 't30', line: 'OK the launch copy?', since: '2026-11-09T10:00:00Z', task: ask, ask: {asked_by: 'bot:ops', kind: 'ok', may: []}});
  await page.locator('#t-needs', {hasText: 'Approve the launch copy'}).waitFor({state: 'attached'});
  assert.equal((await text('#cnt-needs')).trim(), '1');
  await page.evaluate(() => { S.v2.needs = []; S.v2.needsCount = 0; botNeedsChanged('ops'); });
  await page.waitForFunction(() => !document.querySelector('#t-needs')?.textContent?.includes('Approve the launch copy'));
  assert.equal((await text('#cnt-needs')).trim(), '0');
  await settled();
  assert.equal(reads.tasks, listed, 'no task list read follows a task event');
  assert.equal(reads.tabs, tabbed, 'no filter read follows a task or Needs you event');
  // The 2-minute refresh: a delta from the open read's cursor, what changed comes in; the tabs are read once with it.
  delta = {tasks: [task('t5', 'Send the release mail', 'spec', {owner_id: 'bot:ops'})], gone: []};
  await page.evaluate(() => refresh(false));
  await page.locator('#t-open', {hasText: 'Send the release mail'}).waitFor();
  assert.deepEqual(reads.changed, ['12.a']);
  assert.equal(reads.tasks, listed, 'the refresh reads no whole task list');

  // ---- a person's page
  put(task('t6', 'Order the badges', 'spec', {owner_id: 'human:sam'}));
  put(task('t7', 'Call the venue', 'spec', {owner_id: 'human:sam'}));
  reads.changed = [];
  await page.evaluate(() => { location.hash = '#/person/sam'; });
  await page.locator('#person-tasks', {hasText: 'Call the venue'}).waitFor({timeout: t(8000)});
  const personListed = reads.tasks;
  await emit('tasks', ...Array.from({length: 20}, (_, i) => event(task('t6', `Order the badges ${i + 1}`, 'spec', {owner_id: 'human:sam', version: 4 + i}))));
  await page.locator('#person-tasks', {hasText: 'Order the badges 20'}).waitFor();
  // Done: still Sam's, under Finished. Snoozed: still on the list with the open ones (status=all, #3418). Handed to Ana:
  // off it. New for Sam: on it.
  await emit('tasks', event(task('t6', 'Order the badges 20', 'done', {owner_id: 'human:sam'})),
    event(task('t7', 'Call the venue', 'spec', {owner_id: 'human:sam', snoozed: true})),
    event(task('t8', 'Print the menus', 'spec', {owner_id: 'human:sam'}), 'insert'));
  await page.locator('#person-tasks', {hasText: 'Print the menus'}).waitFor();
  assert.match(await text('#person-tasks .v2-group'), /Call the venue/);
  assert.match(await text('.person-tasks-done'), /Order the badges 20/);
  await emit('tasks', event(task('t8', 'Print the menus', 'spec', {owner_id: 'human:ana'})));
  await page.waitForFunction(() => !document.querySelector('#person-tasks')?.textContent?.includes('Print the menus'));
  await settled();
  assert.equal(reads.tasks, personListed, 'no task list read follows a task event');
  delta = {tasks: [task('t9', 'Book the bus', 'spec', {owner_id: 'human:sam'})], gone: ['t6']};
  await page.evaluate(() => refresh(false));
  await page.locator('#person-tasks', {hasText: 'Book the bus'}).waitFor();
  await page.waitForFunction(() => !document.querySelector('#person-tasks')?.textContent?.includes('Order the badges'));
  assert.deepEqual(reads.changed, ['12.a']);
  assert.equal(reads.tasks, personListed, 'the refresh reads no whole task list');

  // ---- My dashboard
  await page.evaluate(() => { location.hash = '#/home'; });
  const pin = (name: string) => page.locator('.dash-pin', {has: page.locator('h2', {hasText: name})});
  await pin('My tasks').locator('li', {hasText: 'Sign the lease'}).waitFor({timeout: t(8000)});
  const placed = reads.places;
  // The stream sends Ana's own tasks here (live.ts liveTaskScope).
  for (let i = 0; i < 50 && !/party=human%3Aana/.test(reads.streams.at(-1) || ''); i++) await page.waitForTimeout(100);
  assert.match(reads.streams.at(-1) || '', /party=human%3Aana/);
  await emit('tasks', ...Array.from({length: 20}, (_, i) => event(task('t10', `Sign the lease ${i + 1}`, 'spec', {owner_id: 'human:ana', due_at: '2026-11-20', version: 4 + i}))));
  await pin('My tasks').locator('li', {hasText: 'Sign the lease 20'}).waitFor();
  // Done: off it, one fewer. New and hers, due sooner: first, one more. Sam's: not on it.
  await emit('tasks', event(task('t11', 'Plan the offsite', 'done', {owner_id: 'human:ana'})),
    event(task('t12', 'Renew the domain', 'spec', {owner_id: 'human:ana', due_at: '2026-11-15'}), 'insert'),
    event(task('t13', 'Pack the boxes', 'spec', {owner_id: 'human:sam'}), 'insert'));
  await pin('My tasks').locator('li', {hasText: 'Renew the domain'}).waitFor();
  assert.deepEqual(await pin('My tasks').locator('li a').allTextContents(), ['Renew the domain', 'Sign the lease 20']);
  assert.equal((await pin('My tasks').locator('.dash-big strong').textContent())?.trim(), '2');
  // Needs you as the app holds it.
  await page.evaluate(() => { S.v2.needs = [{id: 'n1', kind: 'task', task_id: 'n1', line: 'Approve the budget'}]; S.v2.needsCount = 1; liveEmit('needs-list', S.v2.needs); });
  await pin('Needs attention').locator('li', {hasText: 'Approve the budget'}).waitFor();
  assert.equal((await pin('Needs attention').locator('.dash-big strong').textContent())?.trim(), '1');
  // Threads holds Launch: unread to 7, read to 5.
  await page.evaluate(() => {
    THREADS.threads = [{id: 'th1', title: 'Launch', audience: 'company', last_seq: 7, last_unread_seq: 7, activity_seq: 7, me: {member: true, unread: 1, activity: true, last_read_seq: 5}}];
  });
  // Ana reads a sub-thread of Launch, then Launch to 6: what is left keeps both rows. Read to 7: off the card.
  await emit('threads', {kind: 'read', thread_id: 'th1-sub', root_id: 'th1', read: {last_read_seq: 3}}, {kind: 'read', thread_id: 'th1', root_id: 'th1', read: {last_read_seq: 6}});
  await settled();
  await page.evaluate(() => new Promise(requestAnimationFrame));
  assert.match(await pin('Unread threads').textContent() || '', /Launch/, 'a read short of the newest leaves the row');
  assert.equal(await page.evaluate(() => THREADS.threads[0]?.['me'] && (THREADS.threads[0]['me'] as {unread: number}).unread), 1);
  // A new unread message on Hiring counts on its row (now first); Ana's read of Launch takes it off.
  await emit('threads', {kind: 'activity', thread_id: 'th2', root_id: 'th2', message_id: 'm9', unread: true, unread_count: 1, counters: {last_seq: 9, last_unread_seq: 9}},
    {kind: 'read', thread_id: 'th1', root_id: 'th1', read: {last_read_seq: 7}});
  await page.waitForFunction(() => !document.querySelector('.dash-grid')?.textContent?.includes('Launch'));
  assert.equal(await page.evaluate(() => (THREADS.threads[0]?.['me'] as {unread: number}).unread), 0);
  assert.match(await pin('Unread threads').locator('li').first().textContent() || '', /Hiring[\s\S]*3 new/);
  // Taken off Hiring's members: the event names the thread alone, and its row goes.
  await emit('threads', {kind: 'members', thread_id: 'th2', gone: true});
  await page.waitForFunction(() => !document.querySelector('.dash-grid')?.textContent?.includes('Hiring'));
  await settled();
  assert.equal(reads.places, placed, 'no read of the place follows an event');

  // ---- a read that lands late: the events that came while it was out go over it
  // A person's page, first read: Kim's tasks are read, one is removed while the answer is on its way, then it lands.
  put(task('t20', 'Ship the stickers', 'spec', {owner_id: 'human:kim'}));
  put(task('t21', 'Frame the poster', 'spec', {owner_id: 'human:kim'}));
  let slow = holdNext((p, q) => p === '/api/v2/tasks' && q.get('owner') === 'human:kim' && !q.get('changed_after'));
  await page.evaluate(() => { location.hash = '#/person/kim'; });
  await slow.seen;
  await emit('tasks', gone('t20', 13));
  await settled();
  slow.go();
  await page.locator('#person-tasks', {hasText: 'Frame the poster'}).waitFor({timeout: t(8000)});
  assert.doesNotMatch(await text('#person-tasks'), /Ship the stickers/, 'a task removed while the first read was out stays removed');
  // Its catch-up: the delta read has the poster, which is removed while the delta is out (the delta's cursor is 30).
  delta = {tasks: [task('t21', 'Frame the poster again', 'spec', {owner_id: 'human:kim'}), task('t22', 'Hang the sign', 'spec', {owner_id: 'human:kim'})], gone: []};
  slow = holdNext((p, q) => p === '/api/v2/tasks' && !!q.get('changed_after'));
  void page.evaluate(() => refresh(false));
  await slow.seen;
  await emit('tasks', gone('t21', 31));
  await settled();
  slow.go();
  await page.locator('#person-tasks', {hasText: 'Hang the sign'}).waitFor();
  assert.doesNotMatch(await text('#person-tasks'), /Frame the poster/, 'a task removed while the catch-up was out stays removed');
  delta = {tasks: [], gone: []};
  // A bot's page, first read: the PM's open tasks are read, one is removed while the answer is on its way.
  put(task('t23', 'Write the launch post', 'spec', {owner_id: 'bot:pm'}));
  put(task('t24', 'Plan the launch call', 'spec', {owner_id: 'bot:pm'}));
  put(task('t25', 'Price the launch bundle', 'spec', {owner_id: 'bot:pm', private: true}));
  slow = holdNext((p, q) => p === '/api/v2/tasks' && q.get('owner') === 'pm' && q.get('status') === 'open');
  await page.evaluate(() => { location.hash = '#/bot/pm/tasks'; });
  await slow.seen;
  // One deleted, and Ana taken off another's readers (its id alone, backend/events.py _tasks), while the read is out.
  await emit('tasks', gone('t23', 14), {seq: 15, id: 't25', kind: 'update', kinds: ['update'], gone: true});
  await settled();
  slow.go();
  await page.locator('#t-open', {hasText: 'Plan the launch call'}).waitFor({timeout: t(8000)});
  assert.doesNotMatch(await text('#t-open'), /Write the launch post/, "a bot's task removed while the first read was out stays removed");
  assert.doesNotMatch(await text('#t-open'), /Price the launch bundle/, 'a task Ana lost while the first read was out stays off');
  // My dashboard read again on coming back: a task of Ana's finished while the read is out stays off My tasks.
  await page.evaluate(() => { location.hash = '#/person/sam'; });
  await page.locator('#person-tasks', {hasText: 'Book the bus'}).waitFor();
  slow = holdNext(p => decodeURIComponent(p) === '/api/v2/places/person:me');
  await page.evaluate(() => { location.hash = '#/home'; });
  await slow.seen;
  await emit('tasks', {...event(task('t10', 'Sign the lease', 'done', {owner_id: 'human:ana'})), seq: 40});
  await settled();
  slow.go();
  await pin('My tasks').locator('li', {hasText: 'Plan the offsite'}).waitFor();
  assert.doesNotMatch(await pin('My tasks').textContent() || '', /Sign the lease/, 'a task finished while the place was read stays off My tasks');
  // Needs you: a mend's read of the changed ids that lands after a newer whole read does not put the older list back.
  assert.deepEqual(await page.evaluate(async () => {
    const real = v2Get, kept = {needs: S.v2.needs, version: S.v2.needsVersion, next: S.v2.needsNext, count: S.v2.needsCount};
    let release = () => {};
    const gate = new Promise<void>(done => { release = done; });
    v2Get = async (path: string) => {
      if (!path.includes('ids=')) return null;
      await gate;
      return {version: 'p2', count: 2, positions: {a: 0}, items: [{id: 'a', kind: 'task', line: 'A2'}]};
    };
    Object.assign(S.v2, {needs: [{id: 'a', kind: 'task', line: 'A1'}, {id: 'b', kind: 'task', line: 'B'}], needsVersion: 'p1', needsNext: null, needsCount: 2});
    try {
      const mend = v2NeedsMend({version: 'p2', base: 'p1', count: 2, changed: ['a'], removed: []});
      needsTake({items: [{id: 'c', kind: 'task', line: 'C'}], version: 'p3', count: 1, next: null});
      release();
      await mend;
      return {version: S.v2.needsVersion, ids: S.v2.needs.map(x => x.id)};
    } finally {
      v2Get = real;
      Object.assign(S.v2, {needs: kept.needs, needsVersion: kept.version, needsNext: kept.next, needsCount: kept.count});
    }
  }), {version: 'p3', ids: ['c']});
  // Stale Needs you events queued behind each other: one whole read covers them all, not one read each.
  assert.deepEqual(await page.evaluate(async () => {
    const real = v2Get, kept = {needs: S.v2.needs, version: S.v2.needsVersion, next: S.v2.needsNext, count: S.v2.needsCount};
    let whole = 0;
    v2Get = async (path: string) => {
      if (path.includes('ids=')) return null;
      whole++;
      return {version: 'p5', count: 1, next: null, items: [{id: 'e', kind: 'task', line: 'E'}]};
    };
    Object.assign(S.v2, {needs: [{id: 'a', kind: 'task', line: 'A'}], needsVersion: 'p1', needsNext: null, needsCount: 1});
    try {
      const all = ['p2', 'p3', 'p4', 'p5'].map(version => v2NeedsChanged({version, base: 'p0', count: 1, changed: ['e'], removed: ['a']}));
      await Promise.all(all);
      return {whole, version: S.v2.needsVersion, ids: S.v2.needs.map(x => x.id)};
    } finally {
      v2Get = real;
      Object.assign(S.v2, {needs: kept.needs, needsVersion: kept.version, needsNext: kept.next, needsCount: kept.count});
    }
  }), {whole: 1, version: 'p5', ids: ['e']});

  // A list's order holds as rows come and change: queue order is by rank then age, Done most recent first, and the
  // plain Threads list by title.
  assert.deepEqual(await page.evaluate(() => {
    const ids = (rows: {id: string}[]) => rows.map(r => r.id).join(' ');
    const q = [{id: 'a', rank: 1, created_at: '1'}, {id: 'c', rank: 3, created_at: '1'}, {id: 'n', rank: null, created_at: '1'}];
    taskRowsPut(q, {id: 'b', rank: 2, created_at: '2'}, 'b', () => true, taskQueueOrder);
    const added = ids(q);
    taskRowsPut(q, {id: 'a', rank: 4, created_at: '1'}, 'a', () => true, taskQueueOrder);
    const done = [{id: 'x', done_at: '2026-11-09'}, {id: 'y', done_at: '2026-11-08'}];
    taskRowsPut(done, {id: 'y', done_at: '2026-11-10'}, 'y', () => true, taskFinishedOrder);
    const kept = {rail: THREADS.rail, threads: THREADS.threads, pinned: THREADS.pinned};
    try {
      Object.assign(THREADS, {rail: false, pinned: [], threads: [{id: 'th1', title: 'Alpha', audience: 'company', me: {member: true}}, {id: 'th2', title: 'Beta', audience: 'company', me: {member: true}}]});
      threadsPut({id: 'th1', title: 'Zulu', audience: 'company', me: {member: true}});
      return {added, ranked: ids(q), done: ids(done), threads: THREADS.threads.map(t => t.title).join(' ')};
    } finally { Object.assign(THREADS, kept); }
  }), {added: 'a b c n', ranked: 'b c a n', done: 'y x', threads: 'Beta Zulu'});

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('live lists: ok');
})().catch(e => { console.error(e); process.exit(1); });
