import type * as PageSupport from './support/page.cts';
import type * as Routes from './support/routes.cts';
// A bot's task rail is one Tasks section with tabs, each with its count: Needs you (what this bot needs from the person:
// its asks of them on its own tasks or another bot's, and the task it gave them), Doing (the open tasks it holds, newest
// first), Blocked (open tasks of its own whose turn is someone else's, each saying what it waits on in its row's line),
// Done recently (the last 14 days), and Assigned to others, a filter tab the server counts for the person looking. The
// org tree's number is the Needs you rows, and stays so when the list moves. A Needs you row opens its card over the
// rail. The Tasks header's pencil edits the sidebar and there is no board icon; a bot with one tab shows its list
// without a tab bar. Fixtures only, no network (Needs you items and tasks as the v3 hub sends them).
const {chromium}: typeof import('playwright') = require('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs: typeof import('node:fs') = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as PageSupport.Api;
const {unrouted} = require('./support/routes.cts') as Routes.Api;
type Turn = {state: string; who: string | null; line: string; ask?: boolean; requirement_id?: string | null; since?: string | null; until?: null};
type Task = {id: string; title: string; owner_id: string; requester_id: string; status: 'open' | 'done' | 'closed'; pipeline_id: string;
  stage_id: string; labels: string[]; version: number; created_at: string; updated_at: string; turn: Turn | null; done_at?: string};
type Layout = {builtin?: string; name: string; show_count?: boolean; conditions?: {field: string; value: string | string[]}[]};
declare const S: {v2: {needs: unknown[]}};
declare const BOT: {slug: string};
declare const BOT_DONE: Map<string, {rows: {id: string}[]}>;
declare const BOT_TASK_FILTERED: Map<string, {tabs: {needs: {count: number; ids: string[]; matched_ids: string[]}}}>;
declare const needCountsSeen: string[];
declare const needCountObserver: MutationObserver;
declare function botTaskTabs(): unknown;
declare function botNeedsChanged(bot: string): void;
declare function botNeedsPaint(bot: string): void;
declare function botDonePaint(bot: string): void;
declare function botTaskFilterRead(bot: string): Promise<void>;
declare function needsMeCount(bot: string): number;
declare function loadBotTasksV2(bot: string): Promise<void>;
declare function renderTree(): void;
declare function v2Refresh(): Promise<void>;
declare const window: Window & typeof globalThis & {
  needCountsSeen: string[];
  needCountObserver: MutationObserver;
  fourTabs: () => unknown;
  botTaskTabs: () => unknown;
};

const ago = (minutes: number) => new Date(Date.now() - minutes * 60000).toISOString();
const bots = ['lead', 'qa', 'senior'].map(name => ({name, display_name: {lead: 'Head of Engineering', qa: 'QA Engineer',
  senior: 'Senior Engineer'}[name], org_parent: '', host: 'keeper', status: 'active', can_chat: true, users: [{id: 'ana', name: 'Ana'}], schedules: []}));
const bot = (who: string, line: string): Turn => ({state: 'bot', who, line});
const asks = (rid: string, line: string): Turn => ({state: 'person', who: 'human:ana', line, ask: true, requirement_id: rid, since: ago(30)});
const task = (id: string, title: string, over: Partial<Task> = {}): Task => ({id, title, owner_id: 'bot:lead', requester_id: 'human:ana', status: 'open',
  pipeline_id: 'general', stage_id: 'general-todo', labels: [], version: 1, created_at: ago(600), updated_at: ago(60),
  turn: bot('bot:lead', 'Head of Engineering to do it'), ...over});
const T = {
  own: task('t-own', 'Ship the release notes'),
  // A task it gave Ana: her turn, no ask.
  forAna: task('t-ana', 'Pick the release day', {owner_id: 'human:ana', requester_id: 'bot:lead',
    turn: {state: 'person', who: 'human:ana', line: 'Ana to do it'}}),
  // Its own tasks with an ask of Ana: a question, and an OK.
  waits: task('t-waits', 'Turn on the screen default', {turn: asks('r-waits', 'Which bucket holds the keys?'), created_at: ago(480)}),
  announce: task('t-announce', 'Send the announcement', {turn: asks('r-announce', 'OK the announcement'), created_at: ago(400)}),
  friday: task('t-friday', 'Plan the weekly summary', {turn: asks('r-friday', 'Is Friday fine for the summary?'), created_at: ago(350)}),
  // Blocked: waiting on another bot's task, and on something outside Tico.
  onQa: task('t-on-qa', 'Merge the export fix', {turn: {state: 'tasks', who: 'bot:qa', line: 'Waits on QA Engineer'}, created_at: ago(300)}),
  vendor: task('t-vendor', 'Renew the domain', {turn: {state: 'by_hand', who: null, line: "Waiting for the vendor's invoice."}, created_at: ago(200)}),
  roadmap: task('t-roadmap', 'Plan the import page', {created_at: ago(30)}),
  doneNow: task('t-done-now', 'Send the weekly summary', {status: 'done', turn: null, done_at: ago(2 * 1440), created_at: ago(3 * 1440)}),
  doneOld: task('t-done-old', 'Close the old mailbox', {status: 'done', turn: null, done_at: ago(20 * 1440), created_at: ago(21 * 1440)}),
  // Its ask of Ana on another bot's task.
  asked: task('t-asked', 'Support access fixes', {owner_id: 'bot:senior', requester_id: 'bot:lead', turn: asks('r-asked', 'Should full access refuse credential changes?')}),
  qa: task('t-qa', 'Run the release check', {owner_id: 'bot:qa', requester_id: 'bot:lead', turn: bot('bot:qa', 'QA Engineer working')}),
};
// Needs you as the hub sends it: an ask (with who asked) or a task of hers, each with its task.
type Need = {kind: 'ask' | 'task'; id: string; task_id: string; requirement_id: string | null; line: string; since: string; task: Task;
  ask?: {requirement_id: string; kind: 'answer' | 'ok'; line: string; met: boolean; who: string; asked_by: string; choices: string[] | null; shows: []; answer: null; may: string[]}};
const askNeed = (t: Task, kind: 'answer' | 'ok' = 'answer', choices: string[] | null = ['Yes', 'No']): Need => ({kind: 'ask', id: t.turn!.requirement_id!,
  task_id: t.id, requirement_id: t.turn!.requirement_id!, line: t.turn!.line, since: ago(30), task: t, ask: {requirement_id: t.turn!.requirement_id!, kind,
  line: t.turn!.line, met: false, who: 'human:ana', asked_by: 'bot:lead', choices: kind === 'ok' ? null : choices, shows: [], answer: null,
  may: kind === 'ok' ? ['ok', 'no'] : ['answer']}});
const taskNeed = (t: Task): Need => ({kind: 'task', id: t.id, task_id: t.id, requirement_id: null, line: t.turn!.line, since: ago(30), task: t});
const NEEDS: Need[] = [askNeed(T.asked), taskNeed(T.forAna), askNeed(T.waits), askNeed(T.friday), askNeed(T.announce, 'ok'),
  taskNeed(task('t-qa-ana', 'Approve the test plan', {owner_id: 'human:ana', requester_id: 'bot:qa', turn: {state: 'person', who: 'human:ana', line: 'Ana to do it'}}))];
const needsOf = (bot: string) => NEEDS.filter(n => n.task.requester_id === bot || n.ask?.asked_by === bot);

// Head of Engineering keeps Done recently beside the new defaults (a layout saved before them).
const LAYOUT: Layout[] = [{builtin: 'needs', name: 'Needs you'}, {builtin: 'active', name: 'Doing'}, {builtin: 'blocked', name: 'Blocked'},
  {builtin: 'done', name: 'Done recently'}, {name: 'Assigned to others', show_count: true, conditions: [{field: 'asked_by', value: 'this_bot'},
    {field: 'assigned_to', value: 'others'}, {field: 'status', value: ['open']}]}];

(async () => {
  const browser = await chromium.launch({headless: true, channel: process.env.TICO_BROWSER_CHANNEL === undefined ? 'chrome' : process.env.TICO_BROWSER_CHANNEL || undefined} as Parameters<typeof chromium.launch>[0]);
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}, serviceWorkers: 'block'});
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    let needs: Need[] = NEEDS, filterExtras: Need[] | null = null, releaseFilterRead: Promise<void> | null = null;
    const filterURLs: string[] = [];
    await page.route('**/*', async route => {
      const url = new URL(route.request().url()), p = url.pathname, q = url.searchParams;
      const json = (body: unknown) => route.fulfill({contentType: 'application/json', body: JSON.stringify(body)});
      if (/\/marked\.min\.js$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: 'window.marked={parse:s=>String(s)}'});
      if (url.origin !== 'https://tico-ui.test') return route.abort();
      const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
      if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
      if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
      if (p === '/api/me') return json({id: 'ana', role: 'owner', name: 'Ana', email: 'ana@example.test', cloud: true});
      if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}]});
      // A list, as the server sends it: the catch-all {} broke the tree drawn before Needs you whenever it landed ahead
      // of /api/v2/status, and the page never opened (v0.3.54 release check).
      if (p === '/api/issues') return json([]);
      if (p === '/api/employees') return json(bots);
      if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: [], schedules: []});
      if (p === '/api/v2/status') return json({bots: bots.map(b => ({bot: b.name, state: 'idle'}))});
      if (p === '/api/v2/needs-you') return json({actor: 'human:ana', items: needs, count: needs.length, version: 'v' + needs.length, next: null});
      if (p === '/api/v2/pipelines') return json({pipelines: [{id: 'general', name: 'General', stages: [{id: 'general-todo', name: 'To do'}]}]});
      // An OK with nothing bound to it has no preview; its card shows the ask's own line.
      if (p.startsWith('/api/v2/oks/')) return route.fulfill({status: 404, contentType: 'application/json', body: JSON.stringify({error: 'not_found'})});
      if (p === '/api/v2/goals') return json({goals: [], chain: [], reports: [], company: []});
      if (p === '/api/v2/updates') return json({updates: [], missed: [], unread: 0, next_before: null, today: {}});
      if (p === '/api/v2/tasks') {
        const all = Object.values(T);
        // The rail reads open and finished tasks apart, finished ones since a day; the server filters both.
        const st = q.get('status'), since = q.get('updated_since');
        const fits = (t: Task) => (!st || st === 'all' || st.split(',').includes(t.status)) && (!since || t.updated_at >= since);
        if (q.get('owner') === 'lead') return json({tasks: all.filter(t => t.owner_id === 'bot:lead' && fits(t))});
        return json({tasks: []});
      }
      // The built-in tabs and Assigned to others, which Ana may change on Head of Engineering only.
      const tabs = p.match(/^\/api\/v2\/bots\/([^/]+)\/task-tabs$/);
      if (tabs) {
        if (route.request().method() === 'PUT') LAYOUT.splice(0, LAYOUT.length, ...route.request().postDataJSON().tabs);
        return json({bot: tabs[1], tabs: LAYOUT, widgets: null, saved: false, personal: false, can_edit: tabs[1] === 'lead'});
      }
      // What the server counts for Ana: what Head of Engineering asked of anyone but her and itself, still open.
      if (p === '/api/v2/bots/lead/task-tabs/tasks') {
        const requested = (q.get('match_ids') || '').split(',');
        if (filterExtras) {
          filterURLs.push(url.href);
          if (releaseFilterRead) await releaseFilterRead;
          const extras = filterExtras.map(it => it.task);
          return json({bot: 'lead', tabs: [{key: 'needs', name: 'Needs you', count: extras.length, ids: extras.map(t => t.id), next: null,
            matched_ids: extras.filter(t => requested.includes(t.id)).map(t => t.id)}], tasks: extras});
        }
        const all = Object.values(T), open = (t: Task) => t.status === 'open';
        const newest = (a: Task, b: Task) => b.created_at.localeCompare(a.created_at);
        const owned = all.filter(t => t.owner_id === 'bot:lead');
        const rule = LAYOUT[0]!.conditions?.[0];
        const needRows = rule?.field === 'status'
          ? all.filter(t => (t.owner_id === 'bot:lead' || t.requester_id === 'bot:lead') && (rule.value as string[]).includes(t.status))
          : needsOf('bot:lead').filter(n => needs.includes(n)).map(n => n.task);
        const groups = [
          {key: 'needs', name: 'Needs you', rows: needRows},
          {key: 'active', name: 'Doing', rows: owned.filter(open).sort(newest)},
          {key: 'blocked', name: 'Blocked', rows: owned.filter(t => open(t) && !!t.turn && !['bot:lead', 'human:ana'].includes(String(t.turn.who))).sort(newest)},
          {key: 'c4', name: 'Assigned to others', rows: all.filter(t => open(t) && t.requester_id === 'bot:lead' && !['human:ana', 'bot:lead'].includes(t.owner_id))},
        ];
        return json({bot: 'lead', tabs: groups.map(({key, name, rows}) => ({key, name, count: rows.length, ids: rows.map(t => t.id), next: null,
          ...(key === 'needs' ? {matched_ids: rows.filter(t => requested.includes(t.id)).map(t => t.id)} : {})})), tasks: all});
      }
      if (p === '/api/v2/conversations') return json({conversations: []});
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('https://tico-ui.test/#/bot/lead/tasks');
    const rail = page.locator('#pane-tasks');
    const titles = async (list: string) => (await rail.locator(`${list} .tk-row .tk-title`).allInnerTexts()).map(s => s.trim());
    const tab = (key: string) => rail.locator(`#bot-tab-t-${key}`);
    await rail.locator('#t-needs .tk-row').first().waitFor();

    // One Tasks section with its tabs and their counts, before Updates and Files; no folded section after it.
    const order = await rail.evaluate(el => [...(el.querySelectorAll(':scope>.bot-w>.rail-sec') as unknown as Element[])].map(c => c.id));
    assert.deepEqual(order, ['bot-tasks', 'bot-latest', 'bot-files'], JSON.stringify(order));
    assert.equal(await rail.locator('#bot-assigned').count(), 0);
    assert.equal(await rail.locator('#bot-tasks .rail-h').innerText().then(s => s.trim().toLowerCase()), 'tasks');
    await page.waitForFunction(() => document.querySelector('#cnt-c4')?.textContent.trim() === '2'
      && document.querySelector('#cnt-needs')?.textContent.trim() === '5');
    const tabs = (await rail.locator('[data-bot-task-tab]').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
    assert.deepEqual(tabs, ['Needs you 5', 'Doing 7', 'Blocked 2', 'Done recently 1', 'Assigned to others 2'], JSON.stringify(tabs));
    assert.equal(await tab('needs').getAttribute('aria-selected'), 'true', 'Needs you leads while it has rows');

    // Needs you holds its asks of Ana (on its own tasks and another bot's) and the task it gave her; QA Engineer's is not here.
    const rows = await titles('#t-needs');
    assert.deepEqual(rows.slice().sort(), ['Plan the weekly summary', 'Send the announcement',
      'Support access fixes', 'Turn on the screen default', 'Pick the release day'].sort(), JSON.stringify(rows));
    assert.equal(await rail.locator('#bot-active').isHidden(), true, 'one tab at a time');
    const badge = page.locator('#tree a[href="#/bot/lead"] .cnt.needs');
    assert.equal((await badge.innerText()).trim(), '5', 'the tree number is the Needs you rows');
    assert.equal(await page.evaluate(() => needsMeCount('lead')), 5);
    assert.equal(await badge.getAttribute('title'), '5 things Head of Engineering needs from you');
    assert.ok(!(await titles('#t-open')).includes('Pick the release day'));
    assert.ok(!(await titles('#t-blocked')).includes('Pick the release day'));
    assert.ok(!(await titles('#t-c4')).includes('Pick the release day'));
    // An OK or a question opens its Needs you card over the rail; nothing unfolds in the rail.
    assert.equal(await rail.locator('#t-needs details').count(), 0);
    await rail.locator('#t-needs .tk-row', {hasText: 'Send the announcement'}).click();
    await page.locator('#need-card [data-key="r-announce"] .dc-q', {hasText: 'OK the announcement'}).waitFor();
    assert.equal(await page.evaluate(() => location.hash), '#/bot/lead/tasks', 'the rail stays where it was');
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#need-card')?.open);

    // Editing Needs you changes its task filter, while the org number stays a fixed attention count.
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click();
    const dialog = page.locator('#bot-filter-dialog');
    await dialog.waitFor();
    assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
    assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
    await dialog.locator('[data-cond-field]').selectOption('status');
    assert.ok((await dialog.innerText()).includes('The org chart number still counts everything Head of Engineering needs from you, not this tab.'));
    // Done tasks only: its two done tasks, and its five Needs you items, which the rule does not hold, still show.
    await dialog.locator('[data-cond-value="0"][value="open"]').uncheck();
    await dialog.locator('[data-cond-value="0"][value="done"]').check();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '7'
      && document.querySelector('#t-needs')?.textContent.includes('Send the weekly summary'));
    assert.ok((await titles('#t-needs')).includes('Pick the release day'), 'what needs Ana from it stays in the tab');
    assert.equal((await badge.innerText()).trim(), '5', 'editing a tab does not change fixed attention');
    // Pick a different count, then Reset visibly restores the single Needs: You condition.
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click(); await dialog.waitFor();
    // Open and done: ten open tasks it holds or asked for (its Needs you items among them, counted once) and two done.
    await dialog.locator('[data-cond-value="0"][value="open"]').check();
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '12');
    assert.equal((await badge.innerText()).trim(), '5');
    await rail.locator('#bot-sidebar-edit').click();
    await tab('needs').click(); await dialog.waitFor();
    await dialog.getByRole('button', {name: 'Reset to default'}).click();
    assert.equal(await dialog.locator('[data-cond-field]').inputValue(), 'needs');
    assert.equal(await dialog.locator('[data-cond-value="0"]').inputValue(), 'you');
    assert.ok(!(await dialog.innerText()).includes('The org chart number still counts'));
    await dialog.getByRole('button', {name: 'Save'}).click();
    await rail.locator('#bot-sidebar-done').click();
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '5');


    // Doing is the open tasks it holds, newest first.
    await tab('active').click();
    assert.deepEqual(await titles('#t-open'), ['Plan the import page', 'Renew the domain', 'Merge the export fix', 'Plan the weekly summary',
      'Send the announcement', 'Turn on the screen default', 'Ship the release notes']);
    assert.equal(await rail.locator('#bot-needs').isHidden(), true);

    // Blocked: its open tasks whose turn is someone else's (not its own, not Ana's); each row's line says what it waits on.
    await tab('blocked').click();
    assert.deepEqual(await titles('#t-blocked'), ['Renew the domain', 'Merge the export fix']);
    const tips = await rail.locator('#t-blocked .tk-row').evaluateAll(els => els.map(el => (el as HTMLElement).dataset.tip || ''));
    assert.match(tips[0]!, /Waiting for the vendor's invoice\.$/, tips[0]);
    assert.match(tips[1]!, /^QA Engineer · Waits on QA Engineer$/, tips[1]);

    // Done recently: finished in the last 14 days; the one from three weeks ago is not listed. It is drawn only once its
    // tab opens, 50 rows at a time (paging, at the end).
    assert.equal(await rail.locator('#t-done .tk-row').count(), 0, 'Done is drawn when its tab opens');
    await tab('done').click();
    assert.deepEqual(await titles('#t-done'), ['Send the weekly summary']);

    // Assigned to others keeps what it asked of another bot; its task for Ana is in Needs you, not here.
    await tab('c4').click();
    assert.deepEqual(await titles('#t-c4'), ['Support access fixes', 'Run the release check']);

    // A redraw keeps the picked tab while Needs you has rows, and leaves the rail scrolled where it was.
    await tab('done').click();
    const scrolled = await page.evaluate(() => {
      const r = document.querySelector<HTMLElement>('#pane-tasks')!;
      r.style.overflowY = 'auto'; r.style.height = '80px'; r.scrollTop = r.scrollHeight; return r.scrollTop;
    });
    assert.ok(scrolled > 0);
    await page.evaluate(() => botNeedsChanged('lead'));
    assert.equal(await page.evaluate(() => document.querySelector<HTMLElement>('#pane-tasks')!.scrollTop), scrolled);
    assert.equal(await tab('done').getAttribute('aria-selected'), 'true');
    assert.equal(await rail.locator('#bot-needs').isHidden(), true);
    await page.evaluate(() => { const r = document.querySelector<HTMLElement>('#pane-tasks')!; r.style.height = r.style.overflowY = ''; r.scrollTop = 0; });

    // A half-typed answer on a question's card survives a redraw of the rail, and the picked tab stays.
    await tab('needs').click();
    await rail.locator('#t-needs .tk-row', {hasText: 'Plan the weekly summary'}).click();
    await page.locator('#need-card [data-key="r-friday"] #dc-other').fill('Friday works');
    await page.evaluate(() => botNeedsChanged('lead'));
    assert.equal(await page.locator('#need-card #dc-other').inputValue(), 'Friday works');
    assert.equal(await tab('needs').getAttribute('aria-selected'), 'true');
    await page.locator('#need-card [data-dc="close"]').click();
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#need-card')?.open);

    // An ask whose task the server counted on a later page is counted once, even before that page loads.
    await page.evaluate(() => {
      const tab = BOT_TASK_FILTERED.get('lead')!.tabs.needs;
      tab.ids = tab.ids.filter(id => id !== 't-asked'); tab.matched_ids = ['t-asked'];
      botNeedsPaint('lead');
    });
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '5');
    await page.evaluate(() => botTaskFilterRead('lead'));
    // The question answered elsewhere leaves Needs you: the tab's count and the tree's number move together.
    needs = needs.filter(it => !['r-asked', 'r-friday', 'r-announce'].includes(it.id));
    await page.evaluate(async () => { await v2Refresh(); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '2');
    assert.equal((await badge.innerText()).trim(), '2');
    assert.equal(await rail.locator('#t-needs .tk-row').count(), 2);

    // Nothing needs Ana: the tab says so, and the tree's number goes.
    needs = [];
    for (const t of [T.waits, T.forAna]) { t.status = 'done'; t.turn = null; t.done_at = ago(1); }
    await page.evaluate(async () => { await v2Refresh(); await loadBotTasksV2('lead'); renderTree(); });
    await page.waitForFunction(() => document.querySelector('#cnt-needs')?.textContent.trim() === '0');
    assert.equal(await page.locator('#tree a[href="#/bot/lead"] .cnt.needs').count(), 0);
    assert.equal((await rail.locator('#t-needs').innerText()).trim(), 'Nothing needs you.');
    assert.deepEqual(await titles('#t-c4'), ['Support access fixes', 'Run the release check']);

    // Many supplemental questions keep requests bounded and deduplicate beyond the first chunk.
    // A question arriving during the read must not be added twice to that read's total.
    const extra = (id: string): Need => { const t = {...T.asked, id: 'task-' + id}; return {...askNeed(t), id, requirement_id: id, task_id: t.id}; };
    filterExtras = Array.from({length: 240}, (_, i) => extra('question-' + String(i).padStart(36, '0')));
    await page.evaluate(items => {
      S.v2.needs = items; window.needCountsSeen = [];
      window.needCountObserver = new MutationObserver(() => needCountsSeen.push(document.querySelector<HTMLElement>('#cnt-needs')!.textContent!.trim()));
      needCountObserver.observe(document.querySelector('#cnt-needs')!, {childList: true, subtree: true});
    }, filterExtras);
    let release!: () => void;
    releaseFilterRead = new Promise<void>(resolve => { release = resolve as () => void; });
    const requested = page.waitForRequest(r => r.url().includes('/task-tabs/tasks?match_ids='));
    const reading = page.evaluate(() => botTaskFilterRead('lead'));
    await requested;
    const late = extra('question-late');
    filterExtras.push(late);
    await page.evaluate(item => { S.v2.needs.push(item); }, late);
    releaseFilterRead = null; release();
    await reading;
    assert.ok(filterURLs.length > 1, 'a large supplement list uses more than one bounded read');
    assert.ok(filterURLs.every(url => url.length < 4200), 'no request exceeds common proxy header limits');
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '241', 'late question is already in the task total');
    await page.evaluate(() => botTaskFilterRead('lead'));
    assert.equal((await tab('needs').locator('.cnt').innerText()).trim(), '241');
    assert.equal(await rail.locator('#t-needs .tk-row').count(), 241, 'the next read includes the new question once');
    assert.ok(!(await page.evaluate(() => { needCountObserver.disconnect(); return needCountsSeen; })).includes('242'), 'no transient double count');
    filterExtras = null;

    // Done recently pages 50 rows at a time.
    await tab('done').click();
    await page.evaluate(() => { const d = BOT_DONE.get('lead')!, row = d.rows[0]!; d.rows = Array.from({length: 120}, (_, i) => ({...row, id: 'many-' + i})); botDonePaint('lead'); });
    assert.equal(await rail.locator('#t-done .tk-row').count(), 50);
    await rail.getByRole('button', {name: 'Show 50 more'}).click();
    assert.equal(await rail.locator('#t-done .tk-row').count(), 100);
    await rail.getByRole('button', {name: 'Show 20 more'}).click();
    assert.equal(await rail.locator('#t-done .tk-row').count(), 120);
    assert.equal(await rail.locator('.bot-done-more').count(), 0);

    // The Tasks header has no board icon; the sidebar's one edit icon is for whoever may change this bot's
    // layout, and Ana manages Head of Engineering here. The old tab settings gear is gone.
    assert.equal(await rail.locator('#bot-tasks-board').count(), 0);
    const reopen = async () => {
      await page.evaluate(() => { location.hash = '#/bot/qa'; });
      await page.waitForFunction(() => BOT?.slug === 'qa');
      await page.evaluate(() => { location.hash = '#/bot/lead/tasks'; });
      await rail.locator('#t-open .tk-row').first().waitFor({state: 'attached'});
    };
    await rail.locator('#bot-sidebar-edit:not([hidden])').waitFor();
    assert.equal(await rail.locator('#bot-tasks-settings').count(), 0);

    // Spec test 11: a bot with one tab shows that one list, with no tab bar over it.
    await page.evaluate(() => { window.fourTabs = botTaskTabs; window.botTaskTabs = () => [['active', 'Doing', 'cnt-active']]; });
    await reopen();
    assert.equal(await rail.locator('.bot-task-tabs').isHidden(), true, 'no tab bar over a single list');
    assert.equal(await rail.locator('#bot-active').isVisible(), true);
    for (const key of ['needs', 'blocked', 'done']) assert.equal(await rail.locator(`#bot-${key}`).isHidden(), true, key);
    assert.equal(await rail.locator('#t-open .tk-row').count(), 6);
    await page.evaluate(() => { window.botTaskTabs = window.fourTabs; });
    await reopen();
    assert.equal(await rail.locator('.bot-task-tabs').isVisible(), true);
    assert.equal(await rail.locator('[data-bot-task-tab]').count(), 5);

    assert.deepEqual(errors, []);
    console.log('bot-needs: ok');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
