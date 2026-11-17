import type {Page, Route} from 'playwright';
// Offline browser check of moving tasks to another pipeline from a board (task-pipelines.ts, task-props.ts
// taskMoveToMenu; docs/pipelines-v3.md "Move to another pipeline"): a shift-click picks a card instead of opening it and
// starts selecting, where a plain click picks too; Move to… lists every pipeline's stages but the one the picked tasks
// are in, and typing finds by each word ("back ice" is Backlog › On ice); one line confirms with the count, with an
// optional why; one request moves them all. The result names what stayed and why, and those stay picked. ⋯ › Select
// starts selecting, All in <stage> picks the stage's tasks, ✕ stops.
// Run: npm run build:ui && node ui/tests/pipeline-move.cts
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const stage = (id: string, name: string, byHand = false) => ({id, name, purpose: '', owner: null, by_hand: byHand, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: []});
const pipeline = (id: string, name: string, prefix: string, stages: ReturnType<typeof stage>[]) => ({id, name, goal: '', prefix,
  manager_id: null, owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages,
  done: {id: `${id}-done`, name: 'Done'}, closed: {id: `${id}-closed`, name: 'Closed'}, may_change: true});
const PIPELINES = [pipeline('dev', 'Dev', 'D', [stage('ideas', 'Ideas', true), stage('build', 'Build')]),
  pipeline('backlog', 'Backlog', 'B', [stage('on-ice', 'On ice', true), stage('next-up', 'Next up')])];
const brief = (id: string, number: number, title: string, stageId: string) => ({id, title, number, pipeline_id: 'dev', stage_id: stageId,
  status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: 1, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});
const TASKS = [brief('t1', 101, 'Old Trello idea', 'ideas'), brief('t2', 102, 'Another old idea', 'ideas'), brief('t3', 103, 'Export', 'build')];
const moves: Record<string, unknown>[] = [], opened: string[] = [];

async function serve(page: Page, errors: string[]): Promise<void> {
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    if (p.startsWith('/api/v2/pipelines/')) return json({pipeline: PIPELINES.find(x => p.endsWith(`/${x.id}`))});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks: TASKS});
    if (p === '/api/v2/tasks/move' && r.method() === 'POST') {
      const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
      moves.push(body);
      // t2 is in a bot's running turn: it stays; t1 goes.
      const t1 = TASKS.find(t => t.id === 't1')!;
      Object.assign(t1, {pipeline_id: 'backlog', stage_id: 'on-ice', version: 2});
      return json({batch_id: 'b1', moved: ['t1'], moved_unseen: 0,
        skipped: [{id: 't2', code: 'task_turn_running', reason: 'Builder is working on this task now.'}]});
    }
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && r.method() === 'GET') { opened.push(one[1]!); return json(unrouted(p)); }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
}

const picked = (page: Page) => page.locator('.tk-card.pv-picked').evaluateAll(els => els.map(el => (el as HTMLElement).dataset['task']));
const menuItems = (page: Page) => page.locator('.prop-pop:popover-open .tl-mi:not([hidden])').allInnerTexts();

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 800}, serviceWorkers: 'block'});
    await serve(page, errors);
    await page.goto('http://tico-ui.test/#/tasks?pipeline=dev&layout=columns');
    await page.locator('.tk-card[data-task="t1"]').waitFor();

    // 1. A shift-click picks the card and opens nothing; while selecting, a plain click picks too.
    await page.locator('.tk-card[data-task="t1"]').click({modifiers: ['Shift']});
    await page.locator('.pv-sel').waitFor();
    await page.locator('.tk-card[data-task="t2"]').click();
    assert.deepEqual(await picked(page), ['t1', 't2']);
    assert.equal((await page.locator('.pv-sel .pv-bar-text').innerText()).trim(), '2 selected');
    assert.deepEqual(opened, [], 'picking opens no task');
    assert.equal(await page.locator('.tk-card[data-task="t1"]').getAttribute('aria-pressed'), 'true');

    // 2. Move to…: every stage but the one they are in; each word typed finds on its own.
    await page.locator('[data-pv-sel-move]').click();
    await page.locator('.prop-pop:popover-open .tl-menu-q').waitFor();
    const all = (await menuItems(page)).map(t => t.replace(/\s+/g, ' ').trim());
    assert.deepEqual(all, ['Dev › Build', 'Backlog › On ice', 'Backlog › Next up'], all.join(' | '));
    await page.locator('.prop-pop:popover-open .tl-menu-q').fill('back ice');
    assert.deepEqual((await menuItems(page)).map(t => t.replace(/\s+/g, ' ').trim()), ['Backlog › On ice']);
    await page.keyboard.press('Enter');

    // 3. One line confirms, with the count; the why is optional. One request moves them all.
    const confirm = page.locator('.prop-pop:popover-open');
    await confirm.locator('.tl-menu-h').filter({hasText: 'Move 2 tasks to Backlog › On ice?'}).waitFor();
    await confirm.locator('.tl-menu-q').fill('old Trello ideas');
    await page.keyboard.press('Enter');
    await page.locator('.pv-bar:not(.pv-sel)').waitFor();
    assert.deepEqual(moves, [{ids: ['t1', 't2'], to_pipeline: 'backlog', to: 'on-ice', why: 'old Trello ideas'}]);

    // 4. What stayed is said with why, and stays picked; what moved left the board.
    const said = (await page.locator('.pv-bar:not(.pv-sel) .pv-bar-text').innerText()).trim();
    assert.equal(said, "Moved 1. 1 didn't move: 1 is in a bot's running turn.");
    await page.locator('.tk-card[data-task="t1"]').waitFor({state: 'detached'});
    assert.deepEqual(await picked(page), ['t2']);
    if (process.env['TICO_SHOTS']) await page.screenshot({path: `${process.env['TICO_SHOTS']}/pipeline-move-result.png`});

    // 5. ✕ stops selecting; ⋯ › Select starts again, and All in Ideas picks the stage's tasks.
    await page.locator('[data-pv-sel-x]').click();
    await page.locator('.pv-sel').waitFor({state: 'detached'});
    assert.deepEqual(await picked(page), []);
    await page.locator('#task-menu').click();
    await page.locator('#task-mi-select').click();
    await page.locator('.pv-sel').waitFor();
    await page.locator('[data-pv-sel-all="ideas"]').click();
    assert.deepEqual(await picked(page), ['t2']);
    assert.equal((await page.locator('.pv-sel .pv-bar-text').innerText()).trim(), '1 selected');

    assert.deepEqual(errors, []);
    console.log('pipeline-move: ok');
  } finally {
    await browser.close();
  }
})().catch((error: unknown) => { console.error(error); process.exit(1); });
