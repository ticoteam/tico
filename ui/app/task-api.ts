/* ui/app/task-api.ts — the one client for tasks and pipelines (docs/dev/pipelines-v3-cutover.md, "HTTP")
   Classic script: its globals are shared with the other files under ui/app/, loaded in the order index.html lists them.
   Every screen that reads or moves a task calls these, never the routes by hand. A refused move rejects with an
   ApiFailure whose body taskRefusedOf reads: what is missing and the commands allowed next. */
'use strict';

type TaskV3 = import('../types/api').Task;
type TaskBriefV3 = import('../types/api').TaskBrief;
type TaskWaitV3 = import('../types/api').TaskWait;
type TaskReq = import('../types/api').TaskRequirement;
type OkShown = import('../types/api').OkPreview;
/** One item of Needs you. `ask` is the requirement as the task's own read gives it; a stuck stretch is `paused`. */
type NeedsEntry = import('../types/api').NeedsYouItem & {
  missing?: string[];
  task_ids?: string[];
  bots?: string[];
  from_id?: string;
  conversation_id?: string;
  message_id?: string;
};
/** Either a full task or a list row: what a row, a mark or a filter reads. */
type TaskAny = TaskV3 | TaskBriefV3;
/** The stage a full task is in (the OpenAPI document leaves it untyped). */
interface TaskStageV3 { id: string; name: string; kind: 'work' | 'done' | 'closed'; by_hand: boolean; }

/** A pipeline's settings record (backend/pipeline_settings.read). */
interface PipelineReqRec {
  id?: string;
  kind: string;
  /** An item's source and condition (kind `item`, e.g. github and pr_merged); null on every other kind. */
  source?: string | null;
  condition?: string | null;
  settings: Record<string, unknown>;
  can_push_past: boolean;
  who_oks: 'none' | 'people_only' | 'people_with_exceptions';
  person_id: string | null;
  line: string;
  /** An answer's choices, when it is a choice; null on every other requirement. */
  choices?: string[] | null;
}
interface PipelineStageRec {
  id?: string;
  name: string;
  purpose: string;
  owner: string | null;
  by_hand: boolean;
  /** On a by-hand stage: a person's request moves on by itself when the next stage has room. */
  requests_move_on: boolean;
  task_limit: number | null;
  expected_minutes: number | null;
  expected_bots_only: boolean;
  on_enter: Record<string, unknown>;
  requirements: PipelineReqRec[];
  /** Where `owner` is `pick` ("One of several", backend/stage_pick.py): the bots it picks from and how. A save that
      leaves it out keeps the stored one. */
  pick?: PipelinePickRec;
  /** Who gets the next task, in one sentence, as last saved: the server writes it and a save never sends it. */
  pick_next?: string;
}
/** A stage's "One of several". `families` is each listed bot's model family ("Claude", "GPT"; "" when unknown), read only. */
interface PipelinePickRec {
  bots: string[];
  how: 'turns' | 'random';
  limit_each: number | null;
  prefer: 'anyone' | 'new' | 'family';
  owns_task: boolean;
  families?: Record<string, string>;
}
interface PipelineRec {
  id: string;
  name: string;
  goal: string;
  prefix: string | null;
  manager_id: string | null;
  owner_id: string | null;
  private: boolean;
  factory_id: string | null;
  preset: string | null;
  settings_version: number;
  retired_at: string | null;
  /** Each active bot's model family ("Claude", "GPT"; "" when unknown), for a stage's One of several: read only. */
  bot_families?: Record<string, string>;
  stages: PipelineStageRec[];
  done: {id: string; name: string} | null;
  closed: {id: string; name: string} | null;
  may_change?: boolean;
}

/** What every write on a task answers: the task as it is now, and the id the write made where it made one. */
interface TaskMoved { task: TaskV3; answer_id?: string; requirement_id?: string; feedback_id?: string; }
/** A move the engine refused (409): its rule, the line a person reads, what is missing, what may come next. */
interface TaskRefused { error: string; detail: string; missing: string[]; next: string[]; version: number | null; }

const taskPath = (id: string) => `/v2/tasks/${encodeURIComponent(id)}`;
const askPath = (id: string, rid: string) => `/v2/tasks/${encodeURIComponent(id)}/asks/${encodeURIComponent(rid)}`;
/** The engine's refusal in a failed write, or null when the failure was something else. */
function taskRefusedOf(err: unknown): TaskRefused | null {
  const body = (err as Partial<ApiFailure> | null)?.body;
  if (!body || typeof body.error !== 'string' || !body.error.startsWith('task_')) return null;
  const list = (v: unknown) => Array.isArray(v) ? v.map(String) : [];
  return {
    error: body.error,
    detail: typeof body.detail === 'string' ? body.detail : '',
    missing: list(body.missing),
    next: list(body.next),
    version: typeof body.version === 'number' ? body.version : null,
  };
}
const withVersion = (version: number | null | undefined, body: Record<string, unknown>) =>
  version == null ? body : {...body, version};

// ---- reads
const taskGet = (id: string): Promise<TaskV3> => get<TaskV3>(taskPath(id), {fresh: true});
interface TaskBriefQuery {
  owner?: string; status?: string; ids?: string[]; snoozed?: string; q?: string; pipeline?: string;
  sort?: 'queue' | 'finished'; limit?: number; offset?: number;
  /** The Tasks page's search and filters, with taskFilterMatch's meaning (backend/task_match.py). */
  search?: string; owners?: string[]; stages?: string[]; requesters?: string[]; labels?: string[]; turn?: string[];
  due?: string; late?: boolean; pushed_past?: boolean;
  /** A keyset page's start: the `next` the page before it gave. */
  after?: string;
}
const taskBriefParams = (query: TaskBriefQuery): URLSearchParams => {
  const p = new URLSearchParams({brief: '1'});
  for (const [k, v] of Object.entries(query)) {
    if (v == null || v === '') continue;
    p.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  return p;
};
/** One page of list rows, the cursor a later `taskChanges` resumes from, whether the server holds more (`more`) and
 * where its next keyset page starts (`next`, the `after` of that page). */
async function taskBriefsPage(query: TaskBriefQuery = {}): Promise<{tasks: TaskBriefV3[]; cursor: string; more: boolean; next: string}> {
  const res = await get<{tasks?: TaskBriefV3[]; cursor?: string; next_offset?: number | null; next_after?: string | null} | TaskBriefV3[]>(`/v2/tasks?${taskBriefParams(query)}`);
  return Array.isArray(res) ? {tasks: res, cursor: '', more: false, next: ''}
    : {tasks: res.tasks || [], cursor: res.cursor || '', more: res.next_offset != null || !!res.next_after, next: res.next_after || ''};
}
async function taskBriefs(query: TaskBriefQuery = {}): Promise<TaskBriefV3[]> {
  return (await taskBriefsPage(query)).tasks;
}
/** What changed since `cursor` under the same filters (GET /v2/tasks?changed_after): the changed rows that still match,
 * the ids that left the filters or became unreadable (`gone`), the next cursor; `reset` when only a full read will do. */
interface TaskChanges { tasks: TaskBriefV3[]; gone: string[]; cursor: string; reset?: boolean }
async function taskChanges(query: Omit<TaskBriefQuery, 'limit' | 'offset' | 'sort' | 'after'>, cursor: string): Promise<TaskChanges> {
  const p = taskBriefParams(query);
  p.set('changed_after', cursor);
  const res = await get<Partial<TaskChanges>>(`/v2/tasks?${p}`, {fresh: true});
  return {tasks: res.tasks || [], gone: (res.gone || []).map(String), cursor: res.cursor || '', reset: !!res.reset};
}

// ---- lists fed by the live stream
// Each list puts an event's task in place by its own rule and never reads the whole list because an event came (Chris,
// Nov 3). Its catch-up is a changed_after delta from the cursor its last read gave. The Tasks page, a bot's page, a
// person's page and My dashboard share these.
/** A row a live list holds: a list row, or a task as an event or a write carries it. */
type TaskLiveRow = {id: string};
/** A task as a write's reply carries it (Task), in a list row's terms (TaskBrief): a full task says `snooze`, not
 * always `snoozed`. */
function taskListRow<T extends TaskLiveRow>(t: T): T {
  const full = t as T & {snooze?: unknown; snoozed?: boolean};
  return 'snooze' in full && full.snoozed === undefined ? {...t, snoozed: !!full.snooze} : t;
}
/** A list's order, as the server reads it (backend/hubdb.py tasks `order`). */
type TaskRowOrder = (a: TaskLiveRow, b: TaskLiveRow) => number;
const taskRowField = (t: TaskLiveRow, k: string): unknown => (t as Record<string, unknown>)[k];
/** `queue`: ranked first, by rank, then oldest first. */
const taskQueueOrder: TaskRowOrder = (a, b) => {
  const ra = taskRowField(a, 'rank'), rb = taskRowField(b, 'rank');
  return (Number(ra == null) - Number(rb == null)) || (Number(ra ?? 0) - Number(rb ?? 0))
    || String(taskRowField(a, 'created_at') ?? '').localeCompare(String(taskRowField(b, 'created_at') ?? '')) || String(a.id).localeCompare(String(b.id));
};
/** `finished`: the most recently done or closed first. */
const taskFinishedOrder: TaskRowOrder = (a, b) => {
  const at = (t: TaskLiveRow) => String(['done_at', 'closed_at', 'updated_at', 'created_at'].map(k => taskRowField(t, k)).find(v => v) ?? '');
  return at(b).localeCompare(at(a)) || String(b.id).localeCompare(String(a.id));
};
/** One task as the viewer reads it now, into `rows` by the list's rule `has`: in place, added, or out when `has` says no
 * or the task is gone or unreadable (null). With the list's `order`, an added or changed row goes where that order puts
 * it; without, an added one goes first. True when the rows moved. */
function taskRowsPut<T extends TaskLiveRow>(rows: T[], given: T | null, id: string, has: (t: T) => boolean, order?: TaskRowOrder): boolean {
  const t = given && taskListRow(given), i = rows.findIndex(x => String(x.id) === id), keep = !!t && has(t);
  if (t && keep && order) {
    if (i >= 0) rows.splice(i, 1);
    const at = rows.findIndex(x => order(t, x) < 0);
    rows.splice(at < 0 ? rows.length : at, 0, t);
  } else if (t && keep && i >= 0) rows[i] = t;
  else if (t && keep) rows.unshift(t);
  else if (i >= 0) rows.splice(i, 1);
  else return false;
  return true;
}
/** The task an event carries, or null when the event says it is gone or the viewer can no longer read it. */
const taskOfEvent = <T extends TaskLiveRow>(d: LiveTaskEvent): T | null => d.gone ? null : (d.task as T | null | undefined) ?? null;
const taskEventId = (d: LiveTaskEvent) => String(d.id ?? d.task_id ?? '');
/** A burst of live task events onto `rows`: true when they moved, 'bulk' when one change named many tasks (the list then
 * catches up from its cursor; nothing is read for the others). */
function taskRowsLive<T extends TaskLiveRow>(rows: T[], events: LiveTaskEvent[], has: (t: T) => boolean, order?: TaskRowOrder): boolean | 'bulk' {
  if (events.some(d => d.bulk)) return 'bulk';
  let moved = false;
  for (const d of events) moved = taskRowsPut(rows, taskOfEvent<T>(d), taskEventId(d), has, order) || moved;
  return moved;
}
// A read and the events that come while it is out. A read answers the list as it was when the server read it; an event
// that came after (a task removed, a title changed) must not be undone by that older answer landing later. So while a
// list's read is out its events are held as well as shown, and when the read lands the held ones newer than it go over
// it again: a removal stays removed. Each event carries its change number (`seq`, backend/events.py _event) and a list
// read its cursor (`<seq>.<tag>`, backend/task_reads.py): what a read already holds is never played twice, and an event
// it covers that comes late is dropped. A read that is no longer the list's newest (the page was left or read again)
// is dropped whole.
/** One live list's reads against its events. `ticket` is its newest read; `held`, the events since a read went out (null
 * while none is out); `seq`, the change number the rows are as of (0: not known). */
interface TaskFeed { ticket: number; held: LiveTaskEvent[] | null; seq: number }
const taskFeedNew = (): TaskFeed => ({ticket: 0, held: null, seq: 0});
/** The change number a list read was taken at: its cursor's first part, 0 when it gave none. */
const taskCursorSeq = (cursor: string | null | undefined): number => Number(String(cursor || '').split('.')[0]) || 0;
const taskEventSeq = (d: LiveTaskEvent): number => Number(d.seq) || 0;
/** A read goes out: its ticket, which taskFeedLanded asks back. */
function taskFeedRead(feed: TaskFeed): number {
  feed.held ||= [];
  return ++feed.ticket;
}
/** Events as they come: the ones the rows do not already hold, kept too while a read is out. */
function taskFeedEvents(feed: TaskFeed, events: LiveTaskEvent[]): LiveTaskEvent[] {
  const fresh = events.filter(d => !taskEventSeq(d) || taskEventSeq(d) > feed.seq);
  feed.held?.push(...fresh);
  return fresh;
}
/** The read with `ticket` landed, as of `cursor`: null when a newer read is out (drop this one), else the events to put
 * over its rows (the held ones newer than it; all of them when the read gave no cursor). */
function taskFeedLanded(feed: TaskFeed, ticket: number, cursor?: string | null): LiveTaskEvent[] | null {
  if (ticket !== feed.ticket) return null;
  const seq = taskCursorSeq(cursor), held = feed.held || [];
  feed.held = null;
  if (seq) feed.seq = seq;
  return held.filter(d => !seq || !taskEventSeq(d) || taskEventSeq(d) > seq);
}
/** The read with `ticket` failed: nothing is held for it any longer. */
function taskFeedFailed(feed: TaskFeed, ticket: number): void {
  if (ticket === feed.ticket) feed.held = null;
}
/** A list row as a list of its own kind holds it: the server's rows are those rows (TaskBrief), read untyped. */
const taskAsRow = <T extends TaskLiveRow>(t: TaskBriefV3): T => t as unknown as T;
/** A list read page by page: whether the server holds more, and the last row it gave. With `after` (keyset paging, the
 * Tasks page) the next page starts after that row's sort key, as the server gave it; else at an `offset` counted from
 * the rows held. */
interface TaskPaging<T extends TaskLiveRow> { more: boolean; edge: T | null; after?: string }
/** The next page of a list held in `rows` (kept by `has`, in `order`, the order its `query` asks the server for). It
 * reads on from the held rows that sort at or before the last row the server gave, so a task that came or left while
 * the list was open shifts nothing; the page goes in by `has` and `order`, and `paging` moves on. Answers the events
 * that came while the read was out (to put over the rows), 'left' when `still()` says the list is no longer wanted,
 * or null when a newer read is out. A failed read throws. */
async function taskPageNext<T extends TaskLiveRow>(paging: TaskPaging<T>, rows: T[], query: TaskBriefQuery, feed: TaskFeed,
  has: (t: T) => boolean, order: TaskRowOrder, still: () => boolean): Promise<LiveTaskEvent[] | 'left' | null> {
  const edge = paging.edge;
  if (!paging.more || !edge) return [];
  const start = paging.after !== undefined ? {after: paging.after} : {offset: rows.filter(t => order(t, edge) <= 0).length};
  const ticket = taskFeedRead(feed);
  let page: Awaited<ReturnType<typeof taskBriefsPage>>;
  try { page = await taskBriefsPage({...query, ...start}); } catch (e) { taskFeedFailed(feed, ticket); throw e; }
  if (!still()) { taskFeedFailed(feed, ticket); return 'left'; }
  const held = taskFeedLanded(feed, ticket, page.cursor);
  if (!held) return null;
  for (const t of page.tasks) taskRowsPut(rows, taskAsRow<T>(t), String(t.id), has, order);
  const last = page.tasks.at(-1);
  paging.more = page.more && !!page.tasks.length;
  paging.edge = last ? taskAsRow<T>(last) : null;
  if (paging.after !== undefined) paging.after = page.next;
  return held;
}
/** Catches `rows` up from `cursor` (taskChanges under the list's own `query`): the rows that changed go through
 * taskRowsPut, the ones `gone` come out, then the events that came meanwhile go over them again (`feed`). `still()` is
 * asked once the answer is in: false drops it (the page was left). Answers the next cursor (`again` when a held event
 * was a bulk change: catch up once more), 'reset' when only a full read will do (no cursor, or too old), or null. */
async function taskRowsCatchUp<T extends TaskLiveRow>(rows: T[], cursor: string, query: Omit<TaskBriefQuery, 'limit' | 'offset' | 'sort' | 'after'>,
  has: (t: T) => boolean, still: () => boolean, feed: TaskFeed, order?: TaskRowOrder): Promise<{cursor: string; moved: boolean; again?: boolean} | 'reset' | null> {
  if (!cursor) return 'reset';
  const ticket = taskFeedRead(feed);
  let out: TaskChanges;
  try { out = await taskChanges(query, cursor); } catch (e) { taskFeedFailed(feed, ticket); throw e; }
  if (!still()) { taskFeedFailed(feed, ticket); return null; }
  if (out.reset) { taskFeedFailed(feed, ticket); return 'reset'; }
  const held = taskFeedLanded(feed, ticket, out.cursor || cursor);
  if (!held) return null;
  let moved = false;
  for (const id of out.gone) moved = taskRowsPut(rows, null, id, has) || moved;
  for (const t of out.tasks) moved = taskRowsPut(rows, taskAsRow<T>(t), String(t.id), has, order) || moved;
  const after = taskRowsLive(rows, held, has, order);
  return {cursor: out.cursor || cursor, moved: moved || after === true, ...(after === 'bulk' ? {again: true} : {})};
}
async function needsYou(): Promise<NeedsEntry[]> {
  const res = await get<{items?: NeedsEntry[]}>('/v2/needs-you', {fresh: true});
  return res.items || [];
}

// ---- writes on a task
interface TaskNew { title: string; body: string; owner: string; pipeline?: string; parent?: string; labels?: string[]; private?: boolean; }
const taskCreate = (b: TaskNew): Promise<TaskV3> => post('/v2/tasks', b, newRequestId()).then((r: {task: TaskV3}) => r.task);
const taskEdit = (id: string, version: number | null, fields: Record<string, unknown>): Promise<TaskV3> =>
  post(taskPath(id), withVersion(version, fields)).then((r: {task?: TaskV3} & TaskV3) => r.task || r);
const taskDone = (id: string, version: number | null, note?: string): Promise<TaskMoved> =>
  post(`${taskPath(id)}/done`, withVersion(version, note ? {note} : {}), newRequestId());
/** `to`: next, done, closed, reopen, or a stage's id or name. `why` pushes past what is missing. */
const taskMove = (id: string, to: string, version: number | null, why?: string): Promise<TaskMoved> =>
  post(`${taskPath(id)}/move`, withVersion(version, why ? {to, why} : {to}), newRequestId());
/** A person lets `whom` see a private task and moves it on (the stuck card's one fix, task #392). Refused, adding
    nobody, when the task now waits on someone else. */
const taskLetSee = (id: string, whom: string[], version: number | null): Promise<TaskMoved> =>
  post(`${taskPath(id)}/move`, withVersion(version, {to: 'next', let_see: whom}), newRequestId());
/** `setAnyway`: a person gives it past a stage rule that refused this owner (409 task_stage_rule). */
const taskReassign = (id: string, owner: string, version: number | null, why?: string, setAnyway?: boolean): Promise<TaskMoved> =>
  post(`${taskPath(id)}/owner`, withVersion(version, {owner, ...(why ? {why} : {}), ...(setAnyway ? {set_anyway: true} : {})}), newRequestId());
/** `until`: an ISO date or date-time; null snoozes with no end. */
const taskSnooze = (id: string, until: string | null, version: number | null, why?: string): Promise<TaskMoved> =>
  post(`${taskPath(id)}/snooze`, withVersion(version, {...(until ? {until} : {}), ...(why ? {why} : {})}), newRequestId());
const taskUnsnooze = (id: string, why?: string): Promise<TaskMoved> => post(`${taskPath(id)}/unsnooze`, why ? {why} : {}, newRequestId());
/** A person hands a task back to its owner to try again. */
const taskTryAgain = (id: string, why?: string): Promise<TaskMoved> => post(`${taskPath(id)}/try-again`, why ? {why} : {}, newRequestId());

// ---- asks
interface TaskAskNew { to: string; line: string; kind?: 'answer' | 'ok'; choices?: string[]; show?: string[]; }
const taskAsk = (id: string, b: TaskAskNew, version: number | null): Promise<TaskMoved> =>
  post(`${taskPath(id)}/asks`, withVersion(version, {...b}), newRequestId());
const taskWithdrawAsk = (id: string, rid: string, why?: string): Promise<TaskMoved> =>
  writeRequest('DELETE', `/v2/tasks/${encodeURIComponent(id)}/asks/${encodeURIComponent(rid)}`, why ? {why} : {});
/** ok, no (send back, with a note), again (look again), or the answer to a question. On a page, `pageVersion` is the
    page the person was shown (`<file id>@<version>`): refused when another is attached now. */
const taskAnswer = (id: string, rid: string, value: string, version: number | null, note?: string, pageVersion?: string): Promise<TaskMoved> =>
  post(`${askPath(id, rid)}/answer`, withVersion(version, {value, ...(note ? {note} : {}), ...(pageVersion ? {page_version: pageVersion} : {})}), newRequestId());
const taskGiveAsk = (id: string, rid: string, to: string, version: number | null): Promise<TaskMoved> =>
  post(`${askPath(id, rid)}/give`, withVersion(version, {to}), newRequestId());
const taskThumbs = (id: string, rid: string, up: boolean, note?: string): Promise<TaskMoved> =>
  post(`${askPath(id, rid)}/thumbs`, note ? {up, note} : {up}, newRequestId());
const answerUndo = (answerId: string): Promise<TaskMoved> => post(`/v2/answers/${encodeURIComponent(answerId)}/undo`, {}, newRequestId());

// ---- an OK bound to what was shown
/** What an OK is for, exactly as asked, and the hash of it to send back. Opening it records that it was shown. */
const okShown = (rid: string): Promise<OkShown> => get<OkShown>(`/v2/oks/${encodeURIComponent(rid)}`, {fresh: true});
/** The person's own OK of what they were shown: `shownHash` is the hash of the preview on the screen, never a newer
    one. A refusal (the content changed: 409) is the caller's to show; this is never retried with another hash. */
const okGive = (taskId: string, rid: string, shownHash: string): Promise<TaskMoved> =>
  post(`${askPath(taskId, rid)}/ok`, {shown_hash: shownHash}, newRequestId());

// ---- pipelines
async function pipelinesAll(): Promise<PipelineRec[]> {
  const res = await get<{pipelines: PipelineRec[]}>('/v2/pipelines');
  return res.pipelines || [];
}
/** One pipeline's settings record (the server answers {pipeline: record}, to a read and to a save). */
const pipelineRec = async (id: string): Promise<PipelineRec> =>
  (await get<{pipeline: PipelineRec}>(`/v2/pipelines/${encodeURIComponent(id)}`, {fresh: true})).pipeline;
const pipelineSave = async (id: string, rec: Omit<PipelineRec, 'id'> | PipelineRec): Promise<PipelineRec> =>
  ((await post(`/v2/pipelines/${encodeURIComponent(id)}`, rec, newRequestId())) as {pipeline: PipelineRec}).pipeline;
const pipelineRetire = (id: string, settingsVersion: number): Promise<unknown> =>
  post(`/v2/pipelines/${encodeURIComponent(id)}/retire`, {settings_version: settingsVersion}, newRequestId());
