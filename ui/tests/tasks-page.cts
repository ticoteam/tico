// The Tasks page (docs/dev/pipelines-v3.md S2, S7), offline, and the fake server the other task scripts open the app with.
// Every request is answered from the fixtures below, in the shapes backend/openapi_v2.py gives them (ui/types/api.d.ts):
// GET /api/v2/pipelines, /needs-you (NeedsYou), /tasks (TaskBrief rows), /tasks/{id} (TaskDetail), and the writes on a
// task (edit with its version, done, move, owner, snooze, an ask's answer, a comment). Checks here:
//  - You: Needs you under Answer, Review and Do, each row its task's title (an Answer row, a bot's ❓, the question under
//    it); search narrows the rows, and Escape clears it; nothing scrolls sideways on a phone;
//  - You: the person's own open tasks (nothing asked of them, so not in Needs you or its count) at the end of Do, kept live;
//  - the view button lists You and every pipeline still in use (a retired one is not offered); picking one puts it in
//    the address and reads its open tasks, snoozed ones too, drawn last in their stage and dimmed;
//  - the archive: Snoozed, Done and Closed each read their own tasks, and a row opens the task in its window; a
//    pipeline's archive reads and keeps that pipeline's tasks only, a live task of another pipeline left out;
//  - a pipeline over one page (500 open tasks) or an archive over 200: More reads the next page from where the rows held
//    end (a task taken out live moves it back by one); a search or a filter reads the rest first, so it finds them all;
//  - a search that is only a task number (556, #556, P556) puts that task first, from any pipeline and status.
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
  note?: string;
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
  kind: 'task' | 'ask' | 'paused' | 'mention' | 'question';
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
  /** A task's comments, by task id, as GET /api/v2/tasks/{id} sends them; also its chat's timeline (c-<id>), and a
      direct chat's messages by its conversation id (GET /api/v2/threads/{id}/timeline). */
  comments?: Record<string, unknown[]>;
  /** A task's history events, by task id, as GET /api/v2/tasks/{id} sends them. */
  events?: Record<string, unknown[]>;
  /** A task's bot runs, by task id, as GET /api/v2/tasks/{id} sends them (`runs`). */
  runs?: Record<string, unknown[]>;
  bots?: unknown[];
  beforeGoto?: ((page: Page) => unknown) | null;
  /** Answers held back by path until the test lets them go (a slow server, in the order the test sets). */
  slow?: Hold | null;
  /** false: return as soon as the page is asked for, before its first read lands. */
  settle?: boolean;
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
declare function tasksTaskPut(state: unknown, t: unknown): boolean;
declare function tasksLiveApply(events: {id: string; task?: unknown; gone?: boolean}[]): void;
/** The app's shared day words (ui/app/format.ts): "Today", "Tomorrow", "Nov 12". */
declare function dayWords(value: string | number | Date | null | undefined, opts?: {weekday?: boolean}): string;

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
  return {id, title, number: Number(id.replace(/\D/g, '')) || null, status: finished ? at_ as 'done' | 'closed' : 'open',
    pipeline_id: where?.pipeline.id ?? 'product', pipeline: where ? {id: where.pipeline.id, name: where.pipeline.name} : {id: 'product', name: 'Product'},
    stage_id: where?.stage.id ?? at_, stage: where?.stage ?? (at_ === 'done' ? DONE : CLOSED), owner_id: 'bot:pm', requester_id: 'human:ana',
    assignee_id: null, due_at: null, late: null, snoozed: false, snooze: null, turn: finished ? null : turn('bot', 'bot:pm', 'PM is on it'),
    labels: [], lane: 'company', links: [], private: false, created_at: at(60 * 30), updated_at: at(30), version: 3, conversation_id: 'c-' + id, ...over};
}
/** A list row (TaskBrief): what GET /api/v2/tasks?brief=1 sends. */
function brief(t: Task) {
  return {id: t.id, title: t.title, number: t.number ?? null, status: t.status ?? 'open', pipeline_id: t.pipeline_id ?? null, stage_id: t.stage_id ?? null,
    owner_id: t.owner_id ?? null, requester_id: t.requester_id ?? null, assignee_id: t.assignee_id ?? null, due_at: t.due_at ?? null,
    late: t.late ?? null, snoozed: !!t.snoozed, turn: t.turn ?? null, version: t.version, created_at: t.created_at, updated_at: t.updated_at,
    // a finished task's own time, as the server's list rows carry it
    ...(t['done_at'] ? {done_at: t['done_at']} : {}), ...(t['closed_at'] ? {closed_at: t['closed_at']} : {})};
}
const ask = (rid: string, line: string): Requirement => ({requirement_id: rid, kind: 'ok', line, who: 'human:ana', asked_by: 'bot:pm',
  may: ['ok', 'no'], met: false, shows: [], standing: 'waiting', answer: null});
function fixtures(): Task[] {
  return [
    task('t-1', 'Checkout flow', 'spec', {turn: turn('person', 'human:ana', 'Waiting on Ana', {ask: true, requirement_id: 'r1'})}),
    task('t-2', 'Signup plans', 'spec', {turn: turn('person', 'human:ana', 'Waiting on Ana', {ask: true, requirement_id: 'r2'})}),
    task('t-3', 'Book the team offsite', 'todo', {owner_id: 'human:ana', requester_id: 'human:sam', turn: turn('person', 'human:ana', 'Waiting on Ana')}),
    task('t-4', 'Rounding in the API', 'build', {owner_id: 'bot:eng'}),
    task('t-5', 'Renew the domain', 'todo', {owner_id: 'bot:ops', snoozed: true, snooze: {by: 'human:ana', until: new Date(NOW + 3 * 86400e3).toISOString(), why: null},
      turn: turn('snoozed', null, 'Snoozed', {until: new Date(NOW + 3 * 86400e3).toISOString()})}),
    // Finished at another time than last changed: a row's age is when it finished (done_at, closed_at).
    task('t-6', 'Ship the receipt email', 'done', {updated_at: at(10), done_at: at(240)}),
    task('t-7', 'Old pricing test', 'closed', {updated_at: at(5), closed_at: at(60 * 70)}),
  ];
}
/** Needs you for these tasks: an OK, a bot's ❓ in a task's chat (backend/question_marks.py), and a task Ana owns. */
function needsOf(tasks: Task[]): Need[] {
  const of = (id: string) => brief(tasks.find(t => t.id === id)!);
  return [
    {kind: 'ask', id: 'r1', task_id: 't-1', requirement_id: 'r1', line: 'Approve the spec for Checkout flow', since: at(90), task: of('t-1'),
      ask: ask('r1', 'Approve the spec for Checkout flow')},
    {kind: 'question', id: 'r2', mark_id: 'q2', task_id: 't-2', message_id: 'm-q2', conversation_id: 'c-t-2', root_id: 'c-t-2',
      from_id: 'bot:pm', requirement_id: null, line: 'Which plan do new signups get?', since: at(60), task: of('t-2')},
    {kind: 'task', id: 'task:t-3', task_id: 't-3', line: 'Book the team offsite', since: at(300), task: of('t-3')},
  ];
}
const BOTS = [['pm', 'PM'], ['eng', 'Engineer'], ['ops', 'Ops'], ['writer', 'Writer']].map(([name, display_name]) =>
  ({name, display_name, host: 'keeper', status: 'active', state: 'active', can_chat: true}));
const CONFIG = {version: '0.6.0', app_name: 'Tico', local: true};

/** A slow server whose order the test sets: a read on a held path waits until the test lets that path go. */
class Hold {
  private waiting = new Map<string, (() => void)[]>();
  private held: Set<string>;
  constructor(...paths: string[]) { this.held = new Set(paths); }
  pass(path: string): Promise<void> {
    if (!this.held.has(path)) return Promise.resolve();
    return new Promise<void>(go => { this.waiting.set(path, [...(this.waiting.get(path) ?? []), go]); });
  }
  /** How many reads of `path` are waiting. */
  asked(path: string): number { return this.waiting.get(path)?.length ?? 0; }
  hold(...paths: string[]) { for (const p of paths) this.held.add(p); }
  release(...paths: string[]) {
    for (const p of paths.length ? paths : [...this.held]) {
      this.held.delete(p);
      for (const go of this.waiting.get(p) ?? []) go();
      this.waiting.delete(p);
    }
  }
}

async function open(browser: Browser, {viewport = {width: 1440, height: 900}, theme = 'dark', hash = '#/tasks', mover = true, tasks: given,
  needs: givenNeeds, comments = {}, events = {}, runs = {}, bots = BOTS, beforeGoto = null, slow = null, settle = true}: OpenOptions = {}) {
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
    if (slow) await slow.pass(p);
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
    if (p === '/api/humans' || p === '/api/humans') return json({people: [{id: 'ana', name: 'Ana'}, {id: 'sam', name: 'Sam'}], teams: {}});
    if (p === '/api/status') return json({cloud: true, active: [], queued: [], recent_runs: [], keeper_alive: true, health_issues: []});
    if (p === '/api/v2/status') return json({bots: []});
    if (p.endsWith('/watch')) return route.fulfill({contentType: 'text/event-stream', body: ': fixture\n\n'});
    if (p === '/api/v2/pipelines') return json({pipelines: PIPELINES});
    const onePipeline = p.match(/^\/api\/v2\/pipelines\/([^/]+)$/);
    if (onePipeline && method === 'GET') return json({pipeline: PIPELINES.find(x => x.id === onePipeline[1]) || {}});
    if (p === '/api/v2/needs-you') return json({actor: 'human:ana', count: needs.length, items: needs, version: String(needs.length), next: null});
    if (p === '/api/v2/tasks/labels') return json({labels: [], tags: []});
    // A stage's OK: no grant, so Approve answers ok (decide.ts decideOkLoad).
    if (p.startsWith('/api/v2/oks/')) return json({error: 'not_found', detail: 'Not found'}, 404);
    if (p === '/api/v2/tasks' && method === 'GET') {
      reads.push(url);
      const q = url.searchParams, status = q.get('status'), snoozed = q.get('snoozed') || 'hide', ids = q.get('ids')?.split(',');
      // `q` as backend/hubdb.py tasks reads it: every word in the title.
      const words = (q.get('q') || '').toLowerCase().split(/\s+/).filter(Boolean), owner = q.get('owner');
      // `search` and `owners` as backend/task_match.py reads them: each word in the title, the number or a label.
      const said = (q.get('search') || '').toLowerCase().split(/\s+/).filter(Boolean).map(w => w.replace(/^#/, ''));
      const owners = q.get('owners')?.split(',');
      // `number`: one task by its number, in any status and pipeline, snoozed or not (backend/task_list_routes.py).
      const number = q.get('number');
      const rows = tasks.filter(x => (!status || status === 'all' || x.status === status) && (!ids || ids.includes(x.id))
        && (!number || x.number === Number(number))
        && (ids || number || snoozed === 'all' || (snoozed === 'only' ? !!x.snoozed : !x.snoozed)) && (!q.get('pipeline') || x.pipeline_id === q.get('pipeline'))
        && (!owner || x.owner_id === owner) && words.every(w => x.title.toLowerCase().includes(w))
        && (!owners || owners.includes(String(x.owner_id))) && said.every(w => `${x.title} ${x.number ?? ''}`.toLowerCase().includes(w)));
      // In the server's order (backend/hubdb.py visible_tasks), then a page as backend/task_list_routes.py cuts it:
      // `limit` rows from `offset`, or after the sort key `after` names, and where the next page starts.
      const finished = (t: Task) => String(t['done_at'] ?? t['closed_at'] ?? t.updated_at ?? t.created_at ?? '');
      const byFinished = q.get('sort') === 'finished';
      const key = (t: Task) => byFinished ? [finished(t), t.id] : [String(t.created_at ?? ''), t.id];
      const cmp = (a: string[], b: string[]) => (byFinished ? -1 : 1) * (a[0]!.localeCompare(b[0]!) || a[1]!.localeCompare(b[1]!));
      rows.sort((a, b) => cmp(key(a), key(b)));
      const after = q.get('after'), from = after ? JSON.parse(Buffer.from(after, 'base64url').toString()) as string[] : null;
      const limit = Number(q.get('limit') || 500), offset = Number(q.get('offset') || 0);
      const rest = from ? rows.filter(t => cmp(key(t), from) > 0) : rows.slice(offset), page = rest.slice(0, limit);
      const more = rest.length > limit;
      return json({tasks: q.get('brief') ? page.map(brief) : page, next_offset: more ? offset + limit : null,
        next_after: more ? Buffer.from(JSON.stringify(key(page.at(-1)!))).toString('base64url') : null, cursor: 'c1'});
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
      return json({task: t, children: [], comments: comments[t.id] || [], events: events[t.id] || [], runs: runs[t.id] || [], messages: [], has_more: false, mover});
    }
    if (p === '/api/v2/conversations') return json({conversations: []});
    // A chat (thread-view.ts): a task's chat is c-<task id>, its comments; a direct chat, the messages kept by its id.
    const thread = p.match(/^\/api\/v2\/threads\/([^/]+)\/(timeline|messages)$/);
    if (thread) {
      const cid = decodeURIComponent(thread[1]!), t = find(cid.replace(/^c-/, '')), list = (comments[t ? t.id : cid] ||= []);
      if (thread[2] === 'messages' && method === 'POST') {
        const body = (req.postDataJSON() || {}) as Posted['body']; posts.push({p, body});
        const m = {id: 'm' + (list.length + 1), seq: list.length + 1, kind: 'say', sender_id: 'human:ana', body: body.text, conversation_id: cid,
          created_at: new Date().toISOString(), refs: t ? {task: t.id} : {}};
        list.push(m);
        return json({message: m});
      }
      // A direct chat is with whoever else wrote in it.
      const dmWith = t ? {} : {kind: 'dm', dm_with: [...new Set(list.map(m => String((m as {sender_id?: string}).sender_id)))].filter(a => a !== 'human:ana').map(actor => ({actor}))};
      if (thread[2] === 'timeline') return json({thread: {id: cid, root_id: cid, title: t?.title || '', task_id: t?.id || null, me: {can_post: true}, ...dmWith},
        messages: list.map((m, i) => ({seq: i + 1, conversation_id: cid, ...(m as object)})), next_cursor: null});
    }
    if (p.endsWith('/messages')) return json({messages: []});
    return json(unrouted(p));
  });
  await beforeGoto?.(page);
  await page.goto('https://tico-ui.test/' + hash);
  if (!settle) return {page, errors, posts, reads, tasks, needs: () => needs};
  // A task waiting on Ana's own answer opens on its ask (decideTaskOpen), any other on its page.
  const ready = hash.startsWith('#/task/')
    ? page.locator('#task-modal[open] .tmodal-title, #need-card[open] .dc-q').first().waitFor()
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
    // Each row leads with its task's title; only the Answer row keeps the question as a second line, an OK's ask is its tooltip.
    assert.deepEqual(await rowTitles(page), ['Signup plans', 'Checkout flow', 'Book the team offsite']);
    assert.deepEqual(await page.locator('#task-body .tk-row .tk-ask').allInnerTexts(), ['Which plan do new signups get?']);
    assert.equal(await page.locator('[data-need="r2"] .tk-ask').count(), 1, 'the question under its title');
    assert.equal(await page.getByRole('button', {name: /^Signup plans Which plan do new signups get\?/}).count(), 1, 'a screen reader hears the title and its question apart');
    assert.equal(await page.locator('[data-need="r1"]').getAttribute('title'), 'Approve the spec for Checkout flow', 'an OK has its ask as the tooltip');
    assert.equal(await page.locator('[data-need="task:t-3"]').getAttribute('title'), null, 'a task of your own has no tooltip');
    assert.equal((await page.locator('#task-scope').innerText()).trim(), 'You', 'the count shows once, in ▶');
    assert.equal(await page.locator('#task-play .tl-review-n').innerText(), '3');
    assert.ok(await sideways(page) <= 1, `nothing scrolls sideways at ${viewport.width}px`);
    // Search narrows the rows; Escape clears it.
    await page.locator('#task-q').fill('offsite');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-row').length === 1);
    assert.deepEqual(await rowTitles(page), ['Book the team offsite']);
    await page.locator('#task-q').fill('nothing like this');
    await page.locator('#task-body .empty', {hasText: 'No matches'}).waitFor();
    await page.locator('#task-q').press('Escape');
    await page.waitForFunction(() => document.querySelectorAll('#task-body .tk-row').length === 3);
    assert.deepEqual(posts, [], 'a row answers nothing in place');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('You: Answer, Review and Do; rows by task title; search: ok');
}

/** An ask or a ❓ that has waited on the person more than a day says the day it was asked ("Since Nov 16"), never Late: a
 * task waiting on a person's answer is not Late (backend/late_rule.py). A row of her own work keeps its short age. */
async function youSince(browser: Browser) {
  const tasks = fixtures();
  const days = 2 * 1440 + 30;
  const needs: Need[] = needsOf(tasks).map(n => n.id === 'r1' || n.id === 'task:t-3' ? {...n, since: at(days)} : n);
  const {page, errors} = await open(browser, {tasks, needs});
  const age = (id: string) => page.locator(`[data-need="${id}"] .tk-age`).innerText().then(s => s.trim());
  assert.match(await age('r1'), /^Since [A-Z][a-z]{2} \d{1,2}(, \d{4})?$/, 'an OK asked two days ago says the day');
  assert.equal(await age('r2'), '1h', 'a question asked an hour ago keeps its short age');
  assert.equal(await age('task:t-3'), '2d', 'her own task is not an ask: its short age');
  assert.deepEqual(errors, []);
  await page.close();
  console.log('You: an ask past a day says Since <day>: ok');
}

/** A person's own open tasks, with nothing asked of them, are not in Needs you (its count, ▶, the badge) but are in You:
 * after Needs you's rows, at the end of Do, each once, kept live from the stream; their snoozed ones are in the archive. */
async function youOwnTasks(browser: Browser) {
  const tasks = [...fixtures(),
    task('t-8', 'Call the landlord', 'doing', {owner_id: 'human:ana', requester_id: 'human:ana', turn: turn('person', 'human:ana', 'Waiting on Ana')}),
    task('t-9', 'File the permit', 'todo', {owner_id: 'human:ana', snoozed: true, snooze: {by: 'human:ana', until: new Date(NOW + 86400e3).toISOString(), why: null},
      turn: turn('snoozed', null, 'Snoozed')})];
  const of = (id: string) => brief(tasks.find(t => t.id === id)!);
  // Needs you as the server gives it now: the asks, and a bot's private task Ana must let someone see (a Do row); t-3 and
  // t-8 are Ana's own, with nothing asked of her, so Needs you leaves them out.
  const needs: Need[] = [...needsOf(tasks).filter(n => n.kind !== 'task'),
    {kind: 'task', id: 'task:t-4', task_id: 't-4', line: 'Let Sam see this task', since: at(120), task: of('t-4')}];
  for (const viewport of [{width: 1440, height: 900}, {width: 390, height: 844}]) {
    const {page, errors, reads} = await open(browser, {viewport, tasks, needs});
    await page.locator('#task-body [data-task="t-8"]').waitFor();
    const own = reads.find(u => u.searchParams.get('owner'))!.searchParams;
    assert.deepEqual([own.get('owner'), own.get('status'), own.get('snoozed')], ['human:ana', 'open', 'hide'], 'You reads her own open tasks');
    assert.deepEqual(await groups(page), ['Answer', 'Review', 'Do']);
    assert.deepEqual(await rowTitles(page), ['Signup plans', 'Checkout flow', 'Rounding in the API', 'Book the team offsite', 'Call the landlord'],
      'Needs you first, then her own tasks at the end of Do; a snoozed one is not here');
    assert.deepEqual(await page.locator('#task-body .tk-row').evaluateAll(els => els.map(e => { const d = (e as HTMLElement).dataset; return d['need'] || 'task:' + d['task']; })),
      ['r2', 'r1', 'task:t-4', 'task:t-3', 'task:t-8'], 'an own task is a task row, not a Needs you item');
    assert.equal(await page.locator('#task-body [data-task="t-3"] .tk-mark').count(), 0, 'your own row has no mark');
    // Needs you, its count and ▶ stay what someone asks of her.
    assert.equal(await page.locator('#task-play .tl-review-n').innerText(), '3');
    assert.deepEqual(await page.evaluate(() => TASKS_ST.needs.map(n => n.id)), ['r1', 'r2', 'task:t-4']);
    assert.ok(await sideways(page) <= 1, `nothing scrolls sideways at ${viewport.width}px`);
    // Search narrows her own tasks too.
    await page.locator('#task-q').fill('landlord');
    await page.waitForFunction(() => !TASKS_ST.loading && document.querySelectorAll('#task-body .tk-row').length === 1);
    assert.deepEqual(await rowTitles(page), ['Call the landlord']);
    await page.locator('#task-q').press('Escape');
    await page.waitForFunction(() => !TASKS_ST.loading && document.querySelectorAll('#task-body .tk-row').length === 5);
    // Live, with nothing read: a task given to her comes in, one she finished leaves, a bot's never shows.
    const before = reads.length;
    const given = brief(task('t-10', 'Sign the lease', 'todo', {owner_id: 'human:ana', created_at: at(1)}));
    const finished = {...of('t-3'), status: 'done', stage_id: 'done', turn: null, version: 4};
    const bots = brief(task('t-11', 'Draft the newsletter', 'spec', {owner_id: 'bot:writer'}));
    await page.evaluate(([a, b, c]) => tasksLiveApply([{id: 't-10', task: a}, {id: 't-3', task: b}, {id: 't-11', task: c}]), [given, finished, bots] as const);
    await page.waitForFunction(() => document.querySelector('#task-body [data-task="t-10"]') && !document.querySelector('#task-body [data-task="t-3"]'));
    assert.deepEqual((await rowTitles(page)).slice(-2), ['Call the landlord', 'Sign the lease']);
    assert.equal(await page.locator('#task-body [data-task="t-11"]').count(), 0);
    assert.equal(reads.length, before, 'live changes read nothing');
    assert.equal(await page.locator('#task-play .tl-review-n').innerText(), '3', 'her own tasks never count in Needs you');
    // Her own task opens in its window.
    await page.locator('#task-body [data-task="t-8"]').click();
    await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Call the landlord'}).waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  // Task #389: an OK filed by a bot is titled by its kind alone ("Send an email"); its row says what it is about.
  {
    const tasks = [...fixtures(), task('t-8', 'Send an email', 'todo', {owner_id: 'bot:ops', turn: turn('person', 'human:ana', 'Waiting on Ana', {ask: true, requirement_id: 'r8'})})];
    const needs = [...needsOf(tasks), {kind: 'ask', id: 'r8', task_id: 't-8', requirement_id: 'r8', line: 'Send this email?', since: at(30),
      task: brief(tasks.find(t => t.id === 't-8')!), ask: ask('r8', 'Send this email?'), about: 'Send email: November product update'} as Need];
    const {page, errors} = await open(browser, {tasks, needs});
    assert.ok((await rowTitles(page)).includes('Send email: November product update'), 'the OK says what it is about');
    assert.ok(!(await rowTitles(page)).includes('Send an email'), 'not the kind alone');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("You: a person's own open tasks after Needs you, not in it; live; search: ok");
}

/** Rows with the same ask read by their tasks' titles; a Paused cause and a mention outside a task by their own line. */
async function youByTitle(browser: Browser) {
  const tasks = fixtures(), of = (id: string) => brief(tasks.find(t => t.id === id)!), done = 'Say when the work is done';
  const needs = [
    {kind: 'task', id: 'task:t-3', task_id: 't-3', line: done, since: at(300), task: of('t-3')},
    {kind: 'task', id: 'task:t-4', task_id: 't-4', line: done, since: at(200), task: of('t-4')},
    {kind: 'paused', id: 'paused:google', line: 'Connect Google', since: at(100), task: of('t-1'), bots: ['ops']},
    {kind: 'mention', id: 'mention:m1', line: '@ana are we on for Friday?', since: at(50), from_id: 'human:sam'},
  ] as unknown as Need[];
  for (const viewport of [{width: 1440, height: 900}, {width: 400, height: 844}]) {
    const {page, errors} = await open(browser, {viewport, tasks, needs});
    await page.locator('#task-body .tk-row').nth(3).waitFor();
    const titles = await rowTitles(page);
    assert.deepEqual([...titles].sort(), ['@ana are we on for Friday?', 'Book the team offsite', 'Connect Google', 'Rounding in the API'], `titles at ${viewport.width}`);
    assert.equal(new Set(titles).size, titles.length, 'no two rows read the same');
    assert.equal(await page.locator('[data-need="task:t-3"] .tk-ask').count(), 0, 'a Do row is one line');
    assert.equal(await page.locator('[data-need="paused:google"] .tk-title').innerText(), 'Connect Google', 'Paused keeps its cause');
    assert.ok(await sideways(page) <= 1, `nothing scrolls sideways at ${viewport.width}px`);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('You: rows with the same ask read by their titles; Paused and a mention by their line: ok');
}

// The page once opened as a blank "You", with no other boards in the view button, for seconds: the boards' list went in
// only when the slower Needs you or the rows read landed too, and until then a board left in view (tico.tasks.view)
// drew as You with "Nothing here". Each frame of a slow open is recorded: the view button never says You while a board
// is in view, nothing says "Nothing here" before the rows land, the boards are offered as soon as their read lands, and
// a later open names the board at once from the boards kept from the last read. The kept list is only ever shown: it
// is another person's never, and the live list replaces it on every path (a task opened by its address, a search typed
// while the page loads).
type Kept = {who: string; names: Record<string, string>; drop?: string} | null;
const KEPT_KEY = 'tico.tasks.pipelines';
async function recorded(browser: Browser, hash: string, slow: Hold, kept: Kept = null, view = 'ops') {
  const list = kept ? PIPELINES.filter(p => !p.retired_at && p.id !== kept.drop).map(p => ({...p, name: kept.names[p.id] ?? p.name})) : null;
  return open(browser, {hash, slow, settle: false, beforeGoto: (pg: Page) => pg.addInitScript(([key, seed, board]) => {
    localStorage.setItem('tico.tasks.view', JSON.stringify({pipeline: board, layout: 'list'}));
    if (seed && !sessionStorage.getItem('seeded')) { localStorage.setItem(key, seed); sessionStorage.setItem('seeded', '1'); }
    const seen: string[][] = (window as unknown as {seen: string[][]}).seen = [];
    const look = () => {
      const st = typeof TASKS_ST === 'undefined' ? null : TASKS_ST;   // a classic script's `let`: no window property
      const scope = document.querySelector('#task-scope .tl-scope-l')?.textContent ?? null;
      if (st) seen.push([String(st.loading), scope ?? '', String((st.pipelines as unknown[]).length), document.querySelector('#task-body .empty') ? 'empty' : '']);
      requestAnimationFrame(look);
    };
    requestAnimationFrame(look);
  }, [KEPT_KEY, list ? JSON.stringify({who: kept!.who, list}) : '', view] as const)});
}
const frames = (page: Page) => page.evaluate(() => (window as unknown as {seen: string[][]}).seen);
/** More than `n` frames drawn while a read is out: the page draws on while the held reads wait. */
const drawing = (page: Page, n = 5) =>
  page.waitForFunction(k => (window as unknown as {seen: string[][]}).seen.filter(f => f[0] === 'true').length > k, n);
/** Every read answered: the held ones let go, and the page done with them. */
const loaded = (page: Page) => page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading);
/** The boards' read let go and taken in: the live names are the page's. */
const boards = async (page: Page, slow: Hold) => {
  slow.release(PIPES);
  await page.waitForFunction(() => (TASKS_ST?.pipelines as {name: string}[] | undefined)?.map(p => p.name).join() === 'Product,Operations');
};
const shown = (page: Page) => page.evaluate(() => ({view: TASKS_ST!.pipeline, scope: document.querySelector('#task-scope .tl-scope-l')?.textContent ?? '',
  names: (TASKS_ST!.pipelines as {name: string}[]).map(p => p.name)}));
const ROWS = '/api/v2/tasks', NEEDS = '/api/v2/needs-you', PIPES = '/api/v2/pipelines';
async function slowOpen(browser: Browser) {
  {
    // Needs you answers at once (the app's own read): only the board's rows are slow, so its count must not stand in.
    const slow = new Hold(ROWS);
    const {page, errors} = await recorded(browser, '#/tasks', slow);
    await drawing(page);
    // The boards are offered while the rows are still held: before the fix, this waited out its deadline.
    await page.waitForFunction(() => (TASKS_ST?.pipelines as unknown[] | undefined)?.length === 2);
    slow.release();
    await loaded(page);
    let seen = await frames(page);
    const loading = seen.filter(f => f[0] === 'true');
    assert.deepEqual(loading.filter(f => f[1] === 'You'), [], 'a board in view never shows as You');
    assert.deepEqual(loading.filter(f => f[3] === 'empty'), [], 'no "Nothing here" before the rows land');
    assert.ok(loading.some(f => f[1] === 'Operations' && f[2] === '2'), 'the boards are offered before the rows land');
    // The next open, the boards' read slow too: the board is named from the first frame the page has.
    slow.hold(ROWS, PIPES);
    await page.reload();
    await drawing(page);
    slow.release();
    await loaded(page);
    seen = await frames(page);
    assert.equal(seen[0]?.[0], 'true', 'the first frame is drawn before any read lands');
    assert.deepEqual(seen[0]?.slice(1, 3), ['Operations', '2'], 'named at once from the boards kept');
    assert.deepEqual(seen.filter(f => f[1] === 'You' || (f[0] === 'true' && f[3] === 'empty')), []);
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // From You, Needs you read, to a board whose rows are slow: Needs you's rows say nothing of the board's.
    const slow = new Hold();
    const {page, errors} = await recorded(browser, '#/tasks?view=foryou', slow);
    await page.waitForFunction(() => TASKS_ST && !TASKS_ST.loading && TASKS_ST.needs.length > 0);
    slow.hold(ROWS);
    await page.evaluate(() => { (window as unknown as {seen: string[][]}).seen.length = 0; });
    await page.locator('#task-scope').click();
    await page.locator('#task-filter-pop [data-scope="ops"]').click();
    await page.waitForFunction(() => TASKS_ST?.pipeline === 'ops');
    await drawing(page);
    slow.release();
    await loaded(page);
    const loading = (await frames(page)).filter(f => f[0] === 'true');
    assert.deepEqual(loading.filter(f => f[3] === 'empty'), [], 'no "Nothing here" before the board\'s rows land');
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // Boards kept here for another person: none of them is shown to Ana.
    const slow = new Hold(ROWS, PIPES);
    const {page, errors} = await recorded(browser, '#/tasks', slow, {who: 'sam', names: {ops: 'Sam ops'}});
    await drawing(page);
    await boards(page, slow);
    slow.release();
    await loaded(page);
    const seen = await frames(page);
    assert.deepEqual(seen[0]?.slice(1, 3), ['', '0'], "another person's boards are not shown");
    assert.ok(!seen.some(f => f[1] === 'Sam ops'));
    assert.deepEqual(errors, []);
    await page.close();
  }
  const old: Kept = {who: 'ana', names: {ops: 'Ops (old name)'}};
  // The boards land first, then the rows and Needs you: the order a slow open used to race on.
  const behindTask = async (kept: Kept, view: string, what: string) => {
    const slow = new Hold(ROWS, NEEDS, PIPES);
    const {page, errors} = await recorded(browser, '#/task/t-3', slow, kept, view);
    await page.waitForFunction(() => !!document.querySelector('dialog[open]'));
    assert.equal(await page.evaluate(() => TASKS_ST?.pipeline), 'ops', `the task's board is behind it before the boards land: ${what}`);
    await boards(page, slow);
    slow.release();
    await loaded(page);
    assert.deepEqual(await shown(page), {view: 'ops', scope: 'Operations', names: ['Product', 'Operations']}, what);
    assert.deepEqual(errors, []);
    await page.close();
  };
  // A task opened by its address puts its board behind it before the boards land: the live name wins once they do.
  await behindTask(old, 'ops', 'the live list, not the kept one');
  // A task on a board made since the kept list was read still has its board put behind it.
  await behindTask({who: 'ana', names: {}, drop: 'ops'}, 'product', 'a board new since the kept list');
  {
    // Nothing kept, a search's rows landing before the boards: the board in view is not taken for gone.
    const slow = new Hold(ROWS, NEEDS, PIPES);
    const {page, errors} = await recorded(browser, '#/tasks', slow);
    await page.locator('#task-q').fill('a');
    await page.waitForFunction(() => TASKS_ST?.q === 'a');
    slow.release(ROWS, NEEDS);
    await loaded(page);
    await boards(page, slow);
    await loaded(page);
    assert.deepEqual(await shown(page), {view: 'ops', scope: 'Operations', names: ['Product', 'Operations']}, 'the board stays in view');
    assert.deepEqual(errors, []);
    await page.close();
  }
  for (const kept of [old, null]) {
    // A search typed while the page loads reads with what was known then: the live list still wins, and the board stays.
    const slow = new Hold(ROWS, NEEDS, PIPES);
    const {page, errors} = await recorded(browser, '#/tasks', slow, kept);
    await page.locator('#task-q').fill('a');
    await page.waitForFunction(() => TASKS_ST?.q === 'a');
    await boards(page, slow);
    slow.release();
    await loaded(page);
    assert.deepEqual(await shown(page), {view: 'ops', scope: 'Operations', names: ['Product', 'Operations']}, `searching during a slow open, ${kept ? 'a kept list' : 'none kept'}`);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('a slow open: a board in view is never a blank You; the boards show before the rows; the live list wins: ok');
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
  assert.deepEqual([read.get('status'), read.get('snoozed')], ['open', 'all'], 'a pipeline reads its open tasks, snoozed ones too');
  assert.equal((await page.locator('#task-scope').innerText()).trim(), 'Operations');
  await page.locator('#task-body', {hasText: 'Book the team offsite'}).waitFor();
  // The snoozed task stays in its stage, last there and dimmed (task-pipelines.ts; pipeline-snoozed.cts checks the rest).
  const asleep = page.locator('#task-body .pv-snoozed[data-task="t-5"]');
  await asleep.waitFor();
  assert.ok(await asleep.evaluate(el => !el.nextElementSibling), 'the snoozed task is last in its stage');
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
  await page.locator('#task-menu').click();
  await page.locator('#task-mi-archive', {hasText: 'Archived'}).click();
  await page.waitForFunction(() => document.querySelector('#task-menu')?.getAttribute('aria-expanded') === 'false');   // ⋯ is not left expanded
  await shows('snoozed', {status: 'open', snoozed: 'only'}, ['Renew the domain']);
  // A write's reply is the full task, `snooze` without `snoozed`: the snoozed task stays in Snoozed.
  await page.evaluate(() => {
    const t = TASKS_ST.tasks.find(x => x.id === 't-5')!, {snoozed: _, ...full} = {...t, title: 'Renew the domain name', snooze: {by: 'human:ana', until: null, why: null}};
    tasksTaskPut(TASKS_ST, full);
  });
  assert.deepEqual(await rowTitles(page), ['Renew the domain name']);
  await page.locator('#task-archives [data-archive="done"]').click();
  await shows('done', {status: 'done', sort: 'finished'}, ['Ship the receipt email']);
  // A finished row's age is how long ago it finished (done_at, not its later updated_at), in the shared words (format.ts
  // ago), as an open row's is: done 4 hours ago reads "4h", never the day's date nor "10m".
  assert.equal((await page.locator('#task-body [data-task="t-6"] .tk-age').innerText()).trim(), '4h');
  await page.locator('#task-archives [data-archive="closed"]').click();
  await shows('closed', {status: 'closed', sort: 'finished'}, ['Old pricing test']);
  // Closed over two days ago (closed_at; it changed 5 minutes ago): the day, month first ("Nov 12"), from the same words.
  assert.equal((await page.locator('#task-body [data-task="t-7"] .tk-age').innerText()).trim(),
    await page.evaluate(() => dayWords((TASKS_ST.tasks.find(t => t.id === 't-7') as {closed_at?: string} | undefined)?.closed_at)));
  assert.match((await page.locator('#task-body [data-task="t-7"] .tk-age').innerText()).trim(), /^[A-Z][a-z]{2} \d{1,2}(, \d{4})?$/);
  // A row opens the task in its window.
  await page.locator('#task-body [data-task="t-7"]').click();
  await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Old pricing test'}).waitFor();
  await page.keyboard.press('Escape');
  // ✕ at the bar's left leaves the archive.
  assert.equal(await page.locator('#task-bar #task-archive-x').getAttribute('aria-label'), 'Leave Archived');
  await page.locator('#task-archive-x').click();
  await page.waitForFunction(() => !TASKS_ST.archive && !TASKS_ST.loading);
  assert.equal(await page.locator('#task-archive-x').isVisible(), false);
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

async function pages(browser: Browser) {
  // Operations holds 520 open tasks; Done holds 205.
  const n = (i: number) => String(i).padStart(3, '0');
  const tasks = [...fixtures(), ...Array.from({length: 520}, (_, i) => task(`p-${n(i)}`, `Errand ${n(i)}`, 'todo', {number: 1000 + i})),
    ...Array.from({length: 205}, (_, i) => task(`d-${n(i)}`, `Finished ${n(i)}`, 'done', {number: 2000 + i}))]
    .filter(t => t.id !== 't-3');
  const more = (page: Page) => page.locator('#task-more button');
  const held = (page: Page) => page.evaluate(() => TASKS_ST.tasks.length);
  {
    const {page, errors, reads} = await open(browser, {tasks, needs: [], hash: '#/tasks?pipeline=ops'});
    await page.waitForFunction(() => !TASKS_ST.loading);
    assert.equal(await held(page), 500, 'one page');
    await more(page).waitFor();
    // A task taken out live: the next page starts after the last row given (its sort key), so nothing is skipped.
    await page.evaluate(() => tasksLiveApply([{id: 'p-001', gone: true}]));
    await more(page).click();
    await more(page).waitFor({state: 'hidden'});
    assert.equal(reads.at(-1)!.searchParams.get('offset'), null);
    assert.deepEqual(JSON.parse(Buffer.from(reads.at(-1)!.searchParams.get('after')!, 'base64url').toString()).at(-1), 'p-499');
    // 519 errands and the snoozed fixture (t-5): a pipeline holds its snoozed tasks too.
    assert.equal(await held(page), 520, 'every open task but the one taken out');
    assert.equal(new Set(await page.evaluate(() => TASKS_ST.tasks.map(t => t.id))).size, 520, 'none twice');
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // A search is the server's: one request answers the task past the first page, the rest of the view is not read.
    const {page, errors, reads} = await open(browser, {tasks, needs: [], hash: '#/tasks?pipeline=ops'});
    await page.waitForFunction(() => !TASKS_ST.loading);
    const before = reads.length;
    await page.locator('#task-q').fill('Errand 515');
    await page.waitForFunction(() => !TASKS_ST.loading && TASKS_ST.tasks.length === 1);
    await page.locator('#task-body [data-task="p-515"]').waitFor();
    assert.deepEqual(reads.slice(before).map(u => [u.searchParams.get('search'), u.searchParams.get('limit'), u.searchParams.get('offset')]),
      [['Errand 515', '100', null]], 'one request of at most 100 rows');
    assert.equal(await more(page).isVisible(), false);
    // A search that matches nothing: one request, no rows, no More.
    const mid = reads.length;
    await page.locator('#task-q').fill('zzqx');
    await page.locator('#task-body .pv-empty', {hasText: 'Nothing found'}).waitFor();
    await page.waitForFunction(() => !TASKS_ST.loading);
    assert.equal(reads.length - mid, 1);
    assert.equal(await more(page).isVisible(), false);
    // A search over many: its first 100, then More until it goes away gives every match once, in queue order.
    await page.locator('#task-q').fill('errand');
    await page.waitForFunction(() => !TASKS_ST.loading && TASKS_ST.tasks.length === 100);
    for (let i = 0; i < 5; i++) { await more(page).click(); await page.waitForFunction(n => TASKS_ST.tasks.length === n && !TASKS_ST.paging, Math.min(520, 200 + i * 100)); }
    await more(page).waitFor({state: 'hidden'});
    const ids = await page.evaluate(() => TASKS_ST.tasks.map(t => t.id));
    assert.deepEqual(ids, Array.from({length: 520}, (_, i) => `p-${n(i)}`), 'each once, in queue order');
    // A task open over the page stays open through a change of search.
    await page.locator('#task-body [data-task="p-007"]').click();
    await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Errand 007'}).waitFor();
    await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = 'Errand 007'; q.dispatchEvent(new Event('input')); });
    await page.waitForFunction(() => !TASKS_ST.loading && TASKS_ST.tasks.length === 1);
    assert.equal(await page.locator('#task-modal[open]').count(), 1, 'the task stays open');
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    const {page, errors, reads} = await open(browser, {tasks, needs: [], hash: '#/tasks?archive=done'});
    await page.waitForFunction(() => TASKS_ST.archive === 'done' && !TASKS_ST.loading);
    assert.equal(await held(page), 200);
    await more(page).click();
    await more(page).waitFor({state: 'hidden'});
    assert.ok(reads.at(-1)!.searchParams.get('after'), 'the next page starts after the last row given');
    assert.equal(await held(page), 206, 'the archive in full');
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('a list over one page: More reads the next after the last row; a search reads one bounded page: ok');
}

// Spec "Task search finds a task by its number": a search that is only a number puts that task first, from any pipeline
// and status, with Enter opening it; words, an unknown number and an older number's late answer add nothing.
async function byNumber(browser: Browser) {
  // t-80: closed in the retired Old roadmap (prefix O), which no view or view menu offers.
  // t-44: another Product task a search for 4 matches (its number holds a 4).
  const tasks = [...fixtures(), task('t-55', 'Fifty-five', 'doing'), task('t-44', 'Order more chairs', 'spec'),
    task('t-80', 'Roadmap import', 'closed', {pipeline_id: 'old', pipeline: {id: 'old', name: 'Old roadmap'}})];
  // The rows a person sees, top to bottom: the task first, then the view's own matches.
  const order = (page: Page) => page.locator('[data-task]:visible').evaluateAll(rows => rows.map(r => (r as HTMLElement).dataset.task));
  const jump = (page: Page) => page.locator('#task-jump [data-task]');
  const shows = async (page: Page, typed: string, id: string, where: string) => {
    await page.locator('#task-q').fill(typed);
    await page.locator(`#task-jump [data-task="${id}"]`).waitFor();
    assert.equal(await jump(page).count(), 1, typed);
    assert.equal((await page.locator('#task-jump .pill').textContent())!.trim(), where, typed);
  };
  // A number search waits for its number read's answer; words read no number. Then the search's own read settles.
  const none = async (page: Page, typed: string, number?: string) => {
    const answered = number ? page.waitForResponse(r => new URL(r.url()).searchParams.get('number') === number) : null;
    const searched = page.waitForResponse(r => new URL(r.url()).searchParams.get('search') === typed.trim());
    await page.locator('#task-q').fill(typed);
    await Promise.all([answered, searched]);
    await page.waitForFunction(() => !TASKS_ST.loading);
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.equal(await page.locator('#task-jump').isHidden(), true, typed);
  };
  {
    // You: a closed task of the Product pipeline, which no open view holds.
    const {page, errors, reads} = await open(browser, {tasks});
    await page.waitForFunction(() => !TASKS_ST.loading);
    await shows(page, '7', 't-7', 'Closed');
    assert.equal((await jump(page).locator('.tk-ref').textContent())!.trim(), 'P7');
    assert.equal((await jump(page).locator('.tk-title').textContent())!.trim(), 'Old pricing test');
    assert.match((await jump(page).locator('.tk-ask').textContent())!, /^Product · /);
    const read = reads.find(u => u.searchParams.get('number') === '7')!;
    assert.equal(read.searchParams.get('status'), null, 'any status');
    assert.equal(read.searchParams.get('pipeline'), null, 'any pipeline');
    for (const typed of ['#7', 'p7', 'P-7', ' 7 ']) await shows(page, typed, 't-7', 'Closed');
    // Enter in the search opens it.
    await page.locator('#task-q').fill('7');
    await jump(page).waitFor();
    await page.locator('#task-q').press('Enter');
    await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Old pricing test'}).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('#task-modal[open]').waitFor({state: 'detached'});
    // A snoozed one and a done one, from You.
    await shows(page, '5', 't-5', 'Snoozed');
    await shows(page, '#6', 't-6', 'Done');
    // A finished task of a retired pipeline: found by its label too, and named with that pipeline.
    for (const typed of ['80', 'O80', 'o-80']) await shows(page, typed, 't-80', 'Closed');
    assert.equal((await jump(page).locator('.tk-ref').textContent())!.trim(), 'O80');
    assert.match((await jump(page).locator('.tk-ask').textContent())!, /^Old roadmap · /);
    // Words, letters no pipeline has, and a number no task has: the search as before, nothing first.
    await none(page, '7 old');
    await none(page, 'x7');
    await none(page, '99', '99');
    await page.locator('#task-body', {hasText: 'No matches'}).waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // Another pipeline's open task shows its stage; in its own pipeline it is also a match of the view, and shows once.
    const {page, errors} = await open(browser, {tasks, needs: [], hash: '#/tasks?pipeline=ops'});
    await page.waitForFunction(() => !TASKS_ST.loading);
    await shows(page, '4', 't-4', 'Build');
    await jump(page).click();
    await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Rounding in the API'}).waitFor();
    await page.close();
    assert.deepEqual(errors, []);
  }
  {
    const {page, errors} = await open(browser, {tasks, needs: [], hash: '#/tasks?pipeline=product'});
    await page.waitForFunction(() => !TASKS_ST.loading);
    await shows(page, '4', 't-4', 'Build');
    await page.waitForFunction(() => !TASKS_ST.loading && TASKS_ST.tasks.some(t => t.id === 't-4'));
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.equal(await page.locator('[data-task="t-4"]:visible').count(), 1, 'once');
    // The view's other match (t-44) is still there, under the task the number names.
    await page.locator('#task-body [data-task="t-44"]').waitFor();
    assert.deepEqual(await order(page), ['t-4', 't-44'], 'the task first, then the view');
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // 5 then 55, typed fast, 5's answer last: only 55 shows.
    const {page, errors} = await open(browser, {tasks, needs: []});
    await page.waitForFunction(() => !TASKS_ST.loading);
    // 5's read is held until 55 shows, then let go: its answer lands after 55's.
    let late = 0, letGo = () => {};
    const held = new Promise<void>(res => { letGo = res; });
    await page.route(u => u.pathname === '/api/v2/tasks' && u.searchParams.get('number') === '5', async r => {
      late++; await held; await r.fallback();
    });
    await page.locator('#task-q').fill('5');
    await page.waitForFunction(() => TASKS_ST.jumpFor === 5);
    await page.locator('#task-q').fill('55');
    await page.locator('#task-jump [data-task="t-55"]').waitFor();
    const fiveAnswered = page.waitForResponse(r => new URL(r.url()).searchParams.get('number') === '5');
    letGo();
    await fiveAnswered;
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.equal(late, 1);
    assert.deepEqual(await jump(page).evaluateAll(rows => rows.map(r => (r as HTMLElement).dataset.task)), ['t-55']);
    assert.deepEqual(errors, []);
    await page.close();
  }
  {
    // A phone: the row fits, and a tap opens it.
    const {page, errors} = await open(browser, {tasks, viewport: {width: 390, height: 844}});
    await page.waitForFunction(() => !TASKS_ST.loading);
    await page.evaluate(() => { const q = document.querySelector<HTMLInputElement>('#task-q')!; q.value = '7'; q.dispatchEvent(new Event('input')); });
    await jump(page).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no sideways scroll');
    const row = await jump(page).boundingBox();
    assert.ok(row && row.x >= 0 && row.x + row.width <= 390, 'the row fits');
    await jump(page).tap();
    await page.locator('#task-modal[open] .tmodal-title', {hasText: 'Old pricing test'}).waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log('a search that is a task number puts that task first, from any pipeline and status: ok');
}

const api = {open, fixtures, task, brief, needsOf, PIPELINES};
export type Api = typeof api;
export type Opened = Awaited<ReturnType<typeof open>>;
module.exports = api;
if (require.main === module) (async () => {
  const browser = await chromium.launch({channel: process.env.TICO_BROWSER_CHANNEL ?? 'chrome', headless: true});
  try {
    for (const run of [slowOpen, you, youSince, youOwnTasks, youByTitle, views, archive, pipelineArchive, pages, byNumber]) await run(browser);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
