// Lists fed by the live stream (GET /api/v2/events) put each event's row in place and never read the whole list because
// an event came (Nov 3). Offline: the stream is held open and its events are handed to the page's handlers (liveEmit), as
// the stream would. Each list is counted: no list read after many events, a row changes in place, a row that no longer
// belongs leaves, and the 2-minute refresh catches up with a changed_after delta from the list's cursor.
//  - a bot's page: its Open rows and its filter tabs (Assigned to others), decided by each tab's rows client-side;
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
const gone = (id: string) => { SERVER.delete(id); return {id, kind: 'delete', kinds: ['delete'], gone: true}; };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 1300, height: 820}, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  // Every list read the page makes, by kind; a delta (changed_after) is counted apart with its cursor.
  const reads = {tasks: 0, tabs: 0, changed: [] as string[]};
  let delta: {tasks: Task[]; gone: string[]} = {tasks: [], gone: []};
  await page.route('**/*', (route: Route) => {
    const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/api/me') return json({id: 'ana', name: 'Ana', role: 'owner', cloud: true, registered: true});
    if (p === '/api/employees') return json(BOTS);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/v2/status') return json({bots: BOTS.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], count: 0, version: 'v0'});
    if (p === '/api/v2/events') return;                                                  // held open: events come by liveEmit
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
  await settled();
  assert.equal(reads.tasks, listed, 'no task list read follows a task event');
  assert.equal(reads.tabs, tabbed, 'no filter read follows a task event');
  // The 2-minute refresh: a delta from the open read's cursor, what changed comes in; the tabs are read once with it.
  delta = {tasks: [task('t5', 'Send the release mail', 'spec', {owner_id: 'bot:ops'})], gone: []};
  await page.evaluate(() => refresh(false));
  await page.locator('#t-open', {hasText: 'Send the release mail'}).waitFor();
  assert.deepEqual(reads.changed, ['12.a']);
  assert.equal(reads.tasks, listed, 'the refresh reads no whole task list');

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('live lists: ok');
})().catch(e => { console.error(e); process.exit(1); });
