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
  settings: Record<string, unknown>;
  can_push_past: boolean;
  who_oks: 'none' | 'people_only' | 'people_with_exceptions';
  person_id: string | null;
  line: string;
}
interface PipelineStageRec {
  id?: string;
  name: string;
  purpose: string;
  owner: string | null;
  by_hand: boolean;
  task_limit: number | null;
  expected_minutes: number | null;
  expected_bots_only: boolean;
  on_enter: Record<string, unknown>;
  requirements: PipelineReqRec[];
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
}
const taskBriefParams = (query: TaskBriefQuery): URLSearchParams => {
  const p = new URLSearchParams({brief: '1'});
  for (const [k, v] of Object.entries(query)) {
    if (v == null || v === '') continue;
    p.set(k, Array.isArray(v) ? v.join(',') : String(v));
  }
  return p;
};
/** One page of list rows and the cursor a later `taskChanges` resumes from. */
async function taskBriefsPage(query: TaskBriefQuery = {}): Promise<{tasks: TaskBriefV3[]; cursor: string}> {
  const res = await get<{tasks?: TaskBriefV3[]; cursor?: string} | TaskBriefV3[]>(`/v2/tasks?${taskBriefParams(query)}`);
  return Array.isArray(res) ? {tasks: res, cursor: ''} : {tasks: res.tasks || [], cursor: res.cursor || ''};
}
async function taskBriefs(query: TaskBriefQuery = {}): Promise<TaskBriefV3[]> {
  return (await taskBriefsPage(query)).tasks;
}
/** What changed since `cursor` under the same filters (GET /v2/tasks?changed_after): the changed rows that still match,
 * the ids that left the filters or became unreadable (`gone`), the next cursor; `reset` when only a full read will do. */
interface TaskChanges { tasks: TaskBriefV3[]; gone: string[]; cursor: string; reset?: boolean }
async function taskChanges(query: Omit<TaskBriefQuery, 'limit' | 'offset' | 'sort'>, cursor: string): Promise<TaskChanges> {
  const p = taskBriefParams(query);
  p.set('changed_after', cursor);
  const res = await get<Partial<TaskChanges>>(`/v2/tasks?${p}`, {fresh: true});
  return {tasks: res.tasks || [], gone: (res.gone || []).map(String), cursor: res.cursor || '', reset: !!res.reset};
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
const taskReassign = (id: string, owner: string, version: number | null, why?: string): Promise<TaskMoved> =>
  post(`${taskPath(id)}/owner`, withVersion(version, why ? {owner, why} : {owner}), newRequestId());
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
/** ok, no (send back, with a note), again (look again), or the answer to a question. */
const taskAnswer = (id: string, rid: string, value: string, version: number | null, note?: string): Promise<TaskMoved> =>
  post(`${askPath(id, rid)}/answer`, withVersion(version, note ? {value, note} : {value}), newRequestId());
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
const pipelineRec = (id: string): Promise<PipelineRec> => get<PipelineRec>(`/v2/pipelines/${encodeURIComponent(id)}`, {fresh: true});
const pipelineSave = (id: string, rec: Omit<PipelineRec, 'id'> | PipelineRec): Promise<PipelineRec> =>
  post(`/v2/pipelines/${encodeURIComponent(id)}`, rec, newRequestId());
const pipelineRetire = (id: string, settingsVersion: number): Promise<unknown> =>
  post(`/v2/pipelines/${encodeURIComponent(id)}/retire`, {settings_version: settingsVersion}, newRequestId());
