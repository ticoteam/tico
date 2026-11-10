// The Tasks page (docs/pipelines-v3.md S2, S7), offline, and the fake server the other task scripts open the app with.
// Every request is answered from the fixtures below, in the shapes backend/openapi_v2.py gives them (ui/types/api.d.ts):
// GET /api/v2/pipelines, /needs-you (NeedsYou), /tasks (TaskBrief rows), /tasks/{id} (TaskDetail), and the writes on a
// task (edit with its version, done, move, owner, snooze, an ask's answer, a comment). Checks here:
//  - You: Needs you under Answer, Review and Do, each row the sentence asked; a question with two choices answers in
//    place and leaves (not on a phone); search narrows the rows, and Escape clears it; nothing scrolls sideways on a phone;
//  - the view button lists You and every pipeline still in use (a retired one is not offered); picking one puts it in
//    the address and reads its open tasks, snoozed ones left out;
//  - the archive: Snoozed, Done and Closed each read their own tasks, and a row opens the task in its window; a
//    pipeline's archive reads and keeps that pipeline's tasks only, a live task of another pipeline left out.
// Pipelines themselves (list and columns, the rail, the editor) are pipeline-view-unit.cts and pipeline-drag.cts; the
// ask screen is ask-screen.cts.
import type {Browser, Page, Route} from 'playwright';
const {chromium} = require('playwright') as typeof import('playwright');
const assert: typeof import('node:assert/strict') = require('node:assert/strict');
const fs = require('node:fs') as typeof import('node:fs');
const {html, uiFile} = require('./support/page.cts') as import('./support/page.cts').Api;
const {unrouted} = require('./support/routes.cts') as import('./support/routes.cts').Api;

type Wait = import('../types/api').TaskWait;
type Requirement = import('../types/api').TaskRequirement;
interface Stage {id: string; name: string; kind: 'work' | 'done' | 'closed'; by_hand: boolean}
// A task as GET /api/v2/tasks/{id} sends it (Task): the fields the scripts read are typed, the rest stay unknown.
export interface Task {
  id: string;
  title: string;
  body?: string;
  status?: 'open' | 'done' | 'closed';
  number?: number | null;
  pipeline_id?: string | null;
  pipeline?: {id: string; name: string} | null;
  stage_id?: string | null;
  stage?: Stage | null;
  owner_id?: string | null;
  requester_id?: string | null;
  assignee_id?: string | null;
  due_at?: string | null;
  late?: {level: 'told' | 'person'; since: string} | null;
  snoozed?: boolean;
  snooze?: {by: string; until: string | null; why: string | null} | null;
  turn?: Wait | null;
  labels?: string[];
  links?: unknown[];
  private?: boolean;
  readers?: string[];
  relations?: Record<string, unknown[]>;
  created_at?: string;
  updated_at?: string;
  version: number;
  [field: string]: unknown;
}
// One item of Needs you (NeedsYouItem).
export interface Need {
  id: string;
  kind: 'task' | 'ask' | 'paused' | 'mention';
  line: string;
  since: string | null;
  task_id?: string | null;
  requirement_id?: string | null;
  task?: ReturnType<typeof brief> | null;
  ask?: Requirement | null;
  [field: string]: unknown;
}
// A write the fake server saw: its path and JSON body (the fields the scripts read are typed).
export interface Posted {
  p: string;
  body: {version?: number; to?: string; owner?: string; value?: string; text?: string; until?: string; why?: string; [field: string]: unknown};
}
export interface OpenOptions {
  viewport?: {width: number; height: number};
  theme?: string;
  /** Where the app opens: '#/tasks' (the page) or '#/task/<id>' (one task's window); the open waits for either. */
  hash?: string;
  mover?: boolean;
  tasks?: Task[];
  needs?: Need[];
  /** A task's comments, by task id, as GET /api/v2/tasks/{id} sends them. */
  comments?: Record<string, unknown[]>;
  bots?: unknown[];
  beforeGoto?: ((page: Page) => unknown) | null;
}

// The app's classic-script globals, read inside page.evaluate. A script declares the ones it reads itself
// (`declare var TASKS_ST: Tasks.Globals['TASKS_ST'];`); their types live here, since several scripts read the same ones.
// Each is typed by the fields a script reads; a function the app defines takes its arguments loosely (unknown).
interface TaskLiveDraft {
  base: Task;
  patch: Record<string, unknown>;
  flight?: unknown;
  error?: string | null;
  timer?: number;
}
export interface TaskLive {
  id: string;
  identity?: string;
  owner?: string;
  shared?: boolean;
  reading?: boolean;
  draft: TaskLiveDraft;
  fields: Record<string, {version: number}>;
}
// The task window: an element with the app's expando properties on it.
export interface TaskModalElement extends HTMLDialogElement {
  drawSeq?: number;
  liveTask?: Task;
  taskRail?: unknown;
  taskLive?: TaskLive;
  saving?: boolean;
  close(): void;
}
// The Tasks page's state (ui/app/tasks-page.ts TasksPageState).
export interface TasksState {
  pipeline: string;
  layout: string;
  archive: string;
  q: string;
  needs: Need[];
  tasks: Task[];
  loading: boolean;
  [field: string]: unknown;
}
export interface Globals {
  TASKS_ST: TasksState;
  tasksLoad: (state: unknown) => Promise<unknown>;
  v2Refresh: () => Promise<unknown>;
  v2MessageHTML: (message: unknown) => string;
  taskSaveQueued: (...args: unknown[]) => unknown;
  taskModal: () => TaskModalElement;
  taskModalShow: (task: unknown, ...rest: unknown[]) => Promise<unknown>;
  taskLiveStop: (...args: unknown[]) => unknown;
  taskLiveStorageKey: (...args: unknown[]) => string;
  TASK_EDIT_DRAFTS: Map<string, TaskLiveDraft>;
  TASK_EDIT_STORAGE: string;
  TASK_MODAL_BACK: boolean;
  S: {
    me: {id: string; mover?: boolean; role?: string};
    route: string;
    config?: {environment_id?: string};
    [field: string]: unknown;
  };
  SESSION: {out: boolean; seen: boolean; identity: string; [field: string]: unknown};
  SESSION_RETURN: string;
  sessionKeep: () => void;
  sessionReturn: () => void;
  signedOut: (...args: unknown[]) => unknown;
  get: (path: string, opts?: unknown) => Promise<unknown>;
}
// Expandos a script sets on window for the page to read back.
export interface WindowMarks {
  taskSources?: (EventTarget & {closed?: boolean; url?: string})[];
  taskSafety?: (() => void)[];
  propertySave?: unknown;
  oldView?: TaskLive | undefined;
  leavingLive?: TaskLive | undefined;
}
declare var TASKS_ST: Globals['TASKS_ST'];
declare function tasksLiveApply(events: {id: string; task: unknown}[]): void;

const NOW = Date.now();
const at = (minutes: number) => new Date(NOW - minutes * 60000).toISOString();
const stage = (id: string, name: string, kind: Stage['kind'] = 'work'): Stage => ({id, name, kind, by_hand: false});
const DONE = stage('done', 'Done', 'done'), CLOSED = stage('closed', 'Closed', 'closed');
const pipelineStage = (id: string, name: string) => ({id, name, purpose: '', owner: null, by_hand: false, task_limit: null, expected_minutes: null,
  expected_bots_only: false, on_enter: {}, requirements: []});
// GET /api/v2/pipelines (backend/pipeline_settings.read): two in use, one retired.
const pipeline = (id: string, name: string, stages: [string, string][], retired = false) => ({id, name, goal: '', prefix: name[0], manager_id: null,
  owner_id: 'human:ana', private: false, factory_id: null, preset: null, settings_version: 1, retired_at: retired ? at(60 * 24) : null,
  stages: stages.map(([sid, sname]) => pipelineStage(sid, sname)), done: {id: 'done', name: 'Done'}, closed: {id: 'closed', name: 'Closed'}, may_change: true});
const PIPELINES = [pipeline('product', 'Product', [['spec', 'Spec'], ['build', 'Build'], ['review', 'Review']]),
  pipeline('ops', 'Operations', [['todo', 'To do'], ['doing', 'Doing']]), pipeline('old', 'Old roadmap', [['idea', 'Idea']], true)];
const STAGES = new Map(PIPELINES.flatMap(p => p.stages.map(s => [s.id, {pipeline: p, stage: stage(s.id, s.name)}] as const)));

const turn = (state: Wait['state'], who: string | null, line: string, more: Partial<Wait> = {}): Wait =>
  ({state, who, line, since: at(60), until: null, ask: false, requirement_id: null, ...more});
/** A task in a stage ('done' and 'closed' finish it), as GET /api/v2/tasks/{id} sends it. */
function task(id: string, title: string, at_: string, over: Partial<Task> = {}): Task {
  const where = STAGES.get(at_), finished = at_ === 'done' || at_ === 'closed';
  return {id, title, body: 'Details.', number: Number(id.replace(/\D/g, '')) || null, status: finished ? at_ as 'done' | 'closed' : 'open',
    pipeline_id: where?.pipeline.id ?? 'product', pipeline: where ? {id: where.pipeline.id, name: where.pipeline.name} : {id: 'product', name: 'Product'},
    stage_id: where?.stage.id ?? at_, stage: where?.stage ?? (at_ === 'done' ? DONE : CLOSED), owner_id: 'bot:pm', requester_id: 'human:ana',
    assignee_id: null, due_at: null, late: null, snoozed: false, snooze: null, turn: finished ? null : turn('bot', 'bot:pm', 'PM is on it'),
    labels: [], lane: 'company', links: [], private: false, created_at: at(60 * 30), updated_at: at(30), version: 3, ...over};
}
/** A list row (TaskBrief): what GET /api/v2/tasks?brief=1 sends. */
function brief(t: Task) {
  return {id: t.id, title: t.title, number: t.number ?? null, status: t.status ?? 'open', pipeline_id: t.pipeline_id ?? null, stage_id: t.stage_id ?? null,
    owner_id: t.owner_id ?? null, requester_id: t.requester_id ?? null, assignee_id: t.assignee_id ?? null, due_at: t.due_at ?? null,
    late: t.late ?? null, snoozed: !!t.snoozed, turn: t.turn ?? null, version: t.version};
}
const ask = (rid: string, kind: 'ok' | 'answer', line: string, choices: string[] | null = null): Requirement => ({requirement_id: rid, kind, line,
  who: 'human:ana', asked_by: 'bot:pm', choices, may: kind === 'ok' ? ['ok', 'no', 'thumbs'] : ['answer', 'thumbs'], met: false, shows: [],
  standing: 'waiting', answer: null});
function fixtures(): Task[] {
  return [
    task('t-1', 'Checkout flow', 'spec', {turn: turn('person', 'human:ana', 'Waiting on Ana', {ask: true, requirement_id: 'r1'})}),
    task('t-2', 'Signup plans', 'spec', {turn: turn('person', 'human:ana', 'Waiting on Ana', {ask: true, requirement_id: 'r2'})}),
    task('t-3', 'Book the team offsite', 'todo', {owner_id: 'human:ana', requester_id: 'human:sam', turn: turn('person', 'human:ana', 'Waiting on Ana')}),
    task('t-4', 'Rounding in the API', 'build', {owner_id: 'bot:eng'}),
    task('t-5', 'Renew the domain', 'todo', {owner_id: 'bot:ops', snoozed: true, snooze: {by: 'human:ana', until: new Date(NOW + 3 * 86400e3).toISOString(), why: null},
      turn: turn('snoozed', null, 'Snoozed', {until: new Date(NOW + 3 * 86400e3).toISOString()})}),
    task('t-6', 'Ship the receipt email', 'done', {updated_at: at(240)}),
    task('t-7', 'Old pricing test', 'closed', {updated_at: at(60 * 70)}),
  ];
}
/** Needs you for these tasks: an OK, a question with two choices, and a task Ana owns. */
function needsOf(tasks: Task[]): Need[] {
  const of = (id: string) => brief(tasks.find(t => t.id === id)!);
  return [
    {kind: 'ask', id: 'r1', task_id: 't-1', requirement_id: 'r1', line: 'Approve the spec for Checkout flow', since: at(90), task: of('t-1'),
      ask: ask('r1', 'ok', 'Approve the spec for Checkout flow')},
    {kind: 'ask', id: 'r2', task_id: 't-2', requirement_id: 'r2', line: 'Which plan do new signups get?', since: at(60), task: of('t-2'),
      ask: ask('r2', 'answer', 'Which plan do new signups get?', ['Free', 'Pro trial'])},
    {kind: 'task', id: 'task:t-3', task_id: 't-3', line: 'Book the team offsite', since: at(300), task: of('t-3')},
  ];
}
const BOTS = [['pm', 'PM'], ['eng', 'Engineer'], ['ops', 'Ops'], ['writer', 'Writer']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true}));
const CONFIG = {version: '0.6.0', app_name: 'Tico', local: true};

async function open(browser: Browser, {viewport = {width: 1440, height: 900}, theme = 'dark', hash = '#/tasks', mover = true, tasks: given,
  needs: givenNeeds, comments = {}, bots = BOTS, beforeGoto = null}: OpenOptions = {}) {
  const tasks = given ?? fixtures();
  let needs = givenNeeds ?? needsOf(tasks);
  const posts: Posted[] = [], reads: URL[] = [];
  const page = await browser.newPage({viewport, hasTouch: viewport.width <= 390, serviceWorkers: 'block'});
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(process.env.TASKS_DEBUG ? e.stack! : e.message));
  await page.addInitScript(t => { try { localStorage.setItem('tico.theme', t); } catch {} }, theme);
  const me = {id: 'ana', name: 'Ana', email: 'ana@acme.example', role: mover ? 'owner' : 'member', mover, cloud: true, registered: true};
  const find = (id: string) => tasks.find(x => x.id === id);
  const moved = (t: Task, fields: Partial<Task>) => { Object.assign(t, fields, {version: t.version + 1, updated_at: new Date().toISOString()}); return {task: t}; };
  await page.route('**/*', async (route: Route) => {
    const req = route.request(), url = new URL(req.url()), p = url.pathname, method = req.method();
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (url.origin !== 'https://tico-ui.test') return route.abort();
    const ui = p.match(/\/tico\/ui\/((?:app\/|styles\/)?[^/]+\.(?:js|ts|css))$/);
    if (ui && fs.existsSync(uiFile(ui[1]!)))
      return route.fulfill({contentType: ui[1]!.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(uiFile(ui[1]!), 'utf8')});
    if (p.startsWith('/vendor/fonts/') && fs.existsSync(uiFile(p.slice(1)))) return route.fulfill({contentType: 'font/woff2', body: fs.readFileSync(uiFile(p.slice(1)))});
    if (p === '/vendor/marked.min.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(uiFile('vendor/marked.min.js'), 'utf8')});
    if (/\.(?:js|ts)$/.test(p)) return route.fulfill({contentType: 'application/javascript', body: ''});
    if (p === '/') return route.fulfill({contentType: 'text/html', body: html});
    if (p === '/api/me') return json({...me, config: CONFIG});
    if (p === '/api/v2/config') return json(CONFIG);
    if (p === '/api/employees') return json(bots);
    if (p === '/api/humans' || p === '/api/people') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    const onePipeline = p.match(/^\/api\/v2\/pipelines\/([^/]+)$/);
    if (onePipeline && method === 'GET') return json(PIPELINES.find(x => x.id === onePipeline[1]) || {});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    if (p === '/api/v2/tasks' && method === 'GET') {
      reads.push(url);
      const q = url.searchParams, status = q.get('status'), snoozed = q.get('snoozed') || 'hide', ids = q.get('ids')?.split(',');
      const rows = tasks.filter(x => (!status || x.status === status) && (!ids || ids.includes(x.id))
        && (ids || snoozed === 'all' || (snoozed === 'only' ? !!x.snoozed : !x.snoozed)) && (!q.get('pipeline') || x.pipeline_id === q.get('pipeline')));
      return json({tasks: q.get('brief') ? rows.map(brief) : rows, next_offset: null, cursor: 'c1'});
    }
    if (p === '/api/v2/tasks' && method === 'POST') {
      const body = req.postDataJSON() as Posted['body']; posts.push({p, body});
      const made = task('t-' + (tasks.length + 1), String(body['title'] || ''), 'spec', {owner_id: body.owner || null});
      tasks.push(made);
      return json({task: made});
    }
    const sub = p.match(/^\/api\/v2\/tasks\/([^/]+)\/(done|move|owner|snooze|unsnooze|comments|asks\/([^/]+)\/answer)$/);
    if (sub && method === 'POST') {
      const t = find(decodeURIComponent(sub[1]!)), body = (req.postDataJSON() || {}) as Posted['body'];
      posts.push({p, body});
      if (!t) return json({error: 'not_found', detail: 'Not found'}, 404);
      if (body.version != null && body.version !== t.version)
        return json({error: 'task_changed', detail: 'This task changed. Look again.', missing: [], next: [], version: t.version}, 409);
      const what = sub[2]!;
      if (what === 'done') return json(moved(t, {status: 'done', stage_id: 'done', stage: DONE, turn: null}));
      if (what === 'move') {
        if (body.to === 'closed') return json(moved(t, {status: 'closed', stage_id: 'closed', stage: CLOSED, turn: null}));
        if (body.to === 'reopen') return json(moved(t, {status: 'open', stage_id: 'spec', stage: STAGES.get('spec')!.stage}));
        const where = STAGES.get(String(body.to));
        return where ? json(moved(t, {stage_id: where.stage.id, stage: where.stage})) : json({error: 'not_found', detail: 'No such stage'}, 404);
      }
      if (what === 'owner') { const who = String(body.owner); return json(moved(t, {owner_id: who.includes(':') ? who : 'bot:' + who})); }
      if (what === 'snooze') return json(moved(t, {snoozed: true, snooze: {by: 'human:ana', until: body.until ?? null, why: body.why ?? null}}));
      if (what === 'unsnooze') return json(moved(t, {snoozed: false, snooze: null}));
      if (what === 'comments') {
        const list = (comments[t.id] ||= []);
        list.push({id: 'm' + (list.length + 1), kind: 'say', sender_id: 'human:ana', body: body.text, created_at: new Date().toISOString(), refs: {task: t.id, comment: true}});
        return json({comment: list.at(-1), comments: list, woke: true});
      }
      const rid = decodeURIComponent(sub[3]!);
      needs = needs.filter(n => n.requirement_id !== rid);
      return json({task: moved(t, {turn: turn('bot', t.owner_id || null, 'PM is on it')}).task, answer_id: 'a-' + rid, requirement_id: rid});
    }
    const tree = p.match(/^\/api\/v2\/tasks\/([^/]+)\/tree$/);
    if (tree) return json([]);
    const files = p.match(/^\/api\/v2\/tasks\/([^/]+)\/files$/);
    if (files) return json({files: []});
    const one = p.match(/^\/api\/v2\/tasks\/([^/]+)$/);
    if (one) {
      const t = find(decodeURIComponent(one[1]!));
      if (!t) return json({error: 'not_found', detail: 'Not found'}, 404);
      if (method === 'POST') {
        const body = req.postDataJSON() as Posted['body']; posts.push({p, body});
        if (body.version != null && body.version !== t.version)
          return json({error: 'task_changed', detail: 'This task changed. Look again.', missing: [], next: [], version: t.version}, 409);
        const {version, ...fields} = body;
        return json(moved(t, fields));
      }
      // TaskDetail: the task with its history, comments and messages.
      return json({task: t, children: [], comments: comments[t.id] || [], events: [], messages: [], has_more: false, mover});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    if (p.endsWith('/messages')) return json({messages: []});
    return json(unrouted(p));
  });
  await beforeGoto?.(page);
  await page.goto('https://tico-ui.test/' + hash);
  const ready = hash.startsWith('#/task/')
    ? page.locator('#task-modal[open] .tmodal-title').waitFor()
    : hash.startsWith('#/tasks')
      ? page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && document.querySelector('#task-body .tk-row, #task-body .empty, #task-body .pv'))
      : Promise.resolve();
  await ready.catch((e: Error) => { throw new Error(`${e.message}\npage errors: ${errors.join(' | ')}`); });
  return {page, errors, posts, reads, tasks, needs: () => needs};
}

const rowTitles = (page: Page) => page.locator('#task-body .tk-row .tk-title').allInnerTexts();
const groups = (page: Page) => page.locator('#task-body .tk-group').evaluateAll(els => els.map(e => e.firstChild?.textContent?.trim()));
const sideways = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - innerWidth);

async function you(browser: Browser) {
  for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
    const {page, errors, posts} = await open(browser, {viewport});
    assert.deepEqual(await groups(page), ['Answer', 'Review', 'Do']);
    assert.deepEqual(await rowTitles(page), ['Which plan do new signups get?', 'Approve the spec for Checkout flow', 'Book the team offsite']);
    assert.match(await page.locator('#task-scope').innerText(), /You\s*3/);
    assert.ok(await sideways(page) <= 1, `nothing scrolls sideways at ${viewport.width}px`);
    // Search narrows the rows; Escape clears it.
    await page.locator('#task-q').fill('offsite');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-row').length === 1);
    assert.deepEqual(await rowTitles(page), ['Book the team offsite']);
    await page.locator('#task-q').fill('nothing like this');
    await page.locator('#task-body .empty', {hasText: 'No matches'}).waitFor();
    await page.locator('#task-q').press('Escape');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-row').length === 3);
    // A question with two choices answers in place, with the task's version, and leaves the list (a phone opens it instead).
    if (viewport.width < 640) { assert.equal(await page.locator('[data-need-answer]:visible').count(), 0); await page.close(); continue; }
    await page.locator('[data-need="r2"] [data-need-answer="Pro trial"]').click();
    await page.locator('[data-need="r2"]').waitFor({state: 'detached'});
    assert.deepEqual(posts, [{p: '/api/v2/tasks/t-2/asks/r2/answer', body: {value: 'Pro trial', version: 3}}]);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('You: Answer, Review and Do; search; a two-choice answer in place: ok');
}

async function views(browser: Browser) {
  const {page, errors, reads} = await open(browser);
  await page.locator('#task-scope').click();
  const menu = page.locator('#task-filter-pop [data-scope]');
  await menu.first().waitFor();
  assert.deepEqual(await menu.evaluateAll(els => els.map(e => e.querySelector('.tl-mi-t')?.textContent)), ['You', 'Product', 'Operations'], 'a retired pipeline is not offered');
  await page.locator('#task-filter-pop [data-scope="ops"]').click();
  await page.waitForFunction(() => /pipeline=ops/.test(location.hash) && !TASKS_ST.loading);
  const read = reads.at(-1)!.searchParams;
  assert.deepEqual([read.get('status'), read.get('snoozed')], ['open', 'hide'], 'a pipeline reads its open tasks, snoozed ones left out');
  assert.equal((await page.locator('#task-scope').innerText()).trim(), 'Operations');
  await page.locator('#task-body', {hasText: 'Book the team offsite'}).waitFor();
  assert.equal(await page.locator('#task-body', {hasText: 'Renew the domain'}).count(), 0);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('the view button: You and the pipelines in use; a pipeline in the address: ok');
}

async function archive(browser: Browser) {
  const {page, errors, reads} = await open(browser);
  const shows = async (which: string, want: Record<string, string>, titles: string[]) => {
    await page.waitForFunction(w => TASKS_ST.archive === w && !TASKS_ST.loading, which);
    const read = reads.at(-1)!.searchParams;
    assert.deepEqual(Object.fromEntries(Object.keys(want).map(k => [k, read.get(k)])), want, `${which} reads its own tasks`);
    assert.deepEqual(await rowTitles(page), titles);
    assert.equal(await page.locator(`#task-archives [data-archive="${which}"]`).getAttribute('aria-pressed'), 'true');
  };
  await page.locator('#task-archive').click();
  await shows('snoozed', {status: 'open', snoozed: 'only'}, ['Renew the domain']);
  await page.locator('#task-archives [data-archive="done"]').click();
  await shows('done', {status: 'done', sort: 'finished'}, ['Ship the receipt email']);
  await page.locator('#task-archives [data-archive="closed"]').click();
  await shows('closed', {status: 'closed', sort: 'finished'}, ['Old pricing test']);
  // A row opens the task in its window.
  await page.locator('#task-body [data-task="t-7"]').click();
  await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Old pricing test'}).waitFor();
  await page.keyboard.press('Escape');
  await page.locator('#task-archive').click();
  await page.waitForFunction(() => !TASKS_ST.archive && !TASKS_ST.loading);
  assert.deepEqual(await groups(page), ['Answer', 'Review', 'Do']);
  assert.deepEqual(errors, []);
  await page.close();
  console.log('the archive: Snoozed, Done and Closed each read their own; a row opens its task: ok');
}

async function pipelineArchive(browser: Browser) {
  const tasks = [...fixtures(), task('t-8', 'Close the office lease', 'done', {pipeline_id: 'ops', pipeline: {id: 'ops', name: 'Operations'}})];
  const {page, errors, reads} = await open(browser, {tasks, hash: '#/tasks?pipeline=product&archive=done'});
  await page.waitForFunction(() => TASKS_ST.archive === 'done' && !TASKS_ST.loading);
  assert.equal(reads.at(-1)!.searchParams.get('pipeline'), 'product', "a pipeline's archive reads that pipeline's tasks");
  assert.deepEqual(await rowTitles(page), ['Ship the receipt email']);
  // A task of another pipeline is done: it is not this archive's.
  await page.evaluate(t => tasksLiveApply([{id: t.id, task: t}]), brief(tasks.at(-1)!));
  await page.evaluate(() => new Promise(requestAnimationFrame));
  assert.deepEqual(await rowTitles(page), ['Ship the receipt email']);
  assert.deepEqual(errors, []);
  await page.close();
  console.log("a pipeline's archive: that pipeline's tasks only, read and live: ok");
}

const api = {open, fixtures, task, brief, needsOf, PIPELINES};
export type Api = typeof api;
export type Opened = Awaited<ReturnType<typeof open>>;
module.exports = api;
if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const run of [you, views, archive, pipelineArchive]) await run(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
