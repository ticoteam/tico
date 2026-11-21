import { keepPreviousData, queryOptions, type QueryKey } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import type { OkPreview, PagePublished, PageShown, PipelineSettingsResult, PublishPageBody, TaskDetail, TaskDetailChild, TaskFileList, TaskLinkView, TaskRequirement, TaskResult } from '@/api/schema';
import { keys, meQuery, queryClient, quietRoot, write } from '@/data';
import { flowQuery, pipelinesQuery } from '@/features/tasks/routing';
import type { FileLibrary, FileSearch, TaskFileResult, TaskFileView } from '@/api/schema';

/* One open task: its detail (GET /api/v2/tasks/{tid}, kept current by the stream under keys.tasks.detail), the files
   and pages it shows (under the detail key, re-read when the stream says its files changed), the stages it can move
   to, and what waits on the person (Flow's next and previous). Every change is a write shown first. */

quietRoot('flow');
quietRoot('pipelines');

const enc = encodeURIComponent;
const taskPath = (id: string) => `/api/v2/tasks/${enc(id)}` as const;

/** The task as this view holds it: the task row (with its chat and links) and what its detail adds, flat, so a live
    patch of the row lands. */
export type TaskData = TaskDetail['task'] & {
  runs: NonNullable<TaskDetail['runs']>;
  parent: TaskDetail['parent'];
  children: TaskChild[];
  can_comment: boolean;
  mover: boolean;
  /** The task's note (a legal matter's case in brief): the task read carries it, the schema does not name it yet. */
  note?: string | null;
};

/** A sub-task the reader may open (GET /api/v2/tasks/{tid} `children`); a check's `role` says a second review. */
export type TaskChild = TaskDetailChild;

function flat(d: TaskDetail): TaskData {
  return {
    ...d.task,
    runs: d.runs ?? [],
    parent: d.parent ?? null,
    children: d.children ?? [],
    can_comment: d.can_comment ?? false,
    mover: d.mover ?? false,
  };
}

/** What a person types in the address: a task's number (412) or its id. The server reads `#412` as a number. */
export const refOf = (param: string): string => (/^\d+$/.test(param) ? `#${param}` : param);

async function readTask(ref: string, signal?: AbortSignal): Promise<TaskData> {
  return flat(await api.get<TaskDetail>(taskPath(ref), { signal }));
}

/** The address's task to its id. Reading it fills the detail too, so the page draws from one read. */
export const taskRefQuery = (param: string) =>
  queryOptions({
    queryKey: taskRefKey(param),
    queryFn: async ({ client, signal }) => {
      const t = await readTask(refOf(param), signal);
      client.setQueryData(keys.tasks.detail(t.id), t);
      // The canonical route uses the number. Old links may first resolve by id or include a leading #.
      const canonical = String(t.number ?? t.id);
      if (canonical !== param) client.setQueryData(taskRefKey(canonical), t.id);
      if (t.id !== param && t.id !== canonical) client.setQueryData(taskRefKey(t.id), t.id);
      return t.id;
    },
    retry: (n, e) => !(e instanceof ApiError && (e.status === 403 || e.status === 404)) && n < 1,
  });

const taskRefKey = (param: string) => ['tasks', 'ref', param] as const;

export const taskDetailQuery = (id: string | undefined) =>
  queryOptions({
    queryKey: keys.tasks.detail(id ?? ''),
    queryFn: ({ signal }) => readTask(id ?? '', signal),
    enabled: !!id,
  });

/** The task's files, pages first (GET /api/v2/tasks/{tid}/files). */
export const taskFilesQuery = (id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(id), 'files'] as const,
    queryFn: async ({ signal }) => (await api.get<TaskFileList>(`${taskPath(id)}/files?all=1`, { signal })).files,
  });

/** One version of a file's name, size and type, without its bytes (GET /api/v2/files/{fid}/meta?v=<n>): a version
    a task's listing no longer reaches. A version never changes once written. */
export const fileMetaQuery = (file: string, n: number) =>
  queryOptions({
    queryKey: ['files', 'meta', file, n] as const,
    queryFn: ({ signal }) => api.get<{ name: string; content_type: string; size?: number | null }>(`/api/v2/files/${enc(file)}/meta?v=${n}`, { signal }),
    staleTime: Infinity,
  });

/** One version of a page as its text (GET /api/v2/files/{fid}?v=<n>). A version never changes once written. */
export const pageVersionQuery = (file: string, n: number) =>
  queryOptions({
    queryKey: ['page-versions', file, n] as const,
    queryFn: async ({ signal }) => String(await api.get<unknown>(`/api/v2/files/${enc(file)}?v=${n}`, { signal })),
    staleTime: Infinity,
  });

/** Ended tasks can stay in retired pipelines, which the shared pipeline list filters out. */
export const retiredStagesQuery = (id: string) =>
  queryOptions({
    queryKey: [...pipelinesQuery.queryKey, 'stages', id] as const,
    queryFn: async ({ signal }) => (await api.get<PipelineSettingsResult>(`/api/v2/pipelines/${enc(id)}`, { signal })).pipeline.stages,
  });

/** The tasks that wait on the person, in Flow's order: Flow's next and previous. The same read as Flow's list (GET
    /api/v2/needs-you, one of the slowest), so a task opened from Flow does not read it a second time. */
export const waitingQuery = queryOptions({
  ...flowQuery,
  select: ({ items }) => items.map(({ id, number, pipeline_id }) => ({ id, number, pipeline_id })),
});

/* ---------------------------------------------------------------- writes */

type Fields = Partial<TaskData>;

/** Every query this task shows in: its detail and each list holding it. */
function shownIn(id: string): QueryKey[] {
  const lists = queryClient
    .getQueriesData<{ tasks?: { id: string }[] }>({ queryKey: ['tasks', 'list'] })
    .filter(([, d]) => d?.tasks?.some((t) => t.id === id))
    .map(([k]) => k);
  return [keys.tasks.detail(id), ...lists];
}

/** Changes the task wherever a key holds it: the detail itself, or a row of a list. */
const patch =
  (id: string, fields: Fields | ((t: TaskData) => Fields)) =>
  (cur: unknown): unknown => {
    const put = <T extends { id: string }>(t: T): T => (t.id === id ? { ...t, ...(typeof fields === 'function' ? fields(t as unknown as TaskData) : fields) } : t);
    if (!cur || typeof cur !== 'object') return cur;
    const list = cur as { tasks?: { id: string }[] };
    if (Array.isArray(list.tasks)) return { ...list, tasks: list.tasks.map(put) };
    return put(cur as { id: string });
  };

/** The server's answer in place of what was shown: the task as it is now. */
const settled = (id: string) => (cur: unknown, answer: TaskResult) => patch(id, answer.task as Fields)(cur);

function change(id: string, fields: Fields | ((t: TaskData) => Fields), send: (idem: string) => Promise<TaskResult>): Promise<TaskResult> {
  return write<TaskResult>({ key: shownIn(id), optimistic: patch(id, fields), send, settle: settled(id) });
}

const post = (path: `/api/${string}`, body: object, idem: string) => api.post<TaskResult>(path, body, { idempotencyKey: idem });

export const renameTask = (id: string, title: string) => change(id, { title }, (idem) => post(taskPath(id), { title }, idem));

/** `due` is a day (YYYY-MM-DD) or null to clear it. The old app sets 5 PM local on that day. */
export function setTaskDue(id: string, due: string | null) {
  const at = due ? new Date(`${due}T17:00:00`).toISOString() : null;
  return change(id, { due_at: at }, (idem) => post(taskPath(id), { due: at ?? '' }, idem));
}

export const tagTask = (id: string, labels: string[]) => change(id, { labels }, (idem) => post(taskPath(id), { labels }, idem));

/** `to`: a stage's id, or next, done, closed or reopen. `why` moves it past what is missing. */
export function moveTask(id: string, to: string, why?: string) {
  const pipelines = queryClient.getQueryData(pipelinesQuery.queryKey);
  const shown = (t: TaskData): Fields => {
    const stages = pipelines?.find((p) => p.id === t.pipeline_id)?.stages ?? queryClient.getQueryData(retiredStagesQuery(t.pipeline_id ?? '').queryKey);
    const stage = stages?.find((s) => s.id === to);
    if (stage && t.stage) return { stage_id: stage.id, stage: { ...t.stage, id: stage.id, name: stage.name, side: !!stage.side } };
    if (to === 'closed') return { status: 'closed' };
    if (to === 'done') return { status: 'done' };
    if (to === 'reopen') return { status: 'open' };
    return {};
  };
  return change(id, shown, (idem) => post(`${taskPath(id)}/move`, why ? { to, why } : { to }, idem));
}

export const giveTask = (id: string, owner: string, setAnyway?: boolean) =>
  change(id, { owner_id: owner }, (idem) => post(`${taskPath(id)}/owner`, { owner, ...(setAnyway && { set_anyway: true }) }, idem));

export const markDone = (id: string) => change(id, { status: 'done' }, (idem) => post(`${taskPath(id)}/done`, {}, idem));

/** `until`: an ISO time the task wakes at; null wakes it now (ui/app/task-props.ts snoozeMenu, Unsnooze). */
export const snoozeTask = (id: string, until: string | null) =>
  until
    ? change(id, { snoozed: true }, (idem) => post(`${taskPath(id)}/snooze`, { until }, idem))
    : change(id, { snoozed: false, snooze: null }, (idem) => post(`${taskPath(id)}/unsnooze`, {}, idem));

/** Approve or ask changes on one requirement. The row leaves Flow at once and comes back if the server refuses.
    `shown` is the hash of what the person was shown (decisionShownQuery): an OK bound to it is given with it; null
    answers a stage's own OK, which has no grant, with the page version it was asked on (ui/app/decide.ts). */
/** The id an answer shows under while it is sent: the server's own takes its place (answeringOf). */
export const PENDING_ANSWER = 'pending';

export function answerAsk(id: string, rid: string, value: 'ok' | 'no', note?: string, shown?: string | null, key?: AnswerKey): Promise<TaskResult> {
  const me = queryClient.getQueryData(meQuery.queryKey)?.id ?? '';
  const answered = (t: TaskData): Fields => ({
    to_leave: (t.to_leave ?? []).map((r) =>
      r.requirement_id === rid ? { ...r, met: value === 'ok', may: [], answer: { id: PENDING_ANSWER, value, note: note ?? null, by: me, at: new Date().toISOString() } } : r,
    ),
    turn: t.turn?.requirement_id === rid ? null : t.turn,
  });
  const gone = (cur: unknown): unknown => {
    const f = cur as { count?: number; items?: { id: string }[]; requests?: unknown[] } | undefined;
    if (!f?.items) return cur;
    const items = f.items.filter((w) => w.id !== id);
    // Flow's requests (access, key, key-drop) count too: answering a task's ask leaves them waiting.
    return { ...f, items, count: items.length + (f.requests?.length ?? 0) };
  };
  const flows = queryClient.getQueriesData({ queryKey: ['flow'] }).map(([k]) => k);
  return write<TaskResult>({
    key: [...shownIn(id), ...flows],
    optimistic: (cur) => (Array.isArray(cur) || (cur as { items?: unknown } | undefined)?.items ? gone(cur) : patch(id, answered)(cur)),
    send: (idem) => (value === 'ok' && shown ? post(`${taskPath(id)}/asks/${enc(rid)}/ok`, { shown_hash: shown }, idem) : sendAnswer(id, rid, value, note, idem, key)),
    settle: (cur, answer) => (Array.isArray(cur) || (cur as { items?: unknown } | undefined)?.items ? cur : settled(id)(cur, answer)),
  });
}

/** The version an answer on a page is given on, as the person sees it: `page_version` (`<file id>@<version>`, the whole
    submission's key for several pages) and the visit it is asked on (a document approval's, else the task's `visit`).
    Undefined for a requirement with no page. */
export type AnswerKey = { page_version: string; visit_id?: string };
export function answerKey(req: TaskRequirement | undefined, visit?: string | null): AnswerKey | undefined {
  const one = req?.approval?.file_id ?? req?.page?.file_id;
  const page = req?.approval?.page_version ?? req?.page?.page_version ?? (one ? `${one}@${req?.approval?.version ?? req?.page?.version}` : undefined);
  const on = req?.approval?.visit_id ?? visit;
  return page ? { page_version: page, ...(on && { visit_id: on }) } : undefined;
}

/* A plain answer: a stage's own OK (no grant) with the page version it was asked on, or changes asked with a note. A page
 * several people approve settles on its first answer, so a send back names the version the person saw (`key`, kept
 * when its form opened). */
function sendAnswer(id: string, rid: string, value: 'ok' | 'no', note: string | undefined, idem: string, key?: AnswerKey): Promise<TaskResult> {
  const held = queryClient.getQueryData<TaskData>(keys.tasks.detail(id));
  const on = key ?? answerKey(held?.to_leave?.find((r) => r.requirement_id === rid), held?.visit_id);
  return post(`${taskPath(id)}/asks/${enc(rid)}/answer`, { value, ...(note && { note }), ...on }, idem);
}

/** What the decision binds Approve to (plan/approvals.html): the hash of exactly what was shown. An OK's own preview
    (GET /api/v2/oks/{rid}) gives it, or an email's (GET /api/v2/email/sends/{sid}, which the Email tab reads too).
    A stage's own OK has no grant (404): `hash` is null and Approve answers with the page version. `complete` is false
    when part of what it shows did not load, and Approve stays off. Read again at each new version of the task, so a
    change while it is open shows. `send` is the email the requirement shows: a stage's own OK that shows one is as
    complete as that email's preview, so Approve never sends an email the person could not read. */
export const decisionShownQuery = (task: string, rid: string, version: number, send?: string | null) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'shown', rid, version, send ?? null] as const,
    queryFn: async ({ signal }): Promise<{ hash: string | null; complete: boolean }> => {
      const shown = await api.get<OkPreview>(`/api/v2/oks/${enc(rid)}`, { signal }).catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 404) return null;
        throw e;
      });
      if (!shown) {
        if (!send) return { hash: null, complete: true };
        const mail = await queryClient.fetchQuery({ ...emailSendQuery(task, send), staleTime: 0 });
        return { hash: null, complete: mail.preview_complete };
      }
      if (!shown.subject.startsWith('email_send:')) return { hash: shown.shown_hash, complete: true };
      const mail = await queryClient.fetchQuery({ ...emailSendQuery(task, shown.subject.slice(11)), staleTime: 0 });
      return { hash: mail.preview_complete ? (mail.shown_hash ?? null) : null, complete: mail.preview_complete && !!mail.shown_hash };
    },
    placeholderData: keepPreviousData,
  });

/** Request changes (plan/approvals.html): the request is answered `no` with the person's words, which marks it Changes
    asked and takes it out of Flow until a new version. The server posts the words in the task's chat ("<Name> sent
    this back: <note>"), so the agent hears them once. */
export const requestChanges = (id: string, rid: string, note: string, key?: AnswerKey): Promise<TaskResult> => answerAsk(id, rid, 'no', note, undefined, key);

/** Take an answer back (POST /api/v2/answers/{aid}/undo): the task is read again, and Flow with it. */
export async function undoAnswer(id: string, answer: string): Promise<TaskResult> {
  const r = await api.post<TaskResult>(`/api/v2/answers/${enc(answer)}/undo`, {});
  queryClient.setQueryData(keys.tasks.detail(id), settled(id)(queryClient.getQueryData(keys.tasks.detail(id)), r));
  void queryClient.invalidateQueries({ queryKey: ['flow'] });
  return r;
}

/* What the answer cannot patch in place (a relation named by number, a new subtask, a parent): sent, then the task is
   read again. */
const reread = (id: string) => queryClient.invalidateQueries({ queryKey: keys.tasks.detail(id) });

/** A task as a person names it: its number (412, #412) or its id. */
const named = (other: string) => refOf(other.trim().replace(/^#/, ''));

/** The task a person named (its number or id) read again, wherever this view holds it. */
const rereadNamed = (other: string) => {
  const ref = other.trim().replace(/^#/, '');
  return queryClient.invalidateQueries({
    queryKey: ['tasks', 'detail'],
    predicate: (q) => q.queryKey[2] === ref || String((q.state.data as TaskData | undefined)?.number ?? '') === ref,
  });
};

/** Put a task (as a person names it) under `parent` as its subtask: both are read again, the one for its parent, the
    other for its subtasks. */
export async function setTaskParent(id: string, parent: string) {
  const r = await api.post<TaskResult>(`${taskPath(named(id))}/parent`, { parent: named(parent) });
  await Promise.all([reread(r.task.id), rereadNamed(parent)]);
  return r;
}

export type RelationKind = 'related' | 'depends_on' | 'blocks';

/** Relate another task: related, depends_on (it is blocked by the other), or blocks (the other is blocked by it). */
export async function relateTask(id: string, other: string, kind: RelationKind, remove = false) {
  const r =
    kind === 'blocks'
      ? await api.post<TaskResult>(`${taskPath(named(other))}/relations`, { task: id, kind: 'depends_on', ...(remove && { remove }) })
      : await api.post<TaskResult>(`${taskPath(id)}/relations`, { task: named(other), kind, ...(remove && { remove }) });
  await reread(id);
  return r;
}

/** A new task filed under this one, given to this one's owner until someone gives it on. */
export async function addSubtask(id: string, title: string, owner?: string | null) {
  const r = await api.post<TaskResult>('/api/v2/tasks', { title, parent: named(id), ...(owner && { owner }) });
  await rereadNamed(id);
  return r;
}

/** Ask a person or an agent for an OK, as a requirement of the stage. */
export async function askSomeone(id: string, to: string, line: string) {
  const version = queryClient.getQueryData<TaskData>(keys.tasks.detail(id))?.version;
  const r = await api.post<TaskResult>(`${taskPath(id)}/asks`, { to, line, kind: 'ok', ...(version != null && { version }) });
  queryClient.setQueryData(keys.tasks.detail(id), settled(id)(queryClient.getQueryData(keys.tasks.detail(id)), r));
  return r;
}

/** A page's file name from its title, as the old app made it. */
const pageName = (title: string) =>
  `${
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'page'
  }.html`;

/** A new page on the task, from a title and its text (POST /api/v2/pages, as the task's related page). */
export async function newTaskPage(id: string, title: string, text: string): Promise<PagePublished> {
  const r = await api.post<PagePublished>('/api/v2/pages', { title, text, task: id, as: 'related', name: pageName(title) } satisfies PublishPageBody);
  await reread(id);
  return r;
}

/** The links the server answers with after a change (POST /api/v2/tasks/{tid}/links; the answer is not in the schema). */
type LinksAnswer = { links: TaskLinkView[] };
const withLinks = (cur: TaskData | undefined, links: (was: TaskLinkView[]) => TaskLinkView[]) => cur && { ...cur, links: links(cur.links ?? []) };

/** Add a link: it shows on the task at once, and the server's list replaces it. */
export function linkTask(id: string, url: string, title?: string) {
  const shown: TaskLinkView = { id: `pending:${url}`, kind: 'url', url, title: title ?? null, task_id: id, created_at: new Date().toISOString() };
  return write<LinksAnswer>({
    key: keys.tasks.detail(id),
    optimistic: (cur: TaskData | undefined) => withLinks(cur, (was) => [...was, shown]),
    send: (idem) => api.post<LinksAnswer>(`${taskPath(id)}/links`, { url, ...(title && { title }) }, { idempotencyKey: idem }),
    settle: (cur: TaskData | undefined, answer) => withLinks(cur, () => answer.links),
  });
}

/** Remove a link: it leaves the task once the server has taken it, and the server's list replaces the task's. */
export function dropLink(id: string, link: string) {
  return write<LinksAnswer>({
    key: keys.tasks.detail(id),
    wait: true,
    optimistic: (cur: TaskData | undefined) => withLinks(cur, (was) => was.filter((l) => l.id !== link)),
    send: (idem) => api.post<LinksAnswer>(`${taskPath(id)}/links`, { remove: link }, { idempotencyKey: idem }),
    settle: (cur: TaskData | undefined, answer) => withLinks(cur, () => answer.links),
  });
}

/* ---------------------------------------------------------------- Details' facts and the head's ⋯ (s58) */

import type { GoalResult, TaskMeeting, TaskMeetings } from '@/api/schema';

/** Someone following a task (backend/task_followers.py). */
export interface Follower {
  actor: string;
}
/** The detail's task as Details reads its facts: its followers (null for a task with no thread of its own) and its
    goal, which the schema's task leaves out. */
export type TaskFacts = TaskData & { followers?: Follower[] | null; goal_id?: string | null };

/** Who may see it: its team, the public roadmap too, or only its people. */
export type Visible = 'team' | 'public' | 'private';
export const visibleOf = (t: Pick<TaskData, 'private' | 'visibility'>): Visible => (t.private ? 'private' : t.visibility === 'public' ? 'public' : 'team');

/** The meetings the task was in, newest first (GET /api/v2/tasks/{tid}/meetings). One the person may not read is none. */
export const taskMeetingsQuery = (id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(id), 'meetings'] as const,
    queryFn: async ({ signal }): Promise<TaskMeeting[]> => {
      try {
        const r = await api.get<TaskMeetings>(`${taskPath(id)}/meetings?limit=20`, { signal });
        return [...(r.meetings ?? [])].sort((a, b) => Date.parse(b.started_at) - Date.parse(a.started_at));
      } catch (e) {
        if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return [];
        throw e;
      }
    },
  });

quietRoot('task-goal');

/** A goal's title, for the Goal fact (GET /api/v2/goals/{gid}). */
export const taskGoalQuery = (goal: string) =>
  queryOptions({
    queryKey: ['task-goal', goal] as const,
    queryFn: async ({ signal }) => (await api.get<GoalResult>(`/api/v2/goals/${enc(goal)}`, { signal })).goal.title,
  });

/** A field edit (POST /api/v2/tasks/{tid}): shown at once, the server's task in its place. */
const edit = (id: string, fields: Fields | ((t: TaskData) => Fields), body: object) => change(id, fields, (idem) => post(taskPath(id), body, idem));

/** The person accountable for it, whoever owns it; null takes them off. */
export const setTaskHuman = (id: string, human: string | null) => edit(id, { human_id: human }, { human: human ?? '' });

export const setTaskSize = (id: string, size: number) => edit(id, { size }, { size });

/** The people in one role, replacing who was in it; none takes the role off. */
export function setTaskRoles(id: string, role: string, people: string[]) {
  const shown = (t: TaskData): Fields => {
    const roles = { ...(t.roles ?? {}), [role]: people };
    if (!people.length) delete roles[role];
    return { roles };
  };
  return edit(id, shown, { roles: { [role]: people } });
}

/** The public roadmap's title or summary. */
export const setTaskPublic = (id: string, field: 'public_title' | 'public_summary', text: string) => edit(id, { [field]: text }, { [field]: text });

/** Team, Public roadmap or Private (ui/app/task-props.ts, Visible to). Out of private, then onto the roadmap, is two saves. */
export async function setTaskVisible(id: string, to: Visible) {
  const held = queryClient.getQueryData<TaskData>(keys.tasks.detail(id));
  const now = held ? visibleOf(held) : 'team';
  if (to === 'private') return edit(id, { private: true }, { private: true });
  if (now === 'private') {
    const r = await edit(id, { private: false }, { private: false });
    if (to === 'team') return r;
  }
  const visibility = to === 'public' ? 'public' : 'private';
  return edit(id, { visibility }, { visibility });
}

/** Who else sees a private task (POST /api/v2/tasks/{tid}/readers). */
type ReadersAnswer = { private: boolean; readers: string[] };
export function setTaskReaders(id: string, add: string[], remove: string[]) {
  return write<ReadersAnswer>({
    key: keys.tasks.detail(id),
    optimistic: (cur: TaskData | undefined) => cur && { ...cur, readers: [...new Set([...(cur.readers ?? []), ...add])].filter((r) => !remove.includes(r)) },
    send: (idem) => api.post<ReadersAnswer>(`${taskPath(id)}/readers`, { add, remove }, { idempotencyKey: idem }),
    settle: (cur: TaskData | undefined, answer) => cur && { ...cur, readers: answer.readers, private: answer.private },
  });
}

/** Follow or unfollow: the person themselves, or `actor` (PUT /api/v2/tasks/{tid}/followers). */
type FollowersAnswer = { followers: Follower[] | null };
export function followTask(id: string, change: { actor?: string; following: boolean }) {
  const who = change.actor ?? queryClient.getQueryData(meQuery.queryKey)?.id ?? '';
  const shown = (list: Follower[]): Follower[] => {
    const rest = list.filter((f) => f.actor !== who);
    return change.following ? [...rest, { actor: who }] : rest;
  };
  return write<FollowersAnswer>({
    key: keys.tasks.detail(id),
    optimistic: (cur: TaskFacts | undefined) => cur && { ...cur, followers: shown(cur.followers ?? []) },
    send: (idem) => api.put<FollowersAnswer>(`${taskPath(id)}/followers`, change, { idempotencyKey: idem }),
    settle: (cur: TaskFacts | undefined, answer) => cur && { ...cur, followers: answer.followers },
  });
}

/** To another pipeline: its stage `stage`, its first when left out. The server's task replaces what was shown. */
export const moveTaskTo = (id: string, pipeline: string, stage?: string, why?: string) =>
  change(id, {}, (idem) => post(`${taskPath(id)}/move`, { to_pipeline: pipeline, ...(stage && { to: stage }), ...(why && { why }) }, idem));

/** To the trash, from which it can be restored; the lists that held it are read again. */
export async function deleteTask(id: string) {
  await api.post(`${taskPath(id)}/delete`, {});
  void queryClient.invalidateQueries({ queryKey: ['tasks', 'list'] });
}

/* ---------------------------------------------------------------- a task's files and pages: add, link, remove, who sees */

/** One of the task's pages with what the reader may do to it (GET /api/v2/pages/{fid}): `can_manage` (archive) and
    `can_delete`. Its frame draws from the same read. */
export const pageShownQuery = (task: string, file: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'page', file, 'shown'] as const,
    queryFn: ({ signal }) => api.get<PageShown>(`/api/v2/pages/${enc(file)}`, { signal }),
  });

export interface PagePick {
  id: string;
  title: string;
}

/** Pages to link to a task (ui/app/task-pages.ts): the newest until something is typed, then the best matches. Only
    pages the person may write are offered. */
export const pagePickQuery = (q: string) =>
  queryOptions({
    queryKey: ['tasks', 'page-pick', q.trim()] as const,
    queryFn: async ({ signal }): Promise<PagePick[]> => {
      const text = q.trim();
      const rows = text
        ? (await api.get<FileSearch>(`/api/v2/files/search?${new URLSearchParams({ q: text, kind: 'page', limit: '20' })}`, { signal })).results
        : (await api.get<FileLibrary>('/api/v2/files?kind=page&limit=8', { signal })).files;
      return rows.filter((r) => r.may_write !== false).map((r) => ({ id: r.id, title: r.title }));
    },
    staleTime: 30_000,
  });

/** A file from the person's disk onto the task (POST /api/v2/tasks/{tid}/files); a name the task has adds a version. */
export async function uploadTaskFile(id: string, file: File): Promise<TaskFileResult> {
  const fd = new FormData();
  fd.append('file', file, file.name);
  const r = await api.post<TaskFileResult>(`${taskPath(id)}/files`, fd);
  await reread(id);
  return r;
}

/** Remove a file from the task's list (PATCH /api/v2/files/{fid} archived), or bring it back. It leaves at once. */
export async function archiveTaskFile(id: string, file: string, archived: boolean) {
  const r = await write<unknown>({
    key: taskFilesQuery(id).queryKey,
    optimistic: (cur: TaskFileView[] | undefined) => cur?.map((f) => (f.id === file ? { ...f, archived } : f)),
    send: (idem) => api.patch(`/api/v2/files/${enc(file)}`, { archived }, { idempotencyKey: idem }),
  });
  await reread(id);
  return r;
}

/** Link a page to the task as related (POST /api/v2/pages/{fid}/links). */
export async function linkTaskPage(id: string, page: string) {
  const r = await api.post<PageShown>(`/api/v2/pages/${enc(page)}/links`, { target: `task:${id}`, role: 'related' });
  await reread(id);
  return r;
}

const shownKey = (task: string, file: string) => pageShownQuery(task, file).queryKey;

/** Archive a page, or restore it (PATCH /api/v2/pages/{fid}). An archived page leaves the task's tabs. */
export async function archivePage(task: string, file: string, archived: boolean) {
  const r = await write<PageShown>({
    key: shownKey(task, file),
    optimistic: (cur: PageShown | undefined) => cur && { ...cur, page: { ...cur.page, archived } },
    send: (idem) => api.patch<PageShown>(`/api/v2/pages/${enc(file)}`, { archived }, { idempotencyKey: idem }),
    settle: (cur: PageShown | undefined, answer) => (cur ? { ...cur, page: answer.page } : answer),
  });
  await reread(task);
  return r;
}

/** Delete a page and every version for good (DELETE /api/v2/files/{fid}); the task is read again. */
export async function deletePage(task: string, file: string) {
  const r = await api.delete<unknown>(`/api/v2/files/${enc(file)}`);
  await reread(task);
  return r;
}

/* ---------------------------------------------------------------- an email the task asks about */

import type { EmailSendDetail } from '@/api/schema';

/** A frozen file of the email (backend/email_content.py `attachments`): `part` names it in its download address. */
export interface EmailFile {
  part: number;
  name: string;
  size: number;
  type?: string;
  /** A version of an archived file of the send's task, and of no file still in use there. */
  archived?: boolean;
}

/** The send's state as a task row carries it: with the day it is held for (the preview's `not_before`). */
const withHeld = (r: { send: MailSend; not_before?: string | null }): MailSend => ({ ...r.send, not_before: r.not_before ?? null });

/** The send as its preview gives it. `reply_to_id` (the Gmail message it answers) is read when the server gives it:
    backend/email_sending.py `public` keeps it on the row but leaves it out today. */
export type MailSend = EmailSendDetail['send'] & { reply_to_id?: string | null; thread_id?: string | null; not_before?: string | null };

/** The preview's answer: what the schema has, and what the server adds when it carries them (PR #4540): the hash the
    OK sends back, and the frozen copy's To, Cc and Bcc apart (`send.recipients` is all of them in one list). */
type EmailPreview = Omit<EmailSendDetail, 'send'> & { send: MailSend; shown_hash?: string | null; to?: string[]; cc?: string[]; bcc?: string[] };

/** The email as it will be sent, frozen (GET /api/v2/email/sends/{sid}). Reading it is what lets the person's OK count:
    the server records that it showed them the whole email. */
export const emailSendQuery = (task: string, send: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'email', send] as const,
    queryFn: async ({ signal }) => {
      const r = await api.get<EmailPreview>(`/api/v2/email/sends/${enc(send)}`, { signal });
      queryClient.setQueryData(sendStateQuery(send).queryKey, withHeld(r));
      return { ...r, files: r.send.attachments as unknown as EmailFile[] };
    },
  });

/** A send's states on its way out once its OK is given: in its undo window, being handed to the mail provider. */
const OUTGOING: readonly string[] = ['pending', 'dispatching'];

/** The backstop read of a send on its way: the stream carries each move, this only catches one it missed. */
export const SEND_BACKSTOP_MS = 5 * 60_000;

/** Where a send is (GET /api/v2/email/sends/{sid}): `pending` until `send_after` (the undo window), then `sent` at
    `finished_at`, or `failed`, `canceled`, `blocked` with `detail`. Read once; each move after that comes on the live
    stream, on its tasks' rows (`email_send`, live/patch.ts), with a slow backstop read while it is on its way (`approved`: its
    OK was just given here, so a send still waiting on it counts as on its way). The email's preview fills it too. */
export const sendStateQuery = (send: string, approved = false) =>
  queryOptions({
    queryKey: keys.emailSend(send),
    queryFn: async ({ signal }): Promise<MailSend> => withHeld(await api.get<EmailPreview>(`/api/v2/email/sends/${enc(send)}`, { signal })),
    refetchInterval: (q) => {
      const state = q.state.data?.state ?? '';
      return OUTGOING.includes(state) || (approved && state === 'waiting_approval') ? SEND_BACKSTOP_MS : false;
    },
    staleTime: SEND_BACKSTOP_MS,
    enabled: !!send,
  });

/** Who an email goes to, copies and blind copies apart once the preview says so; before, every address is under To. */
export function addressesOf(d: Pick<EmailPreview, 'send' | 'to' | 'cc' | 'bcc'>): { to: string[]; cc: string[]; bcc: string[] } {
  const split = [d.to, d.cc, d.bcc].some((x) => !!x?.length);
  return split ? { to: d.to ?? [], cc: d.cc ?? [], bcc: d.bcc ?? [] } : { to: d.send.recipients, cc: [], bcc: [] };
}

/** One received email (backend/mail.py `message_public`, with its body). */
export interface Letter {
  msg_id: string;
  thread_id?: string;
  epoch?: number;
  date?: string;
  from_addr?: string;
  from_header?: string;
  subject?: string;
  snippet?: string;
  body?: string;
}

/** The email a send answers, read live from the mailbox (GET /api/v2/mail/messages/{id}?mailbox=). It needs the
    person's own read of that mailbox and a live mail connection: without either it is simply not shown. */
export const answeredQuery = (task: string, send: string, mailbox: string, id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'email', send, 'answers', id] as const,
    queryFn: ({ signal }) => api.get<Letter>(`/api/v2/mail/messages/${enc(id)}?mailbox=${enc(mailbox)}`, { signal }),
    enabled: !!id && !!mailbox,
    retry: false,
    staleTime: Infinity,
  });

/** Where one of its frozen files downloads from (GET /api/v2/email/sends/{sid}/attachments/{part}). */
export const emailFileUrl = (send: string, part: number) => `/api/v2/email/sends/${enc(send)}/attachments/${part}`;

/* ---------------------------------------------------------------- a Word file, read to draw it */

export interface WordRun {
  text: string;
  b?: boolean;
  i?: boolean;
}
export interface WordPara {
  kind: 'h' | 'li' | 'p';
  runs: WordRun[];
}

const wordOn = (el: Element | null) => !!el && !/^(0|false|none)$/i.test(el.getAttribute('w:val') ?? '');
const wordChild = (el: Element | null | undefined, name: string) => (el ? ([...el.children].find((c) => c.localName === name) ?? null) : null);

/** The paragraphs of a Word file's `word/document.xml`, in order (a table's cells read as their paragraphs). */
export function wordParagraphs(xml: string): WordPara[] {
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  return [...doc.getElementsByTagNameNS('*', 'p')].map((p) => {
    const props = wordChild(p, 'pPr');
    const style = wordChild(props, 'pStyle')?.getAttribute('w:val') ?? '';
    const kind = /^(heading|title)/i.test(style) ? 'h' : wordChild(props, 'numPr') ? 'li' : 'p';
    const runs = [...p.getElementsByTagNameNS('*', 'r')].map((r): WordRun => {
      const rp = wordChild(r, 'rPr');
      const text = [...r.children].map((c) => (c.localName === 't' ? (c.textContent ?? '') : c.localName === 'tab' ? '\t' : c.localName === 'br' ? '\n' : '')).join('');
      return { text, ...(wordOn(wordChild(rp, 'b')) && { b: true }), ...(wordOn(wordChild(rp, 'i')) && { i: true }) };
    });
    return { kind, runs: runs.filter((r) => r.text) };
  });
}

/** One file out of a zip (a .docx is one): its bytes, unpacked with the browser's own DecompressionStream. */
async function unzip(zip: ArrayBuffer, name: string): Promise<string | null> {
  const v = new DataView(zip);
  let end = zip.byteLength - 22;
  while (end >= 0 && v.getUint32(end, true) !== 0x06054b50) end--;
  if (end < 0) return null;
  let at = v.getUint32(end + 16, true);
  for (let n = v.getUint16(end + 10, true); n > 0 && v.getUint32(at, true) === 0x02014b50; n--) {
    const method = v.getUint16(at + 10, true);
    const size = v.getUint32(at + 20, true);
    const nameLen = v.getUint16(at + 28, true);
    const local = v.getUint32(at + 42, true);
    const here = new TextDecoder().decode(new Uint8Array(zip, at + 46, nameLen));
    at += 46 + nameLen + v.getUint16(at + 30, true) + v.getUint16(at + 32, true);
    if (here !== name) continue;
    const start = local + 30 + v.getUint16(local + 26, true) + v.getUint16(local + 28, true);
    const bytes = new Uint8Array(zip, start, size);
    if (method === 0) return new TextDecoder().decode(bytes);
    const out = new Response(bytes).body?.pipeThrough(new DecompressionStream('deflate-raw'));
    return out ? new Response(out).text() : null;
  }
  return null;
}

/** A Word file's paragraphs (word/document.xml), read once: an email's frozen file never changes. */
export const wordQuery = (url: string) =>
  queryOptions({
    queryKey: ['files', 'word', url] as const,
    queryFn: async ({ signal }): Promise<WordPara[]> => {
      const res = await fetch(url, { credentials: 'same-origin', signal });
      if (!res.ok) throw new ApiError(res.status, `http_${res.status}`, res.statusText, res.status >= 500);
      const xml = await unzip(await res.arrayBuffer(), 'word/document.xml');
      if (xml == null) throw new ApiError(0, 'not_word', 'Not a Word file');
      return wordParagraphs(xml);
    },
    staleTime: Infinity,
  });

/* ---------------------------------------------------------------- requests in Needs you */

/** Answer an access request, a key card or a key-drop proposal from Needs you, through its own route: the row stays,
    busy, until the server has taken it, then leaves Flow; every other copy of it settles on the server. */
function answerRequest<T>(item: string, send: (idem: string) => Promise<T>, taken: (out: T) => boolean = () => true): Promise<T> {
  // Read Flow again once answered, refused or not: one answer can settle other cards too (another copy, a Read request).
  const again = () => void queryClient.invalidateQueries({ queryKey: flowQuery.queryKey });
  return write<T>({
    key: flowQuery.queryKey,
    wait: true,
    optimistic: (cur: unknown) => cur,
    // Only an answer that settled it takes the row out: a grant that is still open keeps its card.
    settle: (cur: { count: number; requests?: { id: string }[] } | undefined, out: T) => {
      if (!taken(out) || !cur?.requests?.some((r) => r.id === item)) return cur;
      return { ...cur, count: cur.count - 1, requests: cur.requests.filter((r) => r.id !== item) };
    },
    send,
  }).finally(again);
}

/** An access request as its route answers: where it stands now, whoever settled it, and the level it is for. */
export interface AccessAnswer {
  state: string;
  level: string;
}

/** Grant (`read` for Read only on a request for Read and write) or decline an access request. */
export const grantAccess = (item: string, rid: string, level?: 'read') =>
  answerRequest(
    item,
    (idem) => api.post<AccessAnswer>(`/api/v2/access-requests/${enc(rid)}/grant`, level ? { level } : {}, { idempotencyKey: idem }),
    (out) => out.state !== 'open',
  );
export const declineAccess = (item: string, rid: string) =>
  answerRequest(
    item,
    (idem) => api.post<AccessAnswer>(`/api/v2/access-requests/${enc(rid)}/decline`, {}, { idempotencyKey: idem }),
    (out) => out.state !== 'open',
  );
/** The key the person typed into a key card: it goes to the server alone, never into a message. */
export const saveKey = (item: string, rid: string, value: string) =>
  answerRequest(item, (idem) => api.post(`/api/v2/credential-requests/${enc(rid)}/save`, { value }, { idempotencyKey: idem }));
/** A key proposal as its Grant answers: where it stands, its rows, and each tool's check. */
export interface KeyDropAnswer {
  state: string;
  rows: { key: string; tool_name: string }[];
  results?: { key: string; ok: boolean; detail: string }[];
}

/** Grant the key-drop proposal's rows kept (their places on the card as proposed, `at`: `positions: 'original'`), with the loss preview the card showed (`seen`): the
    server refuses with a 409 when who keeps and loses access moved since. */
export const grantKeyDrop = (item: string, message: string, keep: number[], seen: string) =>
  answerRequest(
    item,
    (idem) => api.post<KeyDropAnswer>(`/api/v2/tools-chat/proposals/${enc(message)}/grant`, { keep, seen, positions: 'original' }, { idempotencyKey: idem }),
    (out) => out.state !== 'proposed',
  );
