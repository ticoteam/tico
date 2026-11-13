import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
import type {Browser, BrowserContext, Page, ViewportSize} from 'playwright';
// A bot's editable sidebar (backend/bot_task_tabs.py, ui/app/bot-sidebar.ts): until the layout arrives the rail says
// Loading; one pencil (for someone who may edit) opens edit mode in place, with Done and Cancel. There each widget
// can be moved by its handle, hidden with its eye and given a height by its bottom edge (with Reset); every task tab is
// a pill that can be dragged, opened (Name, Show count, conditions, Delete, Save) or added with +. Done saves it all in
// one PUT; Cancel drops it. A hidden widget is not drawn outside edit mode; Tasks stops at 500px unless set. The layout
// is kept in this browser and drawn from there at once on the next visit. Someone who may not edit has no pencil.
// Fixtures only, no network.
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;

declare const window: Window & typeof globalThis & {
  refreshing?: unknown;
};
// Defined by ui/app/bot-sidebar.ts, which the page loads below.
declare function botTaskFilterRead(bot: string): Promise<unknown>;

type Resolver = (value: void | PromiseLike<void>) => void;
interface Cond { field: string; value: string | string[] }
interface Tab { builtin?: string; name: string; show_count?: boolean; conditions?: Cond[] }
interface Widget { id: string; hidden: boolean; height: number | null }
interface Layout { tabs: Tab[]; widgets: Widget[] }
interface Turn { who: string; state: string; line: string; since: string }
interface Task {
  id: string; title: string; body: string; owner_id: string; requester_id: string; status: string;
  labels: string[]; version: number; created_at: string; updated_at: string;
  pipeline_id: string; stage_id: string; turn: Turn | null; late: null; snoozed: boolean; done_at?: string;
}
interface PageFixture { file_id: string; title: string; bot: string; updated_at: string; archived: boolean; restricted?: boolean }
interface Server {
  layouts: Record<string, Layout>;
  puts: {bot: string; body: Layout}[];
  page: number;
  tasks?: Task[];
  hold?: Promise<void> | null;
  putHold?: Promise<void> | null;
  putFail?: number;
  moreHold?: Promise<void> | null;
}

const ago = (minutes: number): string => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer'}[name], org_parent: '',
  host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const turn = (who: string, state: string, line = ''): Turn => ({who, state, line, since: ago(120)});
const task = (id: string, title: string, over: Partial<Task> = {}): Task => ({id, title, body: 'Details.', owner_id: 'bot:lead', requester_id: 'human:ana', status: 'open',
  labels: [], version: 1, created_at: ago(600), updated_at: ago(60), pipeline_id: 'general', stage_id: 'g-build', turn: turn('bot:lead', 'bot'),
  late: null, snoozed: false, ...over});
const TASKS: Task[] = [
  task('t-own', 'Ship the release notes'),
  task('t-waits', 'Turn on the screen default', {turn: turn('human:ana', 'person', 'Waiting on Ana'), created_at: ago(480)}),
  task('t-bot-wait', 'Wait for QA', {turn: turn('bot:qa', 'tasks', 'Waiting on QA Engineer')}),
  task('t-roadmap', 'Plan the import page', {pipeline_id: 'product', stage_id: 'p-plan', created_at: ago(30)}),
  task('t-roadmap-done', 'Write the import spec', {status: 'done', pipeline_id: 'product', stage_id: 'p-done', turn: null, done_at: ago(1440), created_at: ago(3 * 1440)}),
  task('t-qa', 'Run the release check', {owner_id: 'bot:qa', requester_id: 'bot:lead', turn: turn('bot:qa', 'bot')}),
];
const PIPELINES = [{id: 'general', name: 'General', retired_at: null, stages: [{id: 'g-build', name: 'Build'}]},
  {id: 'product', name: 'Product', retired_at: null, stages: [{id: 'p-plan', name: 'Plan'}]}];
const OTHERS: Tab = {name: 'Assigned to others', show_count: true, conditions: [{field: 'asked_by', value: 'this_bot'},
  {field: 'assigned_to', value: 'others'}, {field: 'status', value: ['open']}]};
const DEFAULT: Tab[] = [{builtin: 'needs', name: 'Needs you', show_count: true}, {builtin: 'active', name: 'Open', show_count: true},
                 {builtin: 'blocked', name: 'Waiting', show_count: true}, OTHERS];
// backend/bot_task_tabs.py DEFAULT_CONDITIONS
const CONDITIONS: Record<'needs' | 'active' | 'blocked', Cond[]> = {
  needs: [{field: 'needs', value: 'you'}],
  active: [{field: 'assigned_to', value: 'this_bot'}, {field: 'status', value: ['open']}],
  blocked: [{field: 'assigned_to', value: 'this_bot'}, {field: 'waiting_on', value: 'not_me'}],
};
const normalized = (tab: Tab): Tab => tab.builtin && tab.builtin !== 'done' && !tab.conditions ? {...tab, conditions: CONDITIONS[tab.builtin as keyof typeof CONDITIONS]} : tab;
const WIDGETS: Widget[] = ['tasks', 'updates', 'files'].map(id => ({id, hidden: false, height: null}));
const PAGES: Record<string, PageFixture> = {'page:f1': {file_id: 'f1', title: 'Weekly numbers', bot: 'lead', updated_at: ago(90), archived: false},
  'page:f2': {file_id: 'f2', title: 'Release plan', bot: 'lead', updated_at: ago(30), archived: false},
  'page:f3': {file_id: 'f3', title: 'Board pack for the November review with every number the finance team asked for', bot: 'lead', updated_at: ago(10), archived: false, restricted: true}};
// Built-in cards pinned to the sidebar: what GET /api/v2/places/bot:<slug> answers for a `card:<id>` widget (backend/cards.py).
const CARDS: Record<string, unknown> = {
  'card:c1': {id: 'c1', template: 'my_tasks', title: '', heading: 'My tasks', name: 'My tasks', team_name: '', hidden: false, settings: {},
    data: {count: 8, tasks: [{id: 't1', title: 'Ship the release', due_at: null}]}},
  'card:c2': {id: 'c2', template: 'updates', title: '', heading: 'Updates', name: 'Updates', team_name: '', hidden: false, settings: {},
    data: {unread: 0, updates: []}},
  'card:c3': {id: 'c3', template: 'spend', title: '', heading: 'Spend', name: 'Spend', team_name: '', hidden: false, settings: {},
    data: {spent: 1240, budget: 2000, month: 'Nov'}}};
// The server's filter, for the fields these fixtures use; Ana is the viewer.
const matches = (bot: string, tab: Tab, t: Task): boolean => (t.owner_id === 'bot:' + bot || t.requester_id === 'bot:' + bot) && tab.conditions!.every(({field, value}) => {
  const who = value === 'this_bot' ? 'bot:' + bot : value === 'me' ? 'human:ana' : value;
  if (field === 'status') return value.includes(t.status);
  // Whose turn it is: a task waits on someone who is not the bot itself.
  const waits = t.status === 'open' && !!t.turn?.who && t.turn.who !== 'bot:' + bot ? t.turn.who : '';
  if (field === 'needs') return waits === 'human:ana';
  if (field === 'waiting_on') return !!waits && (value === 'me' ? waits === 'human:ana' : value === 'not_me' ? waits !== 'human:ana'
    : value === 'person' ? waits.startsWith('human:') : value === 'bot' ? waits.startsWith('bot:') : true);
  if (field === 'pipeline') return value.includes(t.pipeline_id);
  if (field === 'asked_by') return t.requester_id === who;
  if (field === 'assigned_to') return value === 'others' ? !['human:ana', 'bot:' + bot].includes(t.owner_id) : t.owner_id === who;
  throw new Error('fixture has no ' + field);
});

async function open(browser: Browser, viewport: ViewportSize, colorScheme: string, server: Server): Promise<{context: BrowserContext; page: Page; errors: string[]}> {
  const context = await browser.newContext({viewport, colorScheme: colorScheme as 'light' | 'dark', serviceWorkers: 'block'});
  // The app is dark unless this browser saved another theme (ui/theme.ts).
  await context.addInitScript((theme: string) => localStorage.setItem('tico.theme', theme), colorScheme);
  const page = await context.newPage();
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname, q = url.searchParams;
    const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
    if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/vendor/fonts/material-symbols-outlined.woff2') return route.fulfill({contentType: 'font/woff2', path: uiFile('vendor/fonts/material-symbols-outlined.woff2')});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
    if (p === '/api/issues') return json([]);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
    if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
    if (p === '/api/v2/needs-you') return json({items: [], went_away: null});
    if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
    if (p === '/api/v2/updates') return json({updates: q.get('bot') === 'lead' ? [{id: 'u1', bot: 'lead', day: '2026-10-23', kind: 'daily',
      body: 'Shipped the export fix.', read: true, created_at: ago(600)}] : [], missed: [], unread: 0, next_before: null, today: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    const layout = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs$/);
    if (layout) {
      const bot = layout[1]!;
      if (request.method() === 'PUT') {
        const body = JSON.parse(request.postData()!) as Layout;
        server.puts.push({bot, body});
        if (server.putHold) await server.putHold;
        if (server.putFail && server.putFail--) return route.fulfill({status: 500, contentType: 'application/json', body: '{"detail":"Could not save."}'});
        server.layouts[bot] = {tabs: body.tabs, widgets: body.widgets};
      } else if (server.hold) await server.hold;
      const kept = server.layouts[bot] || {tabs: DEFAULT, widgets: WIDGETS};
      return json({bot, ...kept, saved: !!server.layouts[bot], personal: false, can_edit: bot === 'lead'});
    }
    const filtered = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs\/tasks$/);
    if (filtered) {
      const bot = filtered[1]!, tabs = (server.layouts[bot] || {tabs: DEFAULT}).tabs.map((tab, at) => ({tab: normalized(tab), key: tab.builtin || 'c' + at})).filter(({tab}) => tab.builtin !== 'done');
      // A page of server.page rows; `before` here is the index the next page starts at.
      if (q.get('tab') && server.moreHold) await server.moreHold;
      const from = Number(q.get('before') || 0), wanted = tabs.filter(({key}) => !q.get('tab') || key === q.get('tab'));
      const counted = wanted.map(({tab, key}) => {
        const all = (server.tasks || TASKS).filter(t => matches(bot, tab, t)).map(t => t.id), ids = all.slice(from, from + server.page);
        return {key, name: tab.name, count: all.length, ids, next: from + server.page < all.length ? String(from + server.page) : null};
      });
      return json({bot, tabs: counted, tasks: (server.tasks || TASKS).filter(t => counted.some(c => c.ids.includes(t.id)))});
    }
    if (p === '/api/v2/tasks') {
      const who = q.get('owner') || q.get('requester'), field = q.get('owner') ? 'owner_id' : 'requester_id';
      const status = q.get('status');
      return json({tasks: (server.tasks || TASKS).filter(t => t[field] === 'bot:' + who && (!status || status === 'all' || status.split(',').includes(t.status)))});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    // The sidebar's pages (backend/cards.py): what GET /api/v2/places/bot:<slug> answers for the saved widgets.
    const pinned = decodeURIComponent(p).match(/^\/api\/v2\/places\/bot:([^/]+)$/);
    if (pinned) return json({place: 'bot:' + pinned[1], kind: 'bot', saved: true, can_edit: pinned[1] === 'lead',
      pins: ((server.layouts[pinned[1]!] || ({} as Partial<Layout>)).widgets || []).filter(w => PAGES[w.id] || CARDS[w.id])
        .map((w, i) => ({id: 'p' + i, item: w.id, position: i, size: 'medium', ...(PAGES[w.id] ? {page: PAGES[w.id]} : {card: CARDS[w.id]})}))});
    if (p === '/api/v2/cards/choices') return json({templates: [], teams: [], kpis: [], pages: Object.values(PAGES)});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  return {context, page, errors};
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    for (const [scheme, viewport] of [['light', {width: 1280, height: 900}], ['dark', {width: 375, height: 800}]] as [string, ViewportSize][]) {
      const phone = viewport.width < 760, at = `${scheme} ${viewport.width}`;
      let release!: Resolver;
      const server: Server = {layouts: {}, puts: [], page: 1, hold: new Promise<void>(resolve => { release = resolve; })};
      const {context, page, errors} = await open(browser, viewport, scheme, server);
      const rail = page.locator('#pane-tasks');
      const tabTexts = async () => (await rail.locator('[data-bot-task-tab]').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
      const order = () => rail.evaluate(el => [...(el.querySelectorAll(':scope>.bot-w') as unknown as HTMLElement[])].map(w => w.dataset.widget));
      const widget = (id: string) => rail.locator(`.bot-w[data-widget="${id}"]`);
      const pill = (name: string) => rail.locator('[data-bot-task-tab]', {hasText: name});
      const dialog = page.locator('#bot-filter-dialog');
      const shows = () => rail.evaluate(el => [...(el.querySelectorAll(':scope>.bot-w') as unknown as HTMLElement[])].filter(w => w.offsetParent).map(w => w.dataset.widget));
      const goto = async (hash: string) => {
        await page.goto('https://tico-ui.test/' + hash);
        if (phone) await page.locator('.bot-switch button', {hasText: 'Tasks'}).click().catch(() => {});
      };

      // Loading until the layout arrives: no widget is drawn and then moved.
      await goto('#/bot/lead');
      await page.waitForFunction(() => document.querySelector('#pane-tasks')?.hasAttribute('data-loading'));
      if (phone) await page.locator('.bot-switch button', {hasText: 'Tasks'}).click();
      await rail.locator('.bot-rail-loading').waitFor();
      assert.deepEqual(await shows(), [], at + ': nothing drawn while the layout loads');
      release();
      await page.waitForFunction(() => document.querySelector('#cnt-c3')?.textContent!.trim() === '1');
      assert.equal(await rail.locator('.bot-rail-loading').isHidden(), true);
      assert.deepEqual(await tabTexts(), ['Needs you 1', 'Open 4', 'Waiting 1', 'Assigned to others 1'], at);
      await pill('Waiting').click();
      // S14: a row is its title and, dimmed at the right, its stage; no Blocked or Waiting words.
      assert.deepEqual(await rail.locator('#t-blocked .tk-title').allInnerTexts(), ['Wait for QA']);
      assert.equal(await rail.locator('#t-blocked .tk-after').innerText(), 'Build');
      assert.doesNotMatch(await rail.locator('#t-blocked').innerText(), /Blocked|Waiting on/);
      await pill('Open').click();
      // Rows come a page (here one) at a time.
      assert.deepEqual(await rail.locator('#t-open .tk-title').allInnerTexts(), ['Ship the release notes'], at);
      assert.deepEqual(await order(), ['tasks', 'updates', 'files']);
      await pill('Assigned to others').click();
      assert.deepEqual(await rail.locator('#t-c3 .tk-row .tk-title').allInnerTexts(), ['Run the release check']);
      // One pencil, no gear or board icon.
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      assert.equal(await page.locator('#bot-tasks-settings').count(), 0);
      assert.equal(await rail.locator('#bot-tasks-board').count(), 0);
      assert.equal(await rail.locator('.bot-rail-editbar').isHidden(), true);
      assert.equal(await widget('tasks').locator('[data-w-grip]').isHidden(), true, 'no handles outside edit mode');
      // Tasks stops at 500px by default and a short list stays short.
      const tasksMax = await rail.locator('#bot-tasks').evaluate(el => [getComputedStyle(el).maxHeight, el.getBoundingClientRect().height] as [string, number]);
      assert.equal(tasksMax[0], '500px', at);
      assert.ok(tasksMax[1] < 500, JSON.stringify(tasksMax));

      // Edit, change things, Cancel: nothing is written and the rail is as it was.
      await rail.locator('#bot-sidebar-edit').click();
      await rail.locator('#bot-sidebar-done').waitFor();
      assert.equal(await rail.locator('#bot-sidebar-edit').isHidden(), true);
      await widget('files').locator('[data-w-eye]').click();
      await pill('Open').click();
      await dialog.waitFor();
      await dialog.locator('[data-filter-name]').fill('Busy');
      await dialog.getByRole('button', {name: 'Save'}).click();
      assert.deepEqual((await tabTexts()).slice(0, 2), ['Needs you 1', 'Busy 4']);
      await rail.locator('#bot-sidebar-cancel').click();
      assert.equal(server.puts.length, 0);
      assert.deepEqual(await tabTexts(), ['Needs you 1', 'Open 4', 'Waiting 1', 'Assigned to others 1']);
      assert.equal(await widget('files').getAttribute('data-off'), null);

      // Edit again: hide Files, move Updates up by keyboard and down past Files by dragging its handle, set Tasks' height
      // then Reset it, set Updates' height by dragging its edge.
      await rail.locator('#bot-sidebar-edit').click();
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      assert.equal(await rail.locator('[data-bot-tab-add]').isVisible(), true);
      const eye = widget('files').locator('[data-w-eye]');
      await eye.click();
      assert.equal(await eye.getAttribute('aria-pressed'), 'true');
      assert.equal(await eye.getAttribute('aria-label'), 'Show Files');
      assert.equal(await widget('files').locator('.bot-w-bar').isVisible(), true, 'a hidden widget stays in edit mode, to show again');
      await widget('updates').locator('[data-w-grip]').focus();
      await page.keyboard.press('ArrowUp');
      assert.deepEqual(await order(), ['updates', 'tasks', 'files']);
      const grip = (await widget('updates').locator('[data-w-grip]').boundingBox())!, last = (await widget('files').boundingBox())!;
      await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
      await page.mouse.down();
      await page.mouse.move(grip.x + grip.width / 2, grip.y + 40, {steps: 4});
      await page.mouse.move(grip.x + grip.width / 2, last.y + last.height - 2, {steps: 8});
      await page.mouse.up();
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      const size = widget('tasks').locator('[data-w-size]');
      await size.focus();
      await page.keyboard.press('ArrowUp');
      assert.ok(Number(await widget('tasks').getAttribute('data-h')) > 0);
      await widget('tasks').locator('[data-w-reset]').click();
      assert.equal(await widget('tasks').getAttribute('data-h'), null);
      assert.equal(await widget('tasks').locator('[data-w-reset]').isHidden(), true);
      const edge = (await widget('updates').locator('[data-w-size]').boundingBox())!;
      const before = await widget('updates').locator('.rail-sec').evaluate(el => el.getBoundingClientRect().height);
      await page.mouse.move(edge.x + edge.width / 2, edge.y + edge.height / 2);
      await page.mouse.down();
      await page.mouse.move(edge.x + edge.width / 2, edge.y + edge.height / 2 + 300, {steps: 5});
      await page.mouse.up();
      const updatesHeight = Number(await widget('updates').getAttribute('data-h'));
      assert.ok(Math.abs(updatesHeight - Math.max(80, Math.round(before + 300))) <= 2, `${before} -> ${updatesHeight}`);

      // A default tab has ordinary editable conditions and Reset. Turn Open’s count off.
      await pill('Open').click();
      await dialog.waitFor();
      assert.deepEqual(await dialog.locator('[data-cond-field]').evaluateAll<string[], unknown, HTMLSelectElement>(els => els.map(el => el.value)), ['assigned_to', 'status']);
      assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'this_bot');
      assert.deepEqual(await dialog.locator('[data-cond-value="1"]:checked').evaluateAll<string[], unknown, HTMLInputElement>(els => els.map(el => el.value)), ['open']);
      await dialog.locator('[data-cond-remove="0"]').click();
      await dialog.locator('[data-cond-add]').click();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 2);
      await dialog.getByRole('button', {name: 'Reset to default'}).click();
      assert.deepEqual(await dialog.locator('[data-cond-field]').evaluateAll<string[], unknown, HTMLSelectElement>(els => els.map(el => el.value)), ['assigned_to', 'status']);
      assert.equal(await dialog.locator('[data-filter-count]').isChecked(), true);
      await dialog.locator('[data-filter-count]').uncheck();
      await dialog.getByRole('button', {name: 'Save'}).click();
      await dialog.waitFor({state: 'hidden'});
      assert.equal(await pill('Open').locator('.cnt').isHidden(), true);
      // + adds a filter: name it, Pipeline is Product, Status adds Done. Show count is on by default.
      await rail.locator('[data-bot-tab-add]').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-filter-count]').isChecked(), true);
      await dialog.getByRole('button', {name: 'Save'}).click();
      assert.equal(await dialog.locator('[data-filter-error]').innerText(), 'Name it.');
      await dialog.locator('[data-filter-name]').fill('product tasks');
      await dialog.locator('[data-cond-value="0"][value="done"]').check();
      await dialog.locator('[data-cond-add]').click();
      await dialog.locator('[data-cond-field="1"]').selectOption('pipeline');
      await dialog.locator('[data-cond-value="1"][value="general"]').uncheck();
      await dialog.locator('[data-cond-value="1"][value="product"]').check();
      await dialog.locator('[data-cond-add]').click();
      await dialog.locator('[data-cond-field="2"]').selectOption('created_within');
      await dialog.locator('[data-cond-remove="2"]').click();
      await dialog.getByRole('button', {name: 'Save'}).click();
      await dialog.waitFor({state: 'hidden'});
      // Drag it to the front; ArrowLeft and ArrowRight move the focused pill too.
      // The bar is wider than the rail: held near its left end, it scrolls to the first tab.
      await pill('product tasks').scrollIntoViewIfNeeded();
      const from = (await pill('product tasks').boundingBox())!, bar = (await rail.locator('.bot-task-tabs').boundingBox())!;
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(bar.x + 6, from.y + from.height / 2, {steps: 10});
      for (let n = 0; n < 40; n++) await page.mouse.move(bar.x + 4 + n % 2, from.y + from.height / 2);
      await page.mouse.up();
      assert.equal(await dialog.isVisible(), false, 'a drag does not open the pop-up');
      assert.deepEqual((await tabTexts()).map(s => s.replace(/ \d+$/, '')), ['product tasks', 'Needs you', 'Open', 'Waiting', 'Assigned to others']);
      await pill('Waiting').focus();
      await page.keyboard.press('ArrowRight');
      await pill('Waiting').focus();
      await page.keyboard.press('ArrowLeft');
      assert.deepEqual((await tabTexts()).map(s => s.replace(/ \d+$/, '')), ['product tasks', 'Needs you', 'Open', 'Waiting', 'Assigned to others']);
      // Delete removes a tab.
      await pill('Assigned to others').click();
      await dialog.locator('[data-filter-delete]').click();
      await dialog.waitFor({state: 'hidden'});

      // Done: one PUT with everything. A failed write keeps the draft and edit mode, and the draft still changes.
      const files = widget('files').locator('[data-w-eye]'), filesHidden = () => files.getAttribute('aria-pressed');
      server.putFail = 1;
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-status .err').waitFor();
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      await files.click();
      assert.equal(await filesHidden(), 'false');
      await files.click();
      assert.equal(await filesHidden(), 'true');
      // While the retry is on its way nothing in the draft changes: eye, Escape, a pill.
      let putDone!: Resolver;
      server.putHold = new Promise<void>(resolve => { putDone = resolve; });
      await rail.locator('#bot-sidebar-done').click();
      await page.waitForFunction(() => document.querySelector('#bot-sidebar-status')!.textContent === 'Saving…');
      await files.dispatchEvent('click');
      assert.equal(await filesHidden(), 'true', 'no change while saving');
      assert.equal(await files.isDisabled(), true);
      await widget('tasks').locator('[data-w-size]').focus();
      await page.keyboard.press('ArrowDown');
      assert.equal(await widget('tasks').getAttribute('data-h'), null);
      await page.keyboard.press('Escape');
      await pill('Open').dispatchEvent('click');
      assert.equal(await dialog.isVisible(), false);
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), true);
      putDone();
      server.putHold = null;
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      assert.equal(server.puts.length, 2);
      assert.deepEqual(server.puts[1], server.puts[0]);
      assert.deepEqual(server.puts[1], {bot: 'lead', body: {
        tabs: [{name: 'product tasks', show_count: true, conditions: [{field: 'status', value: ['open', 'done']}, {field: 'pipeline', value: ['product']}]},
               {builtin: 'needs', name: 'Needs you', show_count: true, conditions: CONDITIONS.needs}, {builtin: 'active', name: 'Open', show_count: false, conditions: CONDITIONS.active},
               {builtin: 'blocked', name: 'Waiting', show_count: true, conditions: CONDITIONS.blocked}],
        widgets: [{id: 'tasks', hidden: false, height: null},
                  {id: 'files', hidden: true, height: null}, {id: 'updates', hidden: false, height: updatesHeight}]}});
      assert.equal(await rail.evaluate(el => el.classList.contains('editing')), false);
      await page.waitForFunction(() => document.querySelector('#cnt-c0')?.textContent!.trim() === '2');
      assert.deepEqual(await tabTexts(), ['product tasks 2', 'Needs you 1', 'Open', 'Waiting 1']);
      await pill('product tasks').click();
      // A page at a time: Show more reads the next, and a refresh keeps as many as were read, also a refresh that
      // starts while Show more is on its way.
      assert.deepEqual(await rail.locator('#t-c0 .tk-row .tk-title').allInnerTexts(), ['Plan the import page']);
      let moreDone!: Resolver;
      server.moreHold = new Promise<void>(resolve => { moreDone = resolve; });
      await rail.locator('#t-c0 [data-bot-filter-more]').click();
      await page.evaluate(() => { window.refreshing = botTaskFilterRead('lead'); });
      await page.waitForTimeout(300);
      moreDone();
      server.moreHold = null;
      await page.evaluate(() => window.refreshing);
      await rail.locator('#t-c0 [data-bot-filter-more]').waitFor({state: 'detached'});
      assert.deepEqual(await rail.locator('#t-c0 .tk-row .tk-title').allInnerTexts(), ['Plan the import page', 'Write the import spec']);
      await page.evaluate(() => botTaskFilterRead('lead'));
      assert.deepEqual(await rail.locator('#t-c0 .tk-row .tk-title').allInnerTexts(), ['Plan the import page', 'Write the import spec']);
      assert.equal(await rail.locator('#t-c0 [data-bot-filter-more]').count(), 0);
      // A default tab uses exactly its edited conditions after Done and reload; Reset restores its full default.
      await rail.locator('#bot-sidebar-edit').click();
      await pill('Waiting').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-cond-field]').count(), 2);
      assert.equal(await dialog.locator('[data-cond-value="1"]').inputValue(), 'not_me');
      await dialog.locator('[data-cond-value="1"]').selectOption('me');
      await dialog.getByRole('button', {name: 'Save'}).click();
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      await goto('#/bot/lead');
      await page.waitForFunction(() => document.querySelector('#t-blocked .tk-title')?.textContent === 'Turn on the screen default');
      await pill('Waiting').click();
      assert.deepEqual(await rail.locator('#t-blocked .tk-row .tk-title').allInnerTexts(), ['Turn on the screen default']);
      await rail.locator('#bot-sidebar-edit').click();
      await pill('Waiting').click();
      await dialog.waitFor();
      await dialog.getByRole('button', {name: 'Reset to default'}).click();
      assert.equal(await dialog.locator('[data-cond-value="1"]').inputValue(), 'not_me');
      await dialog.getByRole('button', {name: 'Save'}).click();
      await pill('Needs you').click();
      await dialog.waitFor();
      assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
      assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
      assert.doesNotMatch(await dialog.innerText(), /approvals/);
      await dialog.getByRole('button', {name: 'Save'}).click();
      await rail.locator('#bot-sidebar-done').click();
      await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
      await page.waitForFunction(() => document.querySelector('#t-blocked .tk-title')?.textContent === 'Wait for QA');
      // Outside edit mode: Files is not drawn, the rest in the new order, and Updates' height is its most.
      assert.equal(await widget('files').isHidden(), true);
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      assert.equal(await widget('updates').locator('.rail-sec').evaluate(el => getComputedStyle(el).maxHeight), updatesHeight + 'px');
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path: `/tmp/bot-sidebar-${scheme}-${viewport.width}.png`});

      // Next visit: drawn from this browser's copy at once, while the server's answer is still on its way.
      server.hold = new Promise<void>(resolve => { release = resolve; });
      await goto('#/bot/lead');
      await rail.locator('#bot-tab-t-c0').waitFor({state: 'attached'});
      assert.equal(await rail.evaluate(el => el.hasAttribute('data-loading')), false);
      assert.deepEqual(await order(), ['tasks', 'files', 'updates']);
      assert.equal(await widget('files').isHidden(), true);
      release();
      // QA: Ana may not change its sidebar, so no pencil.
      await goto('#/bot/qa');
      await page.waitForFunction(() => !document.querySelector('#pane-tasks')?.hasAttribute('data-loading') && document.querySelector('#bot-tab-t-c3'));
      assert.equal(await rail.locator('#bot-sidebar-edit').isHidden(), true);
      assert.deepEqual(errors, [], at);
      // The edit view, for the record.
      await goto('#/bot/lead');
      await rail.locator('#bot-sidebar-edit:not([hidden])').click();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({path: `/tmp/bot-sidebar-${scheme}-${viewport.width}-edit.png`});
      await context.close();
    }
    // A read-only viewer gets the same default filters: a task waiting on another bot is Waiting 1 and Open 1.
    const isolated: Server = {layouts: {}, puts: [], page: 200, tasks: [task('one-wait', 'Wait for the builder', {owner_id: 'bot:qa', turn: turn('bot:lead', 'tasks', 'Waiting on Head of Engineering')})]};
    const readOnly = await open(browser, {width:1280,height:900}, 'light', isolated);
    await readOnly.page.goto('https://tico-ui.test/#/bot/qa/tasks');
    await readOnly.page.waitForFunction(() => document.querySelector('#cnt-blocked')?.textContent === '1');
    assert.equal(await readOnly.page.locator('#cnt-active').innerText(), '1');
    assert.equal(await readOnly.page.locator('#bot-tasks-board').count(), 0);
    assert.equal(await readOnly.page.locator('#bot-sidebar-edit').isHidden(), true);
    await readOnly.page.locator('#bot-tab-t-blocked').click();
    assert.deepEqual(await readOnly.page.locator('#t-blocked .tk-title').allInnerTexts(), ['Wait for the builder']);
    await readOnly.page.locator('#bot-tab-t-active').click();
    assert.deepEqual(await readOnly.page.locator('#t-open .tk-title').allInnerTexts(), ['Wait for the builder']);
    assert.deepEqual(readOnly.errors, []);
    await readOnly.context.close();
    const filtered = await open(browser, {width:1280,height:900}, 'light', {puts: [], page: 200, layouts: {lead: {
      tabs: [{name: 'Bot waits', conditions: [{field: 'waiting_on', value: 'bot'}]},
             {name: 'People waits', conditions: [{field: 'waiting_on', value: 'person'}]}], widgets: WIDGETS}}});
    await filtered.page.goto('https://tico-ui.test/#/bot/lead/tasks');
    // Waiting on a bot: on QA for the task it waits on, and the task this bot asked QA for.
    await filtered.page.waitForFunction(() => document.querySelector('#cnt-c0')?.textContent === '2');
    await filtered.page.locator('#bot-tab-t-c0').click();
    assert.deepEqual(await filtered.page.locator('#t-c0 .tk-title').allInnerTexts(), ['Wait for QA', 'Run the release check']);
    assert.equal(await filtered.page.locator('#t-c0 .tk-after').first().innerText(), 'Build');
    await filtered.page.locator('#bot-tab-t-c1').click();
    assert.deepEqual(await filtered.page.locator('#t-c1 .tk-title').allInnerTexts(), ['Turn on the screen default']);
    assert.deepEqual(filtered.errors, []);
    await filtered.context.close();
    // A sidebar of pages: the built-ins hidden, a page shown with its title; Add puts another page in the draft, its
    // cross takes one out, and Done saves them as widgets.
    const pages: Server = {layouts: {lead: {tabs: DEFAULT, widgets: [{id: 'page:f1', hidden: false, height: null},
      ...WIDGETS.map(w => ({...w, hidden: true}))]}}, puts: [], page: 200};
    const paged = await open(browser, {width: 1280, height: 900}, 'light', pages);
    const prail = paged.page.locator('#pane-tasks');
    const shown = () => prail.evaluate(el => [...(el.querySelectorAll(':scope>.bot-w') as unknown as HTMLElement[])].filter(w => w.offsetParent).map(w => w.dataset.widget));
    await paged.page.goto('https://tico-ui.test/#/bot/lead');
    await paged.page.waitForFunction(() => document.querySelector('#pane-tasks .bot-w[data-widget="page:f1"]')?.textContent.includes('Weekly numbers'));
    assert.deepEqual(await shown(), ['page:f1'], 'a sidebar of pages only');
    const pencil = (await prail.locator('#bot-sidebar-edit').boundingBox())!, first = (await prail.locator('.bot-w[data-widget="page:f1"] .bot-pin > :first-child').boundingBox())!;
    assert.ok(pencil.y + pencil.height <= first.y + 1, 'the edit icon sits above the page, not on it');
    if (process.env.TICO_UI_SHOTS) await paged.page.screenshot({path: require('node:path').join(process.env.TICO_UI_SHOTS, 'bot-sidebar-pages.png')});
    await prail.locator('#bot-sidebar-edit').click();
    await prail.locator('#bot-sidebar-add').click();
    await paged.page.locator('#bot-pin-dialog[open] [name="page"][value="f2"]').check();
    await paged.page.locator('#bot-pin-dialog[open] [type="submit"]').click();
    await paged.page.waitForFunction(() => document.querySelector('#pane-tasks .bot-w[data-widget="page:f2"]')?.textContent.includes('Release plan'));
    await prail.locator('.bot-w[data-widget="page:f1"] [data-w-remove]').click();
    assert.equal(await prail.locator('.bot-w[data-widget="page:f1"]').count(), 0);
    await prail.locator('#bot-sidebar-done').click();
    await paged.page.waitForFunction(() => !document.querySelector('#pane-tasks.editing'));
    assert.deepEqual(pages.puts.at(-1)!.body.widgets.map(w => w.id + (w.hidden ? ' hidden' : '')), ['tasks hidden', 'updates hidden', 'files hidden', 'page:f2']);
    assert.deepEqual(await shown(), ['page:f2']);
    assert.deepEqual(paged.errors, []);
    await paged.context.close();
    // Pinned pages with no framed preview (a media install: no src) are rows like the rail's others: a mark and the
    // title, the whole row the link, no Open button, status line, version or box; two pins in a row stack as one list.
    for (const viewport of [{width: 1280, height: 900}, {width: 390, height: 844}]) {
      const phone = viewport.width < 760, at = 'pinned rows ' + viewport.width;
      const rows: Server = {layouts: {lead: {tabs: DEFAULT, widgets: [{id: 'page:f1', hidden: false, height: null}, {id: 'page:f3', hidden: false, height: null},
        ...WIDGETS.map(w => ({...w, hidden: true}))]}}, puts: [], page: 200};
      const pinned = await open(browser, viewport, 'light', rows);
      await pinned.page.goto('https://tico-ui.test/#/bot/lead' + (phone ? '/tasks' : ''));
      const rr = pinned.page.locator('#pane-tasks');
      await rr.locator('.bot-w[data-widget="page:f3"] a.pg-open').waitFor();
      const row = (id: string) => rr.locator(`.bot-w[data-widget="${id}"] a.pg-open`);
      assert.equal(await rr.locator('.pg-open-btn, .pg-status, .pg-ver').count(), 0, at + ': no Open button, updated line or version');
      assert.deepEqual(await rr.locator('.pg-open-title').allInnerTexts(), ['Weekly numbers', PAGES['page:f3']!.title], at + ': the titles');
      const style = await row('page:f1').evaluate(el => { const c = getComputedStyle(el); return {border: c.borderTopWidth, bg: c.backgroundColor}; });
      assert.deepEqual(style, {border: '0px', bg: 'rgba(0, 0, 0, 0)'}, at + ': no box');
      for (const id of ['page:f1', 'page:f3']) {
        const h = (await row(id).boundingBox())!.height;
        assert.ok(phone ? h >= 44 : Math.round(h) === 32, `${at}: ${id} is ${h}px tall`);
      }
      // A restricted page shows a lock in place of the page icon; the tooltip is the full title.
      assert.equal(await row('page:f1').locator('.pg-mark .nav-icon').innerText(), 'article');
      assert.equal(await row('page:f3').locator('.pg-mark .nav-icon').innerText(), 'lock', at + ': a lock for a restricted page');
      assert.equal(await row('page:f3').getAttribute('title'), PAGES['page:f3']!.title, at + ': the full title as the tooltip');
      // A long title stays on one line, cut with an ellipsis, inside the rail.
      const cut = await row('page:f3').locator('.pg-open-title').evaluate(el => ({ellipsis: getComputedStyle(el).textOverflow, wraps: el.getBoundingClientRect().height > 24, over: el.scrollWidth > el.clientWidth}));
      assert.deepEqual(cut, {ellipsis: 'ellipsis', wraps: false, over: true}, at + ': one line, ending in an ellipsis');
      const railBox = (await rr.boundingBox())!, rowBox = (await row('page:f3').boundingBox())!;
      assert.ok(rowBox.x >= railBox.x - 1 && rowBox.x + rowBox.width <= railBox.x + railBox.width + 1, at + ': nothing overflows the rail');
      // Two pins in a row: no hairline and no gap between them.
      const joint = await rr.evaluate(el => {
        const [a, b] = ['f1', 'f3'].map(id => el.querySelector(`.bot-w[data-widget="page:${id}"] > .rail-sec`) as HTMLElement);
        const ra = a!.querySelector('a.pg-open')!.getBoundingClientRect(), rb = b!.querySelector('a.pg-open')!.getBoundingClientRect();
        return {line: getComputedStyle(b!).borderTopWidth, gap: Math.round(rb.top - ra.bottom)};
      });
      assert.deepEqual(joint, {line: '0px', gap: 0}, at + ': two pins stack as consecutive rows');
      // Hover shades the row; a focused row shows the focus ring, and Enter opens the page.
      if (!phone) {
        await row('page:f1').hover();
        await pinned.page.waitForFunction(() => getComputedStyle(document.querySelector('#pane-tasks .bot-w[data-widget="page:f1"] a.pg-open')!).backgroundColor !== 'rgba(0, 0, 0, 0)');
        if (process.env.TICO_UI_SHOTS) await rr.screenshot({path: require('node:path').join(process.env.TICO_UI_SHOTS, 'bot-sidebar-page-rows.png')});
        await pinned.page.mouse.move(0, 0);
        await row('page:f1').focus();
        await pinned.page.keyboard.press('Tab');
        assert.equal(await row('page:f3').evaluate(el => document.activeElement === el && el.matches(':focus-visible') ? getComputedStyle(el).outlineStyle : 'not focused'), 'solid', at + ': the focus ring');
        assert.deepEqual(pinned.errors, [], at + ': page errors');
        await pinned.page.keyboard.press('Enter');
        await pinned.page.waitForFunction(() => location.hash === '#/page/f3');
      } else {
        assert.deepEqual(pinned.errors, [], at + ': page errors');
        // Anywhere on the row opens the page, its far end too.
        const box = (await row('page:f1').boundingBox())!;
        await pinned.page.mouse.click(box.x + box.width - 4, box.y + box.height / 2);
        await pinned.page.waitForFunction(() => location.hash === '#/page/f1');
      }
      await pinned.context.close();
    }
    // A card in the sidebar (#355): its count in the rail header's own count style, none at 0; its figures 16px at most.
    const carded = await open(browser, {width: 1280, height: 900}, 'light', {puts: [], page: 200, layouts: {lead: {tabs: DEFAULT,
      widgets: ['card:c1', 'card:c2', 'card:c3'].map(id => ({id, hidden: false, height: null}))}}});
    await carded.page.goto('https://tico-ui.test/#/bot/lead');
    const crail = carded.page.locator('#pane-tasks');
    await crail.locator('.bot-w[data-widget="card:c3"] .dash-big').waitFor();
    const head = (id: string) => crail.locator(`.bot-w[data-widget="${id}"] .bot-pin > h2.rail-h`);
    assert.equal(await head('card:c1').textContent(), 'My tasks 8', 'shown as MY TASKS 8, in the rail header\'s small caps');
    assert.equal(await head('card:c1').locator('.cnt').innerText(), '8');
    assert.equal(await crail.locator('.bot-w[data-widget="card:c1"] .dash-big').count(), 0, 'no figure under the header');
    assert.equal(await head('card:c2').locator('.cnt').count(), 0, 'no count at 0');
    assert.equal(await crail.locator('.bot-w[data-widget="card:c3"] .dash-big strong').evaluate(el => getComputedStyle(el).fontSize), '16px');
    assert.deepEqual(carded.errors, []);
    await carded.context.close();
    console.log('bot-task-tabs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
