import type {Route} from 'playwright';
// Offline browser check of a pipeline's columns on the Tasks page (docs/pipelines-v3.md S7): a task dragged to another
// column is moved by the engine (POST /api/v2/tasks/{id}/move) and shows there. When the move is refused:
// - with requirements a person may push past, the page asks "Move to Build without Spec page?" and sends the why;
// - with a refusal it cannot push past, it shows the engine's line and the task stays where it was.
// Letting go after a drag never opens the task. A stage's header opens the pipeline editor on the record as the server
// sends it ({pipeline: …}), and a save there draws the saved record.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;
declare function tasksLiveApply(events: {id: string; task?: unknown; gone?: boolean}[]): void;

const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const req = {kind: 'owner_done', settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null,
  expected_minutes: null, expected_bots_only: false, on_enter: {}, requirements: [req]});
const pipeline = {id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null, owner_id: 'human:ana', private: false,
  factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: [stage('spec', 'Spec'), stage('build', 'Build')],
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true};
const brief = (id: string, title: string, stageId: string, version = 1) => ({id, title, number: 1, pipeline_id: 'product', stage_id: stageId,
  status: 'open', owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false, turn: null,
  version, created_at: '2026-11-09T10:00:00Z', updated_at: '2026-11-09T10:00:00Z'});

// Task #339: columns are at least 300px, so a board whose stages don't fit scrolls sideways. Each redraw replaced the
// columns and put the board back at its first stage. At 1700×900 with the manager's chat docked, scrolled to 300px, the
// board stays there through a search typed and cleared, a live update, a drop on another stage, the refused-move bar
// closed, a reload and Threads and back; another pipeline keeps its own position. The board's edges fade where stages lie
// beyond them; a stage with no task is its header's height, and every column stretches while a task is held. Runs on the
// fake server of tasks-page.cts.
async function scrollKept(browser: import('playwright').Browser): Promise<void> {
  const {open, PIPELINES, fixtures, task, brief} = require('./tasks-page.cts') as import('./tasks-page.cts').Api;
  const product = PIPELINES.find(p => p.id === 'product')!, ops = PIPELINES.find(p => p.id === 'ops')!;
  (product as {manager_id: string | null}).manager_id = 'bot:pm';
  const more = (id: string, name: string) => ({...product.stages[0]!, id, name});
  product.stages.push(more('ship', 'Ship'), more('learn', 'Learn'));
  ops.stages.push(more('check', 'Check'), more('tell', 'Tell'), more('file', 'File'));
  const tasks = [...fixtures(), task('t-8', 'Stale version', 'build'), task('t-9', 'Sent back', 'review')];
  const {page, errors} = await open(browser, {viewport: {width: 1700, height: 900}, tasks, needs: [], hash: '#/tasks?pipeline=product&layout=columns'});
  const cols = page.locator('#task-body .pv-cols');
  await cols.waitFor();
  // The manager's chat docked beside the stages, open at first from 1600 px (page-chat.ts): the stages do not fit.
  await page.locator('aside.pgc .pgc-head').waitFor();
  const x = () => cols.evaluate(el => el.scrollLeft);
  const settled = async (want: number, what: string) => {
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    assert.equal(await x(), want, `still at ${want} after ${what}`);
  };
  await cols.evaluate(el => { el.scrollLeft = 300; });
  await settled(300, 'scrolling');
  // The board scrolls in its own box with a cue: scrolled to the middle, both edges fade (task-pipelines.ts pvEdges).
  const fades = () => cols.evaluate(el => [el.classList.contains('fade-l'), el.classList.contains('fade-r')]);
  assert.deepEqual(await fades(), [true, true], 'scrolled to the middle: both edges fade');
  // The fade leaves the scrollbars alone: the strip each takes is masked in whole (0 where they overlay the content).
  const bars = await cols.evaluate(el => ({v: (el as HTMLElement).offsetWidth - el.clientWidth, h: (el as HTMLElement).offsetHeight - el.clientHeight,
    size: getComputedStyle(el).maskSize || getComputedStyle(el).getPropertyValue('-webkit-mask-size')}));
  assert.equal(bars.size, `100% 100%, ${bars.v}px 100%, 100% ${bars.h}px`, `the scrollbars' strips are kept whole: ${JSON.stringify(bars)}`);
  // A stage with no task is as tall as its header (no empty box under it)...
  const emptyCol = () => page.locator('[data-pv-col="learn"]').evaluate(el => ({col: el.getBoundingClientRect().height,
    head: el.querySelector('.pv-col-head')!.getBoundingClientRect().height, body: getComputedStyle(el.querySelector('.pv-col-body')!).display,
    tallest: Math.max(...[...document.querySelectorAll('.pv-col')].map(c => c.getBoundingClientRect().height))}));
  const idle = await emptyCol();
  assert.ok(idle.body === 'none' && idle.col - idle.head <= 17 && idle.col < idle.tallest, `an empty stage is its header's height: ${JSON.stringify(idle)}`);
  // ...and while a task is held every column is as tall as the tallest, so an empty stage takes a drop anywhere down it.
  const held = await page.locator('.tk-card[data-task="t-4"]').boundingBox();
  await page.mouse.move(held!.x + held!.width / 2, held!.y + held!.height / 2);
  await page.mouse.down();
  await page.mouse.move(held!.x + held!.width / 2 + 20, held!.y + held!.height / 2, {steps: 3});
  const holding = await emptyCol();
  assert.ok(Math.abs(holding.col - holding.tallest) < 1, `held: the empty stage stretches to the tallest: ${JSON.stringify(holding)}`);
  await page.keyboard.press('Escape');
  await page.mouse.up();
  assert.ok(Math.abs((await emptyCol()).col - idle.col) < 1, 'let go: back to its header');
  await settled(300, 'a held task let go with Escape');

  // Search typed and cleared: two redraws, one with fewer cards.
  await page.locator('#task-q').fill('Rounding');
  await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-card').length === 1).catch(async (e: Error) => {
    // Say what the page shows instead, so a failure here can be read from the log alone.
    const seen = await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q'); return {
      hash: location.hash, cards: document.querySelectorAll('#task-body .tk-card').length, q: q?.value, qIn: q?.parentElement?.parentElement?.className,
      open: [...document.querySelectorAll('dialog[open], :popover-open')].map(d => d.id || d.className), drag: document.body.className,
      focus: document.activeElement?.id || document.activeElement?.tagName}; });
    throw new Error(`the search did not narrow the board: ${JSON.stringify(seen)} (${e.message.split('\n')[0]})`);
  });
  await settled(300, 'a search');
  await page.locator('#task-q').fill('');
  await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-card').length > 1);
  await settled(300, 'the search cleared');

  // A live update of a task on the board.
  const live = tasks.find(t => t.id === 't-4')!;
  Object.assign(live, {title: 'Rounding in the API, renamed', version: live.version + 1});
  await page.evaluate(b => tasksLiveApply([{id: b.id, task: b}]), brief(live));
  await page.locator('#task-body .tk-card[data-task="t-4"]', {hasText: 'renamed'}).waitFor();
  await settled(300, 'a live update');

  // Drags a card to a column from the card's middle, clear of the edges where the board scrolls by itself.
  const drag = async (id: string, col: string) => {
    const from = await page.locator(`.tk-card[data-task="${id}"]`).boundingBox(), to = await page.locator(`[data-pv-col="${col}"]`).boundingBox();
    await page.mouse.move(from!.x + from!.width / 2, from!.y + from!.height / 2);
    await page.mouse.down();
    await page.mouse.move(from!.x + from!.width / 2 + 20, from!.y + from!.height / 2, {steps: 3});
    await page.mouse.move(to!.x + to!.width / 2, to!.y + 40, {steps: 8});
    await page.mouse.up();
  };
  const moved = page.waitForResponse(r => r.url().endsWith('/tasks/t-9/move'));
  await drag('t-9', 'build');
  await page.locator('[data-pv-col="build"] .tk-card[data-task="t-9"]').waitFor();
  assert.equal((await moved).status(), 200, 'the engine moved it');
  await settled(300, 'a drop on another stage');

  // A move the server refuses (the task changed meanwhile) shows the bar; closing it draws again.
  tasks.find(t => t.id === 't-8')!.version += 1;
  await drag('t-8', 'review');
  const bar = page.locator('.pv-bar[role="alert"]');
  await bar.waitFor();
  await bar.locator('[data-pv-bar-x]').click();
  await bar.waitFor({state: 'detached'});
  await settled(300, 'the refused-move bar closed');

  // A reload in the same tab opens where it was; another pipeline keeps its own place.
  await page.reload();
  await cols.waitFor();
  await page.waitForFunction(() => document.querySelector('#task-body .pv-cols')!.scrollLeft > 0);
  await settled(300, 'a reload');
  // Threads and back in the same tab: the page draws a new #task-body, and the board opens where it was.
  await page.evaluate(() => { location.hash = '#/threads'; });
  await cols.waitFor({state: 'detached'});
  await page.evaluate(() => { location.hash = '#/tasks?pipeline=product&layout=columns'; });
  await page.locator('#task-body [data-pv-col="spec"]').waitFor();
  await settled(300, 'Threads and back');
  await page.evaluate(() => { location.hash = '#/tasks?pipeline=ops&layout=columns'; });
  await page.locator('#task-body [data-pv-col="todo"]').waitFor();
  assert.ok(await cols.evaluate(el => el.scrollWidth > el.clientWidth), 'Operations scrolls sideways too');
  await settled(0, 'opening another pipeline');
  assert.deepEqual(await fades(), [false, true], 'at its first stage only the right edge fades');
  await cols.evaluate(el => { el.scrollLeft = el.scrollWidth; });
  await page.waitForFunction(() => document.querySelector('#task-body .pv-cols')!.classList.contains('fade-l'));
  assert.deepEqual(await fades(), [true, false], 'at its last stage only the left edge fades');
  await cols.evaluate(el => { el.scrollLeft = 0; });
  await page.waitForFunction(() => !document.querySelector('#task-body .pv-cols')!.classList.contains('fade-l'));
  await page.evaluate(() => { location.hash = '#/tasks?pipeline=product&layout=columns'; });
  await page.locator('#task-body [data-pv-col="spec"]').waitFor();
  await settled(300, 'coming back to Product');
  assert.deepEqual(errors, []);
  await page.close();
}

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  const tasks = [brief('t1', 'Saved carts', 'spec'), brief('t2', 'Refund flow', 'spec')];
  const moves: {id: string; body: Record<string, unknown>}[] = [];
  const saves: Record<string, unknown>[] = [];
  // Knobs on the fake server: saves refused as if someone saved just before each one, and a re-read held back.
  let refuseNext = 0;
  let readGate: Promise<void> | null = null;
  try {
    const page = await browser.newPage({viewport: {width: 1200, height: 900}, serviceWorkers: 'block'});
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
      if (p === '/api/v2/pipelines') return json({pipelines: [pipeline]});
      // As backend/task_pipelines_routes.py answers: the record inside {pipeline: …}, on a read and on a save.
      if (p === '/api/v2/pipelines/product' && r.method() === 'POST') {
        const body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        saves.push(body);
        if (refuseNext > 0) {
          refuseNext -= 1;
          pipeline.settings_version += 1;   // someone else saved first, leaving Build as it was
          return json({error: 'task_changed', detail: 'The pipeline changed since you looked. Read it again.'}, 409);
        }
        // As backend/pipeline_settings.py save: a save at a version that is no longer the record's is refused.
        if (body['settings_version'] !== pipeline.settings_version) {
          return json({error: 'task_changed', detail: 'The pipeline changed since you looked. Read it again.'}, 409);
        }
        const {settings_version: _seen, ...given} = body;
        Object.assign(pipeline, JSON.parse(JSON.stringify(given)), {settings_version: pipeline.settings_version + 1});
        return json({pipeline});
      }
      if (p === '/api/v2/pipelines/product') return readGate ? readGate.then(() => json({pipeline})) : json({pipeline});
      if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
      if (p === '/api/v2/tasks' && r.method() === 'GET') return json({tasks});
      const move = p.match(/^\/api\/v2\/tasks\/([^/]+)\/move$/);
      if (move && r.method() === 'POST') {
        const id = move[1]!, body = JSON.parse(r.postData() || '{}') as Record<string, unknown>;
        moves.push({id, body});
        if (id === 't1' && !body['why']) {
          return json({error: 'task_not_met', detail: 'Spec page is not met.', missing: ['Spec page'], next: ['move'], version: 1}, 409);
        }
        if (id === 't2') {
          return json({error: 'task_no_push_past', detail: "PR merged can't be pushed past. Move back or close.", missing: ['PR merged'], next: [], version: 1}, 409);
        }
        const t = tasks.find(x => x.id === id)!;
        Object.assign(t, {stage_id: 'build', version: t.version + 1});
        return json({task: t});
      }
      if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
      return json(unrouted(p));
    });
    await page.goto('http://tico-ui.test/#/tasks?pipeline=product&layout=columns');
    const card = (id: string) => page.locator(`.tk-card[data-task="${id}"]`);
    const inCol = (col: string, id: string) => page.locator(`[data-pv-col="${col}"] .tk-card[data-task="${id}"]`);
    await card('t1').waitFor();

    // Drags a task to a column with the mouse, in steps, as a person would.
    const drag = async (id: string, col: string) => {
      // The page draws again after each read: both boxes are read in one go, once both are on screen.
      const boxes = await page.waitForFunction(([t, c]) => {
        const a = document.querySelector(`.tk-card[data-task="${t}"]`)?.getBoundingClientRect();
        const b = document.querySelector(`[data-pv-col="${c}"]`)?.getBoundingClientRect();
        return a?.width && b?.width ? {from: {x: a.x, y: a.y, height: a.height}, to: {x: b.x, y: b.y, width: b.width}} : null;
      }, [id, col]);
      const {from, to} = await boxes.jsonValue() as {from: {x: number; y: number; height: number}; to: {x: number; y: number; width: number}};
      await page.mouse.move(from.x + 20, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + 40, from.y + from.height / 2, {steps: 3});
      await page.mouse.move(to.x + to.width / 2, to.y + 40, {steps: 8});
      await page.mouse.up();
    };


    // A refusal it cannot push past says the engine's line, and the task stays.
    await drag('t2', 'build');
    const said = page.locator('.pv-bar[role="alert"]');
    await said.waitFor();
    assert.equal(await said.locator('input').count(), 0, 'no why to give');
    assert.equal((await said.locator('.pv-bar-text').innerText()).trim(), "Refund flow: PR merged can't be pushed past. Move back or close. Missing: PR merged.");
    assert.equal(await inCol('spec', 't2').count(), 1, 'it stays in Spec');
    assert.ok(!(await page.evaluate(() => location.hash)).startsWith('#/task/'), 'the drag opened nothing');
    await said.locator('[data-pv-bar-x]').click();
    assert.equal(await page.locator('.pv-bar').count(), 0, 'closed');

    // A stage's header opens that stage's editor, and only that stage (style guide, Pipeline stage): its name in the
    // title and nowhere else, its fields as the body (no row to open or close), no pipeline fields, + Stage or moves.
    await page.locator('[data-pv-settings="build"]').click();
    await page.locator('#pe-dialog .pe-stage-one').waitFor();
    assert.equal(await page.locator('#pe-title').innerText(), 'Product › Build');
    assert.equal(await page.locator('#pe-dialog details, #pe-dialog summary, #pe-dialog .pe-stage-name').count(), 0, 'no stage row, nothing to collapse');
    assert.equal(await page.locator('#pe-dialog [data-pe-sf="name"]').inputValue(), 'Build', 'its name is a field');
    assert.equal(await page.locator('#pe-dialog [data-pe-pf], #pe-dialog [data-pe-stage-add]').count(), 0, 'no pipeline fields, no + Stage');
    assert.equal(await page.locator('#pe-dialog .pe-tools, #pe-dialog [data-pe-up], #pe-dialog [data-pe-stage-x]').count(), 0, 'no moves or Remove');
    assert.equal(await page.locator('#pe-dialog [data-pe-leave-row]').count(), 1, 'its requirement is a row of To leave');
    assert.equal(await page.locator('#pe-dialog [data-pe-save]').isVisible(), true);
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});

    // ⋯ › Edit pipeline opens the whole pipeline; a save sends the record read and draws the saved one.
    await page.locator('#task-menu').click();
    await page.locator('#task-mi-edit').click();
    const goal = page.locator('#pe-dialog [data-pe-pf="goal"]');
    await goal.waitFor();
    assert.equal(await page.locator('#pe-title').innerText(), 'Product');
    // S11 as drawn: each stage one row, in order; a closed row shows how many requirements it has; labels sit
    // left-aligned above their fields, and Prefix is a short field beside Manager.
    assert.deepEqual(await page.locator('#pe-dialog .pe-stage-name').allInnerTexts(), ['Spec', 'Build']);
    assert.equal((await page.locator('#pe-dialog .pe-stage:not([open]) .pe-stage-reqs').first().innerText()).trim(), '✓ 1');
    const layout = await page.evaluate(() => {
      const box = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
      const label = document.querySelector('#pe-dialog .pe-fields .pe-f > span')!;
      return {align: getComputedStyle(label).textAlign, labelX: label.getBoundingClientRect().x, goalX: box('[data-pe-pf="goal"]').x,
        goalW: box('[data-pe-pf="goal"]').width, prefixW: box('[data-pe-pf="prefix"]').width,
        prefixY: box('[data-pe-pf="prefix"]').y, managerY: box('[data-pe-pf="manager_id"]').y};
    });
    assert.ok(['left', 'start'].includes(layout.align), `labels left-aligned, got ${layout.align}`);
    assert.ok(Math.abs(layout.labelX - layout.goalX) < 2, 'a label starts where its field starts');
    assert.ok(layout.prefixW < layout.goalW / 4, 'Prefix is a short field');
    assert.ok(Math.abs(layout.prefixY - layout.managerY) < 2, 'Prefix sits beside Manager');
    await goal.fill('Ship it');
    await page.locator('#pe-dialog [data-pe-save]').click();
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])') && !document.querySelector('#pe-dialog [data-pe-error]:not([hidden])'));
    assert.equal(saves.length, 1);
    assert.equal(saves[0]!['goal'], 'Ship it');
    assert.equal(saves[0]!['settings_version'], 1, 'saved at the version it was read at');
    assert.deepEqual((saves[0]!['stages'] as {id: string}[]).map(x => x.id), ['spec', 'build']);
    assert.equal(await page.locator('#pe-dialog [data-pe-error]:not([hidden])').count(), 0);
    assert.equal(await page.locator('#pe-dialog').count(), 1, 'the editor stays open');
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});

    // One stage's editor saves that stage and leaves the rest as it was read: Build (the second stage) from its header.
    const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
    const oneStage = async (sid: string) => {
      await page.locator(`[data-pv-settings="${sid}"]`).click();
      await page.locator('#pe-dialog .pe-stage-one').waitFor();
    };
    const purpose = page.locator('#pe-dialog [data-pe-sf="purpose"]');
    const specRead = clone(pipeline.stages[0]!);
    await oneStage('build');
    await purpose.fill('Ship the build');
    await page.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 100 && saves.length < 2; i++) await page.waitForTimeout(20);
    const one = saves.at(-1)!;
    assert.deepEqual((one['stages'] as {id: string}[]).map(x => x.id), ['spec', 'build'], 'every stage, in order');
    assert.deepEqual((one['stages'] as unknown[])[0], specRead, 'Spec is sent as it was read, requirements and all');
    assert.equal((one['stages'] as {purpose: string}[])[1]!.purpose, 'Ship the build');
    assert.deepEqual([one['goal'], one['manager_id']], ['Ship it', null], 'the goal and the manager are untouched');
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});

    // Someone else saves while Build is open on its own, and leaves Build as it was (a new Goal, another requirement on
    // Spec): no clash for this person. The save goes again on their record with this Build in it, with no banner, so
    // nothing they never saw is undone.
    await oneStage('build');
    const theirReq = {...req, kind: 'page', line: 'Spec page'};
    Object.assign(pipeline, {goal: 'Theirs', settings_version: pipeline.settings_version + 1});
    pipeline.stages[0]!.requirements = [...pipeline.stages[0]!.requirements, theirReq];
    const specTheirs = clone(pipeline.stages[0]!);
    await purpose.fill('Mine');
    let before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 150 && saves.length < before + 2; i++) await page.waitForTimeout(20);
    assert.equal(saves.length, before + 2, 'refused once, then saved again by itself');
    const quiet = saves.at(-1)!;
    assert.equal(quiet['settings_version'], pipeline.settings_version - 1, 'at their version');
    assert.equal(quiet['goal'], 'Theirs', 'their Goal is kept');
    assert.deepEqual((quiet['stages'] as unknown[])[0], specTheirs, 'their Spec is kept');
    assert.equal((quiet['stages'] as {purpose: string}[])[1]!.purpose, 'Mine', 'and this person\'s Build is saved');
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    assert.equal(await page.locator('#pe-dialog .pe-clash').count(), 0, 'no banner');

    // Their save added a stage before Build, so Build sits at a new place: still no clash; Build goes in where it is now.
    pipeline.stages = [{...clone(pipeline.stages[0]!), id: 'qa', name: 'QA'}, ...pipeline.stages];
    pipeline.settings_version += 1;
    await purpose.fill('Shifted');
    before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 150 && saves.length < before + 2; i++) await page.waitForTimeout(20);
    const shifted = saves.at(-1)!;
    assert.deepEqual((shifted['stages'] as {id: string; purpose: string}[]).map(x => [x.id, x.purpose === 'Shifted']), [['qa', false], ['spec', false], ['build', true]]);
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    assert.equal(await page.locator('#pe-dialog .pe-clash').count(), 0, 'no banner');
    // ...and removed it again: Build is back at its old place, still quietly.
    pipeline.stages = pipeline.stages.filter(x => x.id !== 'qa');
    pipeline.settings_version += 1;
    await purpose.fill('Back');
    before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 150 && saves.length < before + 2; i++) await page.waitForTimeout(20);
    assert.deepEqual((saves.at(-1)!['stages'] as {id: string; purpose: string}[]).map(x => [x.id, x.purpose === 'Back']), [['spec', false], ['build', true]]);
    await page.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));

    // Saves land before each try four times running: three quiet tries, then the banner (no endless retrying), and Save
    // stays off while it is tried.
    refuseNext = 4;
    before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    assert.equal(await page.locator('#pe-dialog [data-pe-save]').isDisabled(), true, 'Save is off while it is tried');
    await page.locator('#pe-dialog .pe-clash').waitFor();
    assert.equal(saves.length, before + 4, 'four tries, then the banner');
    refuseNext = 0;
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});

    // The editor closed while a refused save's re-read is out: nothing is saved after it is gone.
    await oneStage('build');
    let release = () => {};
    readGate = new Promise<void>(r => { release = r; });
    refuseNext = 1;
    await purpose.fill('Gone before it saved');
    before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    for (let i = 0; i < 100 && saves.length < before + 1; i++) await page.waitForTimeout(20);
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});
    release(); readGate = null;
    await page.waitForTimeout(400);
    assert.equal(saves.length, before + 1, 'only the refused save; none after the editor closed');
    await oneStage('build');

    // They changed Build itself: that is a clash, with the banner; Save mine sends their record with this Build in it.
    pipeline.stages[1]!.purpose = 'Their purpose';
    pipeline.settings_version += 1;
    await purpose.fill('Mine, again');
    before = saves.length;
    await page.locator('#pe-dialog [data-pe-save]').click();
    await page.locator('#pe-dialog .pe-clash [data-pe-mine]').waitFor();
    assert.match(await page.locator('#pe-dialog .pe-clash p').innerText(), /They changed: Build/);
    assert.equal(saves.length, before + 1, 'the first save was refused');
    await page.locator('#pe-dialog [data-pe-mine]').click();
    for (let i = 0; i < 100 && saves.length < before + 2; i++) await page.waitForTimeout(20);
    const mine = saves.at(-1)!;
    assert.equal(mine['goal'], 'Theirs', 'their Goal is kept');
    assert.equal((mine['stages'] as {purpose: string}[])[1]!.purpose, 'Mine, again', 'this person chose theirs over Build');
    await page.locator('#pe-dialog .pe-clash').waitFor({state: 'detached'});
    await page.locator('#pe-dialog [data-pe-close]').click();
    await page.locator('#pe-dialog').waitFor({state: 'detached'});

    // Load theirs on one stage: they renamed it, so the title follows and the stage stays open; they removed it, so the
    // editor becomes the whole pipeline's, titled with its name.
    await oneStage('build');
    pipeline.stages[1]!.name = 'Building';
    pipeline.settings_version += 1;
    await purpose.fill('Mine again');
    await page.locator('#pe-dialog [data-pe-save]').click();
    await page.locator('#pe-dialog [data-pe-theirs]').click();
    await page.locator('#pe-dialog .pe-stage-one').waitFor();
    assert.equal(await page.locator('#pe-title').innerText(), 'Product › Building');
    assert.equal(await page.locator('#pe-dialog [data-pe-sf="name"]').inputValue(), 'Building');
    pipeline.stages = pipeline.stages.slice(0, 1);
    pipeline.settings_version += 1;
    await purpose.fill('Gone?');
    await page.locator('#pe-dialog [data-pe-save]').click();
    await page.locator('#pe-dialog [data-pe-theirs]').click();
    await page.locator('#pe-dialog [data-pe-pf="goal"]').waitFor();
    assert.equal(await page.locator('#pe-title').innerText(), 'Product');
    assert.deepEqual(await page.locator('#pe-dialog .pe-stage-name').allInnerTexts(), ['Spec']);
    await page.locator('#pe-dialog [data-pe-close]').click();

    assert.deepEqual(errors, []);
    await page.close();

    await scrollKept(browser);
    console.log('PASS: a dragged task moves, a refused one says why, with a why to push past only where it may be; the columns stay scrolled where they were.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
