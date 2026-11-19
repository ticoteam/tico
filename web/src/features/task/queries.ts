import { queryOptions, type QueryClient, type QueryKey } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import type { NeedsYou, OkPreview, PagePublished, PageShown, PipelineSettingsList, PublishPageBody, TaskDetail, TaskFileList, TaskLinkView, TaskResult } from '@/api/schema';
import { keys, queryClient, QUIET_STALE_MS, quietRoot, write } from '@/data';
import type { AccessAudience, FileLibrary, FileSearch, Group, TaskFileResult, TaskFileView } from '@/api/schema';

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
};

export interface TaskChild {
  id: string;
  title: string;
  status: string;
  owner_id: string | null;
  label?: string | null;
  step?: string | null;
}

function flat(d: TaskDetail): TaskData {
  return {
    ...d.task,
    runs: d.runs ?? [],
    parent: d.parent ?? null,
    children: (d.children as TaskChild[] | undefined) ?? [],
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
    queryKey: ['tasks', 'ref', param] as const,
    queryFn: async ({ signal }) => {
      const t = await readTask(refOf(param), signal);
      queryClient.setQueryData(keys.tasks.detail(t.id), t);
      return t.id;
    },
    retry: (n, e) => !(e instanceof ApiError && (e.status === 403 || e.status === 404)) && n < 1,
  });

export const taskDetailQuery = (id: string | undefined) =>
  queryOptions({
    queryKey: keys.tasks.detail(id ?? ''),
    queryFn: ({ signal }) => readTask(id ?? '', signal),
    enabled: !!id,
  });

/** Load what the first paint needs, from a route's loader. A refusal is the page's to show, so it is not thrown here. */
export async function loadTask(client: QueryClient, param: string): Promise<TaskData | null> {
  try {
    const id = await client.ensureQueryData(taskRefQuery(param));
    return await client.ensureQueryData(taskDetailQuery(id));
  } catch {
    // the page shows not found, no access or the fault
    return null;
  }
}

/** The task's files, pages first (GET /api/v2/tasks/{tid}/files). */
export const taskFilesQuery = (id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(id), 'files'] as const,
    queryFn: async ({ signal }) => (await api.get<TaskFileList>(`${taskPath(id)}/files?all=1`, { signal })).files,
  });

/** One of the task's pages, with the address it is drawn from (GET /api/v2/pages/{fid}). */
export const taskPageQuery = (task: string, file: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'page', file] as const,
    queryFn: async ({ signal }) => (await api.get<PageShown>(`/api/v2/pages/${enc(file)}`, { signal })).page,
  });

/** One version of a page as its text (GET /api/v2/files/{fid}?v=<n>). A version never changes once written. */
export const pageVersionQuery = (file: string, n: number) =>
  queryOptions({
    queryKey: ['page-versions', file, n] as const,
    queryFn: async ({ signal }) => String(await api.get<unknown>(`/api/v2/files/${enc(file)}?v=${n}`, { signal })),
    staleTime: Infinity,
  });

export interface StageChoice {
  id: string;
  name: string;
  side: boolean;
}
export interface PipelineStages {
  id: string;
  name: string;
  stages: StageChoice[];
}

/** Each pipeline's stages, for the stage pick. Its own key: the board keeps its own shape under ['pipelines']. */
export const pipelineStagesQuery = queryOptions({
  queryKey: ['pipelines', 'stages'] as const,
  queryFn: async ({ signal }): Promise<PipelineStages[]> =>
    (await api.get<PipelineSettingsList>('/api/v2/pipelines', { signal })).pipelines.map((p) => ({
      id: p.id,
      name: p.name,
      stages: p.stages.map((s) => ({ id: s.id, name: s.name, side: s.side })),
    })),
  staleTime: QUIET_STALE_MS,
});

export interface Waiting {
  id: string;
  number: number | null;
  pipeline_id: string | null;
}

/** The tasks that wait on the person, in Flow's order (GET /api/v2/needs-you): Flow's next and previous. */
export const waitingQuery = queryOptions({
  queryKey: ['flow', 'waiting'] as const,
  queryFn: async ({ signal }): Promise<Waiting[]> => {
    const r = await api.get<NeedsYou>('/api/v2/needs-you?limit=200', { signal });
    const seen = new Set<string>();
    const out: Waiting[] = [];
    for (const it of 'items' in r ? r.items : []) {
      const t = it.task;
      if (!t || seen.has(t.id)) continue;
      seen.add(t.id);
      out.push({ id: t.id, number: t.number, pipeline_id: t.pipeline_id });
    }
    return out;
  },
  staleTime: QUIET_STALE_MS,
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
  const stages = queryClient.getQueryData<PipelineStages[]>(pipelineStagesQuery.queryKey);
  const shown = (t: TaskData): Fields => {
    const stage = stages?.find((p) => p.id === t.pipeline_id)?.stages.find((s) => s.id === to);
    if (stage && t.stage) return { stage_id: stage.id, stage: { ...t.stage, id: stage.id, name: stage.name, side: stage.side } };
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

/** Approve or send back one requirement. The row leaves Flow at once and comes back if the server refuses. */
export function answerAsk(id: string, rid: string, value: 'ok' | 'no', note?: string): Promise<TaskResult> {
  const answered = (t: TaskData): Fields => ({
    to_leave: (t.to_leave ?? []).map((r) => (r.requirement_id === rid ? { ...r, met: value === 'ok', may: [] } : r)),
    turn: t.turn?.requirement_id === rid ? null : t.turn,
  });
  const gone = (cur: unknown): unknown => {
    if (Array.isArray(cur)) return (cur as Waiting[]).filter((w) => w.id !== id);
    const f = cur as { count?: number; items?: { id: string }[] } | undefined;
    if (!f?.items) return cur;
    const items = f.items.filter((w) => w.id !== id);
    return { ...f, items, count: items.length };
  };
  const flows = queryClient.getQueriesData({ queryKey: ['flow'] }).map(([k]) => k);
  return write<TaskResult>({
    key: [...shownIn(id), ...flows],
    optimistic: (cur) => (Array.isArray(cur) || (cur as { items?: unknown } | undefined)?.items ? gone(cur) : patch(id, answered)(cur)),
    send: (idem) => sendAnswer(id, rid, value, note, idem),
    settle: (cur, answer) => (Array.isArray(cur) || (cur as { items?: unknown } | undefined)?.items ? cur : settled(id)(cur, answer)),
  });
}

/* An OK bound to what was shown is given with the hash of it (GET /api/v2/oks/{rid}); a stage's own OK has no grant
   (404) and is answered with the page version it was asked on (ui/app/decide.ts). */
async function sendAnswer(id: string, rid: string, value: 'ok' | 'no', note: string | undefined, idem: string): Promise<TaskResult> {
  const held = queryClient.getQueryData<TaskData>(keys.tasks.detail(id));
  const req = held?.to_leave?.find((r) => r.requirement_id === rid);
  if (value === 'ok') {
    const shown = await api.get<OkPreview>(`/api/v2/oks/${enc(rid)}`).catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    });
    if (shown) return post(`${taskPath(id)}/asks/${enc(rid)}/ok`, { shown_hash: shown.shown_hash }, idem);
  }
  const page = req?.approval?.page_version ?? req?.page?.page_version;
  return post(
    `${taskPath(id)}/asks/${enc(rid)}/answer`,
    { value, ...(note && { note }), ...(page && { page_version: page }), ...(req?.approval?.visit_id && { visit_id: req.approval.visit_id }) },
    idem,
  );
}

/* What the answer cannot patch in place (a relation named by number, a new subtask, a parent): sent, then the task is
   read again. */
const reread = (id: string) => queryClient.invalidateQueries({ queryKey: keys.tasks.detail(id) });

/** A task as a person names it: its number (412, #412) or its id. */
const named = (other: string) => refOf(other.trim().replace(/^#/, ''));

/** Put the task under `parent` as its subtask. */
export async function setTaskParent(id: string, parent: string) {
  const r = await api.post<TaskResult>(`${taskPath(id)}/parent`, { parent: named(parent) });
  await reread(id);
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
  const r = await api.post<TaskResult>('/api/v2/tasks', { title, parent: id, ...(owner && { owner }) });
  await reread(id);
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

/** Remove a link: it leaves the task at once, and the server's list replaces the task's. */
export function dropLink(id: string, link: string) {
  return write<LinksAnswer>({
    key: keys.tasks.detail(id),
    optimistic: (cur: TaskData | undefined) => withLinks(cur, (was) => was.filter((l) => l.id !== link)),
    send: (idem) => api.post<LinksAnswer>(`${taskPath(id)}/links`, { remove: link }, { idempotencyKey: idem }),
    settle: (cur: TaskData | undefined, answer) => withLinks(cur, () => answer.links),
  });
}

/* ---------------------------------------------------------------- Details' facts and the head's ⋯ (s58) */

import type { GoalResult, TaskMeeting, TaskMeetings } from '@/api/schema';
import { meQuery } from '@/data';

/** How closely someone follows a task (backend/task_followers.py). */
export type FollowLevel = 'live' | 'mentions' | 'digest' | 'muted';
export interface Follower {
  actor: string;
  notify: FollowLevel;
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
    staleTime: QUIET_STALE_MS,
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

/** Follow, unfollow or set a level: the person themselves, or `actor` (PUT /api/v2/tasks/{tid}/followers). */
type FollowersAnswer = { followers: Follower[] | null };
export function followTask(id: string, change: { actor?: string; following: boolean; notify?: FollowLevel }) {
  const who = change.actor ?? queryClient.getQueryData(meQuery.queryKey)?.id ?? '';
  const shown = (list: Follower[]): Follower[] => {
    const rest = list.filter((f) => f.actor !== who);
    if (!change.following) return rest;
    const was = list.find((f) => f.actor === who);
    return [...rest, { actor: who, notify: change.notify ?? was?.notify ?? 'live' }];
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

/** One of the task's pages with what the reader may do to it (GET /api/v2/pages/{fid}): `can_manage` (who can see it,
    archive) and `can_delete`. Its frame draws from the same read. */
export const pageShownQuery = (task: string, file: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'page', file, 'shown'] as const,
    queryFn: ({ signal }) => api.get<PageShown>(`/api/v2/pages/${enc(file)}`, { signal }),
  });

/** The company's teams, for who can see a page (GET /api/v2/groups). */
export const pageGroupsQuery = queryOptions({
  queryKey: ['tasks', 'groups'] as const,
  queryFn: ({ signal }) => api.get<Group[]>('/api/v2/groups', { signal }),
  staleTime: QUIET_STALE_MS,
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

/** Who can see a page: null is everyone who can see where it is filed (PATCH /api/v2/pages/{fid} audience). */
export function setPageAudience(task: string, file: string, audience: AccessAudience | null) {
  return write<PageShown>({
    key: shownKey(task, file),
    optimistic: (cur: PageShown | undefined) => cur && { ...cur, page: { ...cur.page, audience } },
    send: (idem) => api.patch<PageShown>(`/api/v2/pages/${enc(file)}`, { audience }, { idempotencyKey: idem }),
    settle: (cur: PageShown | undefined, answer) => (cur ? { ...cur, page: answer.page } : answer),
  });
}

/** Delete a page and every version for good (DELETE /api/v2/files/{fid}); the task is read again. */
export async function deletePage(task: string, file: string) {
  const r = await api.delete<unknown>(`/api/v2/files/${enc(file)}`);
  await reread(task);
  return r;
}
