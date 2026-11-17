import type {Browser, Page, Route} from 'playwright';
// Offline browser check of side stages (a stage off the main path, like Rejected off Bot QA): on the board its column sits
// right after its stage, outlined in dashes with no fill and ↳ before its quieter name; a phone's tab for it is dashed with
// ↳; the list's group head has ↳. A drop into or out of it is a plain move (its stage's id, never `next`), and a move on
// from the stage before it skips it (`next`). The task window says "↳ Rejected · from Bot QA · 2h", and Send back moves
// it to the stage it came from. In the editor every stage but the first has a Side switch; on, the row is indented with
// ↳ and Limit, Late after and the requirements go (the save says by hand, nothing else to leave); off keeps by hand.
// TICO_UI_SHOTS=<dir> keeps screenshots.
const {chromium} = require('playwright') as typeof import('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Rec = Record<string, unknown>;
const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: 'owner', mover: true, cloud: true};
const config = {version: '0.1.0', app_name: 'Tico', local: true};
const NOW = Date.now();
const at = (minutes: number) => new Date(NOW - minutes * 60000).toISOString();
const done = {id: 'q0', kind: 'owner_done', source: null, condition: null, settings: {}, can_push_past: true, who_oks: 'none', person_id: null, line: ''};
const stage = (id: string, name: string, extra: Rec = {}) => ({id, name, purpose: '', owner: null, by_hand: false, requests_move_on: false,
  task_limit: null, expected_minutes: null, expected_bots_only: false, side: false, on_enter: {}, requirements: [{...done, id: `q-${id}`}], ...extra});
const STAGES = () => [stage('build', 'In progress'), stage('qa', 'Bot QA'),
  stage('rejected', 'Rejected', {side: true, by_hand: true, requirements: []}),
  stage('ship', 'Ship', {task_limit: 4, expected_minutes: 120})];
const pipelineRec = (): Rec & {stages: Rec[]; settings_version: number} => ({id: 'product', name: 'Product', goal: '', prefix: 'P', manager_id: null,
  owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: null, stages: STAGES(),
  done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true});

// A task as the fake server keeps it. A list row (TaskBrief) carries stage_since, and came_from only when set; the full
// read (TaskDetail) has no stage_since, and came_from always (null or {id, name}).
interface Task {
  id: string; title: string; number: number; stage_id: string; version: number; stage_since: string;
  came_from: {id: string; name: string} | null;
}
const task = (id: string, title: string, stageId: string, extra: Partial<Task> = {}): Task =>
  ({id, title, number: Number(id.slice(1)), stage_id: stageId, version: 1, stage_since: at(30), came_from: null, ...extra});
const row = (t: Task) => ({id: t.id, title: t.title, number: t.number, status: 'open', pipeline_id: 'product', stage_id: t.stage_id,
  stage_since: t.stage_since, owner_id: 'human:ana', requester_id: 'human:ana', assignee_id: null, due_at: null, late: null, snoozed: false,
  turn: null, version: t.version, created_at: at(60 * 24), updated_at: at(30), ...(t.came_from ? {came_from: t.came_from} : {})});
function full(t: Task, rec: {stages: Rec[]}) {
  const {stage_since: _since, ...brief} = row(t);
  const s = rec.stages.find(x => x['id'] === t.stage_id)!;
  return {...brief, came_from: t.came_from, pipeline: {id: 'product', name: 'Product'}, stage: {id: s['id'], name: s['name'], kind: 'work', by_hand: !!s['by_hand']},
    snooze: null, labels: [], links: [], private: false, readers: [], relations: {}, conversation_id: 'c-' + t.id};
}

interface Fake { pipeline: ReturnType<typeof pipelineRec>; tasks: Task[]; moves: string[]; saves: {stages: Rec[]}[] }

async function open(browser: Browser, fake: Fake, viewport: {width: number; height: number}, hash: string, errors: string[]): Promise<Page> {
  const page = await browser.newPage({viewport, serviceWorkers: 'block'});
  page.on('pageerror', (error: Error) => errors.push(error.message));
  await page.route('**/*', (route: Route) => {
    const r = route.request(), url = new URL(r.url()), p = url.pathname, method = r.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'http://tico-ui.test') return route.abort();
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!))) return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p === '/api/me') return json({...me, config});
    if (p === '/api/v2/config') return json(config);
    if (p === '/api/employees') return json([]);
    if (p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p === '/api/v2/pipelines') return json({pipelines: [fake.pipeline]});
    // As backend/pipeline_settings.py save: the record as sent, one version on.
    if (p === '/api/v2/pipelines/product' && method === 'POST') {
      const body = JSON.parse(r.postData() || '{}') as {stages: Rec[]};
      fake.saves.push(body);
      const {settings_version: _seen, ...given} = body as Rec;
      Object.assign(fake.pipeline, JSON.parse(JSON.stringify(given)), {settings_version: fake.pipeline.settings_version + 1});
      return json({pipeline: fake.pipeline});
    }
    if (p === '/api/v2/pipelines/product') return json({pipeline: fake.pipeline});
    if (p === '/api/v2/factories') return json({factories: [], pipelines: []});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: 0, items: [], version: '0', next: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p === '/api/v2/tasks' && method === 'GET') return json({tasks: fake.tasks.map(row), cursor: 'c1', next_offset: null, next_after: null});
    // A move as backend/task_engine.py makes it: `next` is the next main stage (side stages are skipped); into a side
    // stage, the task keeps the stage it came from.
    const move = p.match(/^\/api\/v2\/tasks\/([^/]+)\/move$/);
    if (move && method === 'POST') {
      const t = fake.tasks.find(x => x.id === move[1])!, body = JSON.parse(r.postData() || '{}') as Rec;
      fake.moves.push(`${t.id}>${String(body['to'])}`);
      const stages = fake.pipeline.stages, from = stages.findIndex(s => s['id'] === t.stage_id);
      const to = body['to'] === 'next' ? stages.slice(from + 1).find(s => !s['side']) : stages.find(s => s['id'] === body['to']);
      if (!to) return json({error: 'not_found', detail: 'No such stage'}, 404);
      const was = stages[from]!;
      Object.assign(t, {stage_id: to['id'], version: t.version + 1, stage_since: new Date().toISOString(),
        came_from: to['side'] ? {id: String(was['id']), name: String(was['name'])} : null});
      return json({task: full(t, fake.pipeline)});
    }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    if (tree) return json([]);
    const files = p.match(/^\/api\/v2\/tasks\/([^/]+)\/files$/);
    if (files) return json({files: []});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one && method === 'GET') {
      const t = fake.tasks.find(x => x.id === decodeURIComponent(one[1]!));
      if (!t) return json({error: 'not_found', detail: 'Not found'}, 404);
      return json({task: full(t, fake.pipeline), children: [], comments: [], events: [], runs: [], messages: [], has_more: false, mover: true});
    }
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    return json(unrouted(p));
  });
  await page.goto('http://tico-ui.test/' + hash);
  return page;
}

/** Holds task `id` over the head of column `col` (a short column has no empty space under its cards), in steps as a
    person would, and lets go. */
async function drop(page: Page, id: string, col: string): Promise<void> {
  const boxes = await page.waitForFunction(([t, c]) => {
    const a = document.querySelector(`.tk-card[data-task="${t}"]`)?.getBoundingClientRect();
    const b = document.querySelector(`[data-pv-col="${c}"] > .pv-col-head`)?.getBoundingClientRect();
    return a?.width && b?.width ? {from: {x: a.x, y: a.y, height: a.height}, to: {x: b.x, y: b.y, width: b.width, height: b.height}} : null;
  }, [id, col]);
  const {from, to} = await boxes.jsonValue() as {from: {x: number; y: number; height: number}; to: {x: number; y: number; width: number; height: number}};
  await page.mouse.move(from.x + 20, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(from.x + 40, from.y + from.height / 2, {steps: 3});
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, {steps: 8});
  // The card is drawn where it went at once, and again when the engine answers: the next drop measures after that.
  const answered = page.waitForResponse(r => r.url().endsWith(`/api/v2/tasks/${id}/move`));
  await page.mouse.up();
  await answered;
  await page.locator(`[data-pv-col="${col}"] .tk-card[data-task="${id}"]`).waitFor();
  await page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
}

const shot = async (page: Page, name: string, selector?: string) => {
  const dir = process.env.TICO_UI_SHOTS;
  if (!dir) return;
  fs.mkdirSync(dir, {recursive: true});
  await (selector ? page.locator(selector) : page).screenshot({path: path.join(dir, `side-stages-${name}.png`)});
};

(async () => {
  const browser = await chromium.launch({headless: true, channel: (process.env.TICO_BROWSER_CHANNEL ?? 'chrome') as string});
  const errors: string[] = [];
  try {
    const fake: Fake = {pipeline: pipelineRec(), moves: [], saves: [], tasks: [
      task('t1', 'Refund rounding', 'build'), task('t2', 'Signup copy', 'qa'), task('t4', 'Receipt email', 'qa'),
      task('t3', 'Annual plans', 'rejected', {came_from: {id: 'qa', name: 'Bot QA'}}),
      task('t5', 'Checkout totals', 'rejected', {came_from: {id: 'qa', name: 'Bot QA'}, stage_since: at(121)})]};

    // 1. The board: the side column right after its stage, dashed, no fill, ↳ before its quieter name.
    const page = await open(browser, fake, {width: 1440, height: 900}, '#/tasks?pipeline=product&layout=columns', errors);
    await page.locator('.tk-card[data-task="t1"]').waitFor();
    assert.deepEqual(await page.locator('.pv-col').evaluateAll(cols => cols.map(c => `${c.getAttribute('data-pv-col')}${c.classList.contains('side') ? ':side' : ''}`)),
      ['build', 'qa', 'rejected:side', 'ship'], 'Rejected sits right after Bot QA, and is the only side column');
    const side = page.locator('.pv-col.side');
    const mark = side.locator('.side-mark');
    assert.equal(await mark.innerText(), '↳');
    assert.equal(await mark.getAttribute('title'), 'Side stage');
    const look = (sel: string) => page.locator(sel).first().evaluate(el => {
      const s = getComputedStyle(el), name = el.querySelector('.pv-stage-name');
      return {border: s.borderTopStyle, fill: s.backgroundColor, name: name ? getComputedStyle(name).color : ''};
    });
    const sideLook = await look('.pv-col.side'), mainLook = await look('.pv-col[data-pv-col="qa"]');
    assert.equal(sideLook.border, 'dashed', 'a side column is outlined in dashes');
    assert.equal(sideLook.fill, 'rgba(0, 0, 0, 0)', 'with no fill');
    assert.notEqual(mainLook.border, 'dashed', 'a main column is not');
    assert.notEqual(sideLook.name, mainLook.name, 'its name is quieter');
    assert.equal(await page.locator('.pv-col:not(.side) .side-mark').count(), 0, 'no ↳ on a main stage');
    await shot(page, 'board');

    // 2. The task window: "↳ Rejected · from Bot QA · 2h", and Send back moves it to Bot QA.
    // A board's card opens at its conversation: Details shows the task, and its Details tab the facts.
    await page.locator('.tk-card[data-task="t5"]').click();
    await page.locator('#task-modal[open]').waitFor();
    await page.waitForFunction(() => {
      const m = document.querySelector('#task-modal')!;
      m.querySelector<HTMLElement>('[data-thread-details]')?.click();
      m.querySelector<HTMLElement>('[data-task-tab="details"][aria-pressed="false"]')?.click();
      return !!m.querySelector<HTMLElement>('[data-prop-row="stage"]')?.checkVisibility();
    }, null, {polling: 250});
    const stageRow = page.locator('#task-modal[open] [data-prop-row="stage"]');
    assert.equal(await stageRow.locator('.prop-v').getAttribute('aria-label'), 'Stage: Rejected · from Bot QA · 2h');
    assert.equal(await stageRow.locator('.prop-v .side-mark').innerText(), '↳');
    assert.match((await stageRow.locator('.prop-v').innerText()).replace(/\s+/g, ' '), /↳ ?Rejected ?· from Bot QA · 2h/);
    await shot(page, 'task', '#task-modal [data-task-props]');
    await stageRow.getByRole('button', {name: 'Send back'}).click();
    await page.waitForFunction(() => !document.querySelector('#task-modal [data-prop-send-back]'));
    assert.deepEqual(fake.moves, ['t5>qa'], 'Send back is a plain move to the stage it came from');
    assert.equal(await stageRow.locator('.side-mark').count(), 0, 'back on the main path, the line is the usual one');
    assert.match(await stageRow.locator('.prop-v').getAttribute('aria-label') ?? '', /^Stage: Bot QA/);
    // A task in a main stage has no Send back.
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector<HTMLDialogElement>('#task-modal')?.open);

    // 3. Drops: into or out of a side stage, its own id; a move on from Bot QA skips Rejected (`next`); no why is asked.
    fake.moves.length = 0;
    await drop(page, 't1', 'qa');
    await drop(page, 't2', 'rejected');
    await drop(page, 't3', 'qa');
    await drop(page, 't4', 'ship');
    assert.deepEqual(fake.moves, ['t1>next', 't2>rejected', 't3>qa', 't4>next']);
    assert.equal(await page.locator('[data-pv-push]').count(), 0, 'no why bar');
    await page.close();

    // 4. The list: the side group's head has ↳.
    const list = await open(browser, fake, {width: 1440, height: 900}, '#/tasks?pipeline=product&layout=list', errors);
    await list.locator('.pv-group').first().waitFor();
    assert.deepEqual(await list.locator('.pv-group').evaluateAll(gs => gs.map(g => g.classList.contains('side'))), [false, false, true, false]);
    assert.equal(await list.locator('.pv-group.side .pv-group-head .side-mark').innerText(), '↳');
    await list.close();

    // 5. A phone: the side stage's tab is dashed with ↳, right after its stage's tab.
    const phone = await open(browser, fake, {width: 390, height: 844}, '#/tasks?pipeline=product', errors);
    await phone.locator('.pv-tab').first().waitFor();
    assert.deepEqual(await phone.locator('.pv-tab').evaluateAll(ts => ts.map(t => `${t.getAttribute('data-pv-tab')}${t.classList.contains('side') ? ':side' : ''}`)),
      ['build', 'qa', 'rejected:side', 'ship']);
    const tab = phone.locator('.pv-tab.side');
    assert.equal(await tab.locator('.side-mark').innerText(), '↳');
    assert.equal(await tab.evaluate(el => getComputedStyle(el).borderTopStyle), 'dashed');
    assert.notEqual(await phone.locator('.pv-tab[data-pv-tab="qa"]').evaluate(el => getComputedStyle(el).borderTopStyle), 'dashed');
    assert.ok(await phone.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'nothing scrolls sideways');
    await shot(phone, 'phone');
    await phone.close();

    // 6. The editor: no Side on the first stage; Rejected's is on, its row indented with ↳.
    const ed = await open(browser, fake, {width: 1200, height: 900}, '#/tasks?pipeline=product&layout=columns', errors);
    const dialog = ed.locator('#pe-dialog');
    await ed.locator('#task-menu').click();
    await ed.locator('#task-mi-edit').click();
    await dialog.locator('[data-pe-pf="goal"]').waitFor();
    const row_ = (si: number) => dialog.locator(`.pe-stage[data-pe-si="${si}"]`);
    const sw = (si: number) => row_(si).locator('> summary [data-pe-sf="side"]');
    assert.equal(await sw(0).count(), 0, 'the first stage has no Side');
    assert.ok(await sw(1).isVisible());
    assert.equal(await sw(1).isChecked(), false);
    assert.equal(await sw(2).isChecked(), true);
    assert.equal(await sw(2).getAttribute('role'), 'switch');
    assert.equal(await row_(2).locator('> summary .side-mark').innerText(), '↳');
    const indent = async (si: number) => row_(si).locator('> summary').evaluate(el => parseFloat(getComputedStyle(el).paddingLeft));
    assert.ok(await indent(2) > await indent(1), 'a side row is indented');

    // Ship: a Limit, Late after and a requirement, until Side goes on. A tap on the word turns it on, and the row stays shut.
    await row_(3).locator('> summary .pe-stage-name').click();
    await row_(3).locator('[data-pe-sf="expected_n"]').waitFor();
    assert.ok(await row_(3).locator('[data-pe-leave-row]').count() > 0, 'Ship has something to leave');
    await row_(3).locator('> summary .pe-stage-name').click();
    const word = (await row_(3).locator('> summary .pe-side').boundingBox())!;
    await ed.mouse.click(word.x + word.width - 4, word.y + word.height / 2);
    assert.equal(await sw(3).isChecked(), true);
    assert.equal(await row_(3).evaluate(d => (d as HTMLDetailsElement).open), false, 'the tap did not open the row');
    assert.ok(await row_(3).evaluate(d => d.classList.contains('side')));
    await row_(3).locator('> summary .pe-stage-name').click();
    await row_(3).locator('[data-pe-phase="leave"]').waitFor();
    assert.equal(await row_(3).locator('[data-pe-sf="expected_n"]').count(), 0, 'no Late after');
    assert.equal(await row_(3).locator('[data-pe-sf="task_limit"]').count(), 0, 'no Limit');
    assert.equal(await row_(3).locator('[data-pe-leave-row], [data-pe-req-add], [data-pe-sf="requests_move_on"]').count(), 0, 'nothing to leave');
    assert.equal(await row_(3).locator('.pe-stage-reqs').count(), 0, 'the row counts no requirements');
    await shot(ed, 'editor', '#pe-dialog');
    await dialog.locator('[data-pe-save]').click();
    await ed.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    assert.equal(await dialog.locator('[data-pe-error]:not([hidden])').count(), 0);
    const ship = fake.saves[0]!.stages[3]!;
    assert.deepEqual({side: ship['side'], by_hand: ship['by_hand'], requests_move_on: ship['requests_move_on'], task_limit: ship['task_limit'],
      expected_minutes: ship['expected_minutes'], requirements: ship['requirements']},
    {side: true, by_hand: true, requests_move_on: false, task_limit: null, expected_minutes: null, requirements: []});
    assert.deepEqual(fake.saves[0]!.stages.map(s => !!s['side']), [false, false, true, true], 'the others keep theirs');

    // Off again: by hand stays on.
    await sw(3).click();
    assert.equal(await sw(3).isChecked(), false);
    await dialog.locator('[data-pe-save]').click();
    await ed.waitForFunction(() => document.querySelector('#pe-dialog [data-pe-save]:not([disabled])'));
    const after = fake.saves[1]!.stages[3]!;
    assert.deepEqual({side: after['side'], by_hand: after['by_hand']}, {side: false, by_hand: true});
    await ed.close();

    assert.deepEqual(errors, []);
    console.log('PASS: side stages: the dashed column with ↳, plain moves, the phone tab, the list head, Send back, and the editor\'s Side switch.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
