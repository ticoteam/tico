import { keepPreviousData, queryOptions, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type {
  CompanyFloor,
  NeedsYou,
  PipelineSettings,
  PipelineSettingsList,
  PipelineSettingsResult,
  Task,
  TaskManyResult,
  TaskMerge,
  TaskMergeResult,
  TaskMoveResult,
  Tag,
  TagDetail,
  TagList,
} from '@/api/schema';
import { keys, queryClient, quietRoot, QUIET_STALE_MS, write } from '@/data';
import { newId } from '@/lib';

// Pipelines, tags and Flow's list have no live topic of their own: read again after the person's writes, on
// reconnect, and when a page opens on a read older than a minute. Task lists are patched by the stream.
quietRoot('pipelines');
quietRoot('flow');
quietRoot('tags');

/* ---------------------------------------------------------------- pipelines */

export interface Stage {
  id: string;
  name: string;
  /** A side stage: a task goes there by hand and comes back to the stage it left. */
  side?: boolean;
}

export interface Pipeline {
  id: string;
  name: string;
  /** Work stages in order: the board's columns, the list's sections. */
  stages: Stage[];
  /** Who owns the pipeline: an actor id, for the head's face. */
  owner: string | null;
  done: Stage | null;
  closed: Stage | null;
  /** The reader may edit its stages. */
  mayChange: boolean;
}

type PipelineRecord = PipelineSettings & {
  owner_id?: string | null;
  manager_id?: string | null;
  retired_at?: string | null;
  may_change?: boolean;
};

function adaptPipeline(p: PipelineRecord): Pipeline {
  return {
    id: p.id,
    name: p.name,
    stages: p.stages.map((s) => ({ id: s.id, name: s.name, side: s.side })),
    owner: p.owner_id ?? p.manager_id ?? null,
    done: p.done ? { id: p.done.id, name: p.done.name } : null,
    closed: p.closed ? { id: p.closed.id, name: p.closed.name } : null,
    mayChange: p.may_change !== false,
  };
}

/** Every pipeline still in use, in the order they were made (GET /api/v2/pipelines). The menu lists these. */
export const pipelinesQuery = queryOptions({
  queryKey: ['pipelines'] as const,
  queryFn: async ({ signal }) => {
    const r = await api.get<PipelineSettingsList>('/api/v2/pipelines', { signal });
    return (r.pipelines as PipelineRecord[]).filter((p) => !p.retired_at).map(adaptPipeline);
  },
  staleTime: QUIET_STALE_MS,
});

/* ---------------------------------------------------------------- one pipeline's open tasks */

/** The fields a board card or a list row reads. The stream patches whole tasks in; these are the ones used. */
export type TaskItem = Pick<
  Task,
  'id' | 'number' | 'title' | 'status' | 'pipeline_id' | 'stage_id' | 'owner_id' | 'labels' | 'late' | 'turn' | 'updated_at' | 'version'
> &
  Partial<Pick<Task, 'stuck' | 'stage_since' | 'snoozed' | 'created_at'>>;

/** The cache's shape for a list: the server's page, kept as is so the stream's patches apply (live/patch.ts). */
export interface TaskPage {
  tasks: TaskItem[];
  cursor: string;
  next_after?: string | null;
}

export interface Filters {
  /** An actor id: human:<id> or bot:<slug>. */
  owner?: string;
  /** A tag's key. */
  tag?: string;
  late?: boolean;
  /** Words in the title, or #N. */
  q?: string;
  /** Whose turn it is: an actor id. */
  waiting?: string;
  /** Who asked for it: an actor id. */
  asked?: string;
  due?: Due;
}

/** Overdue, due within a week (overdue too), or has a date (backend/task_match.py DUE). */
export type Due = 'overdue' | 'week' | 'any';

/** The server's most, one page at a time (backend/task_list_routes.py). */
const PAGE = 500;

/**
 * The list key's params, in the server's words. `pipeline`, `status` and `owner` are what the stream's matcher reads,
 * so a move or a new task lands in place; a search, a tag or Late make it read the list again instead.
 */
export function listParams(pipeline: string, f: Filters): Record<string, string | number> {
  const p: Record<string, string | number> = { pipeline, status: 'open', limit: PAGE };
  if (f.owner) p.owner = f.owner;
  if (f.tag) p.label = f.tag;
  if (f.late) p.late = 'true';
  if (f.q) p.search = f.q;
  if (f.waiting) p.turn = f.waiting;
  if (f.asked) p.requester = f.asked;
  if (f.due) p.due = f.due;
  return p;
}

/** Any filter narrows the list: its rows are not the stage's whole count. */
export const isFiltered = (f: Filters) => !!(f.owner || f.tag || f.late || f.q || f.waiting || f.asked || f.due);

const query = (params: Record<string, string | number>) =>
  new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

export const pipelineTasksQuery = (pipeline: string, f: Filters) => {
  const params = listParams(pipeline, f);
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<TaskPage>(`/api/v2/tasks?${query(params)}`, { signal }),
    // A new filter keeps the rows on screen until its answer lands, instead of flashing the loading bars.
    placeholderData: keepPreviousData,
  });
};

/** Search's tasks: any pipeline's, by title words or #N (the server's `search`), a few. Under the list root, so the
    stream's changes reach it. */
export const taskSearchQuery = (q: string, limit: number) => {
  const params = { search: q, limit, brief: 'true' };
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<TaskPage>(`/api/v2/tasks?${query(params)}`, { signal }),
  });
};

const loading = new Set<string>();

/** The next page of a pipeline's open list, added to the same cache entry (so the stream keeps patching it). */
export const loadMore = (pipeline: string, f: Filters) => loadMoreOf(listParams(pipeline, f));

/** The next page of any list read with these params. */
export async function loadMoreOf(params: Record<string, string | number>): Promise<void> {
  const key = keys.tasks.list(params);
  const id = JSON.stringify(key);
  const have = queryClient.getQueryData<TaskPage>(key);
  if (!have?.next_after || loading.has(id)) return;
  loading.add(id);
  try {
    const page = await api.get<TaskPage>(`/api/v2/tasks?${query({ ...params, after: have.next_after })}`);
    queryClient.setQueryData<TaskPage>(key, (cur) => {
      if (!cur) return cur;
      const seen = new Set(cur.tasks.map((t) => t.id));
      return { ...cur, tasks: [...cur.tasks, ...page.tasks.filter((t) => !seen.has(t.id))], next_after: page.next_after ?? null };
    });
  } finally {
    loading.delete(id);
  }
}

/* ---------------------------------------------------------------- changes */

const listsHolding = (id: string): QueryKey[] =>
  queryClient
    .getQueriesData<TaskPage>({ queryKey: ['tasks', 'list'] })
    .filter(([, d]) => d?.tasks?.some((t) => t.id === id))
    .map(([k]) => k);

const patchRow = (cur: TaskPage | undefined, id: string, change: (t: TaskItem) => TaskItem | null): TaskPage | undefined =>
  cur && {
    ...cur,
    tasks: cur.tasks.flatMap((t) => {
      if (t.id !== id) return [t];
      const next = change(t);
      return next ? [next] : [];
    }),
  };

function findTask(id: string): TaskItem | undefined {
  for (const [, d] of queryClient.getQueriesData<TaskPage>({ queryKey: ['tasks', 'list'] })) {
    const t = d?.tasks?.find((x) => x.id === id);
    if (t) return t;
  }
  return undefined;
}

/** The stage `to` names in the task's pipeline (an id or a name), or null for done, closed or next. */
function stageIn(pipelineId: string | null, to: string): Stage | null {
  const p = queryClient.getQueryData<Pipeline[]>(pipelinesQuery.queryKey)?.find((x) => x.id === pipelineId);
  return p?.stages.find((s) => s.id === to || s.name.toLowerCase() === to.toLowerCase()) ?? null;
}

/**
 * Move a task to a stage: it shows there at once and is taken back if the server refuses (a requirement not met, a
 * newer version). The refusal's words are the error's message.
 */
export async function moveTask(id: string, to: string): Promise<TaskItem> {
  const before = findTask(id);
  const stage = before ? stageIn(before.pipeline_id, to) : null;
  const shown = listsHolding(id);
  const now = new Date().toISOString();
  const { task } = await write<{ task: TaskItem }>({
    key: shown.length ? shown : keys.tasks.detail(id),
    optimistic: (cur: TaskPage | undefined) =>
      stage && cur?.tasks ? patchRow(cur, id, (t) => ({ ...t, stage_id: stage.id, stage_since: now })) : cur,
    send: (idempotencyKey) =>
      api.post<{ task: TaskItem }>(`/api/v2/tasks/${encodeURIComponent(id)}/move`, { to: stage?.id ?? to, version: before?.version }, { idempotencyKey }),
    settle: (cur: TaskPage | undefined, answer) =>
      cur?.tasks ? patchRow(cur, id, (t) => (answer.task.status === t.status ? { ...t, ...answer.task } : null)) : cur,
    revert: (cur: TaskPage | undefined) =>
      before && cur?.tasks ? patchRow(cur, id, (t) => ({ ...t, stage_id: before.stage_id, stage_since: before.stage_since })) : cur,
  });
  return task;
}

export interface NewTask {
  title: string;
  pipeline: string;
  owner?: string | null;
  /** A stage other than the first ("+" in a stage's head): the task is filed, then moved there. */
  stage?: string | null;
}

export interface Created {
  task: TaskItem;
  /** Filed, but the move to the stage asked for was refused: why. */
  notMoved?: unknown;
}

/** File a task: a pending row shows in its stage at once, and becomes the real one when it lands. */
export async function createTask(input: NewTask, lists: QueryKey[]): Promise<Created> {
  const tmp = `tmp:${newId()}`;
  const first = queryClient.getQueryData<Pipeline[]>(pipelinesQuery.queryKey)?.find((p) => p.id === input.pipeline)?.stages[0];
  const into = input.stage ?? first?.id ?? null;
  const now = new Date().toISOString();
  let notMoved: unknown;
  const row: TaskItem = {
    id: tmp,
    number: null,
    title: input.title,
    status: 'open',
    pipeline_id: input.pipeline,
    stage_id: into,
    owner_id: input.owner ?? null,
    labels: [],
    late: null,
    turn: null,
    updated_at: now,
    stage_since: now,
    version: 0,
  };
  const { task } = await write<{ task: TaskItem }>({
    key: lists.length ? lists : keys.tasks.list(listParams(input.pipeline, {})),
    optimistic: (cur: TaskPage | undefined) => cur?.tasks && { ...cur, tasks: [row, ...cur.tasks] },
    send: async (idempotencyKey) => {
      const made = await api.post<{ task: TaskItem }>(
        '/api/v2/tasks',
        { title: input.title, pipeline: input.pipeline, ...(input.owner ? { owner: input.owner } : {}) },
        { idempotencyKey },
      );
      if (!into || made.task.stage_id === into) return made;
      // A new task has no stage of its own choosing: it is filed into the first, then moved. A refused move keeps it.
      try {
        return await api.post<{ task: TaskItem }>(`/api/v2/tasks/${encodeURIComponent(made.task.id)}/move`, { to: into, version: made.task.version });
      } catch (e) {
        notMoved = e;
        return made;
      }
    },
    // The stream's echo may already be in: replace by id so the row never shows twice.
    settle: (cur: TaskPage | undefined, answer) =>
      cur?.tasks && {
        ...cur,
        tasks: cur.tasks.some((t) => t.id === answer.task.id)
          ? cur.tasks.filter((t) => t.id !== tmp)
          : cur.tasks.map((t) => (t.id === tmp ? answer.task : t)),
      },
    revert: (cur: TaskPage | undefined) => cur?.tasks && { ...cur, tasks: cur.tasks.filter((t) => t.id !== tmp) },
  });
  return { task, notMoved };
}

/** The open list keys of one pipeline (not its snoozed list), for a new task to show in. */
export const listsOf = (pipeline: string): QueryKey[] =>
  queryClient
    .getQueriesData<TaskPage>({ queryKey: ['tasks', 'list'] })
    .filter(([k, d]) => {
      const p = k[2] as Record<string, unknown> | undefined;
      return !!d && p?.pipeline === pipeline && p.status === 'open' && !p.snoozed;
    })
    .map(([k]) => k);

/* ---------------------------------------------------------------- many at once: Select, a stage's "⋯" */

const listsHoldingAny = (ids: ReadonlySet<string>): QueryKey[] =>
  queryClient
    .getQueriesData<TaskPage>({ queryKey: ['tasks', 'list'] })
    .filter(([, d]) => d?.tasks?.some((t) => ids.has(t.id)))
    .map(([k]) => k);

/** What did not change, and why: one line, for a toast. Empty when everything changed. */
export interface Skipped {
  n: number;
  why: string;
}

const skippedOf = (skipped: readonly { reason: string }[]): Skipped => ({ n: skipped.length, why: skipped[0]?.reason ?? '' });

/** Every key that shows one of `ids`: each changes at once; a skipped task comes back when its list is read again. */
async function many<T>(ids: readonly string[], change: (t: TaskItem) => TaskItem | null, send: (k: string) => Promise<T>, left: (out: T) => readonly { reason: string }[]) {
  const set = new Set(ids);
  const shown = listsHoldingAny(set);
  const out = await write<T>({
    key: shown.length ? shown : [['tasks', 'list']],
    optimistic: (cur: TaskPage | undefined) =>
      cur?.tasks && { ...cur, tasks: cur.tasks.flatMap((t) => (set.has(t.id) ? [change(t)].filter((x) => x !== null) : [t])) },
    send,
  });
  const skipped = left(out);
  if (skipped.length) for (const k of shown) void queryClient.invalidateQueries({ queryKey: k });
  return skippedOf(skipped);
}

/** Move tasks to a stage of their pipeline, or to `done`, `closed` or `reopen`, in one write. */
export function moveMany(ids: readonly string[], to: string): Promise<Skipped> {
  const now = new Date().toISOString();
  const leaves = to === 'done' || to === 'closed';
  return many(
    ids,
    (t) => (leaves ? null : { ...t, stage_id: to, stage_since: now }),
    (idempotencyKey) => api.post<TaskMoveResult>('/api/v2/tasks/move', { ids, to }, { idempotencyKey }),
    (out) => out.skipped,
  );
}

/** Give tasks to one owner, in one write. */
export function assignMany(ids: readonly string[], owner: string): Promise<Skipped> {
  return many(
    ids,
    (t) => ({ ...t, owner_id: owner }),
    (idempotencyKey) => api.post<TaskManyResult>('/api/v2/tasks/owner', { ids, owner }, { idempotencyKey }),
    (out) => out.skipped,
  );
}

/** Snooze tasks until a time: they leave the board (Archived holds them under Snoozed). */
export function snoozeMany(ids: readonly string[], until: string): Promise<Skipped> {
  return many(
    ids,
    () => null,
    (idempotencyKey) => api.post<TaskManyResult>('/api/v2/tasks/snooze', { ids, until }, { idempotencyKey }),
    (out) => out.skipped,
  );
}

/* ---------------------------------------------------------------- Archived: snoozed, done, closed */

export type Shelf = 'snoozed' | 'done' | 'closed';
const SHELF_PAGE = 200;

/** One shelf of a pipeline's archive: snoozed open tasks in queue order; done and closed, the latest first. */
export function shelfParams(pipeline: string, shelf: Shelf, f: Filters): Record<string, string | number> {
  const base = { ...listParams(pipeline, f), limit: SHELF_PAGE };
  if (shelf === 'snoozed') return { ...base, snoozed: 'only' };
  return { ...base, status: shelf, sort: 'finished' };
}

export const shelfQuery = (pipeline: string, shelf: Shelf, f: Filters) => {
  const params = shelfParams(pipeline, shelf, f);
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<TaskPage>(`/api/v2/tasks?${query(params)}`, { signal }),
  });
};

/* ---------------------------------------------------------------- stage counts */

/**
 * Open tasks per stage, from the Floor (GET /api/v2/company/floor, backend/company_floor.py): the only per-stage count
 * the server gives. It counts every open task (snoozed ones too) and knows no filter, so a board reads it only when its
 * own list is cut short and nothing narrows it. Side stages are not in it.
 */
export const stageCountsQuery = queryOptions({
  queryKey: ['company', 'floor'] as const,
  queryFn: ({ signal }) => api.get<CompanyFloor>('/api/v2/company/floor', { signal }),
  staleTime: QUIET_STALE_MS,
  select: (floor) => {
    const out = new Map<string, number>();
    for (const f of floor.factories) for (const p of f.pipelines) for (const st of p.stages) out.set(st.id, st.count);
    return out;
  },
});

/* ---------------------------------------------------------------- Edit pipeline: its stages */

/** A pipeline's whole settings record, as its save takes it back (GET /api/v2/pipelines/{id}). */
export type PipelineSettingsRecord = PipelineSettingsResult['pipeline'] & Record<string, unknown>;

export const pipelineSettingsQuery = (pipeline: string) =>
  queryOptions({
    queryKey: ['pipelines', pipeline, 'settings'] as const,
    queryFn: async ({ signal }) =>
      (await api.get<PipelineSettingsResult>(`/api/v2/pipelines/${encodeURIComponent(pipeline)}`, { signal })).pipeline as PipelineSettingsRecord,
    staleTime: QUIET_STALE_MS,
  });

/** A stage as the editor holds it: one already saved keeps its id; a new one has none until the save. */
export interface StageDraft {
  id?: string;
  name: string;
  side: boolean;
}

/**
 * Save a pipeline's stages: names, order, side, added and removed. The record goes back whole, with the version it was
 * read at; the board shows the new stages at once. A removed stage that holds tasks is refused (409 stage_has_tasks)
 * until `moveTo` says where its tasks go.
 */
export async function savePipeline(pipeline: string, stages: readonly StageDraft[], moveTo?: { pipeline: string; stage: string }): Promise<Pipeline> {
  const rec = await queryClient.ensureQueryData(pipelineSettingsQuery(pipeline));
  const held = new Map(rec.stages.map((s) => [s.id, s]));
  const next = stages.map((d) => {
    const was = d.id ? held.get(d.id) : undefined;
    return was ? { ...was, name: d.name, side: d.side } : { name: d.name, side: d.side, requirements: [] };
  });
  const shown = (list: Pipeline[] | undefined) =>
    list?.map((p) => (p.id === pipeline ? { ...p, stages: stages.map((d, i) => ({ id: d.id ?? `tmp:${i}`, name: d.name, side: d.side })) } : p));
  const out = await write<PipelineSettingsResult>({
    key: pipelinesQuery.queryKey,
    optimistic: shown,
    send: (idempotencyKey) =>
      api.post<PipelineSettingsResult>(
        `/api/v2/pipelines/${encodeURIComponent(pipeline)}`,
        { ...rec, stages: next, settings_version: rec.settings_version, ...(moveTo ? { move_tasks_to: moveTo } : {}) },
        { idempotencyKey },
      ),
    settle: (list: Pipeline[] | undefined, answer) => list?.map((p) => (p.id === pipeline ? adaptPipeline(answer.pipeline as PipelineRecord) : p)),
  });
  queryClient.setQueryData(pipelineSettingsQuery(pipeline).queryKey, out.pipeline as PipelineSettingsRecord);
  return adaptPipeline(out.pipeline as PipelineRecord);
}

/* ---------------------------------------------------------------- Flow: what waits on you */

export interface FlowItem {
  /** The task's id. */
  id: string;
  number: number | null;
  title: string;
  pipeline_id: string | null;
  stage_id: string | null;
  owner_id: string | null;
  late: boolean;
  stuck: boolean;
  since: string;
}

export interface Flow {
  count: number;
  items: FlowItem[];
}

/** Every task that waits on the person, across pipelines (GET /api/v2/needs-you). Asks without a task stay in chats. */
export const flowQuery = queryOptions({
  queryKey: ['flow'] as const,
  queryFn: async ({ signal }): Promise<Flow> => {
    const r = await api.get<NeedsYou>('/api/v2/needs-you?limit=200', { signal });
    const seen = new Set<string>();
    const items: FlowItem[] = [];
    for (const it of 'items' in r ? r.items : []) {
      const t = it.task;
      if (!t || seen.has(t.id)) continue;
      seen.add(t.id);
      items.push({
        id: t.id,
        number: t.number,
        title: t.title,
        pipeline_id: t.pipeline_id,
        stage_id: t.stage_id,
        owner_id: t.owner_id,
        late: !!t.late,
        stuck: !!t.stuck,
        since: it.since ?? t.stage_since ?? new Date().toISOString(),
      });
    }
    return { count: items.length, items };
  },
  staleTime: QUIET_STALE_MS,
});

/* ---------------------------------------------------------------- tags */

export const tagsQuery = queryOptions({
  queryKey: ['tags'] as const,
  queryFn: async ({ signal }): Promise<Tag[]> =>
    (await api.get<TagList>('/api/v2/tags?is_template=false', { signal })).tags.filter((t) => !t.is_template),
  staleTime: QUIET_STALE_MS,
});

export const tagQuery = (tag: string) =>
  queryOptions({
    queryKey: ['tags', tag] as const,
    queryFn: ({ signal }) => api.get<TagDetail>(`/api/v2/tags/${encodeURIComponent(tag)}`, { signal }),
    staleTime: QUIET_STALE_MS,
  });

/* ---------------------------------------------------------------- one task let go over another's card */

/** Either task, as the drop dialog names it. */
export interface DropEnd {
  id: string;
  number: number | null;
  title: string;
  owner_id: string | null;
  owner: string;
}

/** What a merge carries over, counted from the real tasks before anyone confirms. */
export interface DropMoves {
  comments: number;
  asks: string[];
  subtasks: number;
  files: number;
  followers: number;
  requesters: number;
  labels: number;
  links: number;
  description: boolean;
  due: string | null;
}

/** Both tasks, why each choice cannot be done (null when it can), and what a merge moves. */
export interface DropChecks {
  task: DropEnd;
  into: DropEnd;
  same_owner: boolean;
  subtask: string | null;
  merge: string | null;
  moves: DropMoves;
}

/** GET /api/v2/tasks/{id}/merge?into=: read fresh each time the dialog opens. */
export const dropChecksQuery = (task: string, into: string) =>
  queryOptions({
    queryKey: ['tasks', 'drop', task, into] as const,
    queryFn: ({ signal }) => api.get<DropChecks>(`/api/v2/tasks/${encodeURIComponent(task)}/merge?into=${encodeURIComponent(into)}`, { signal }),
    staleTime: 0,
    gcTime: 0,
  });

/** Put a task under another as its subtask; both keep their owners and stages. */
export async function makeSubtask(id: string, parent: string): Promise<TaskItem> {
  const shown = listsHolding(id);
  const { task } = await write<{ task: TaskItem }>({
    key: shown.length ? shown : keys.tasks.detail(id),
    optimistic: (cur: unknown) => cur,
    send: (idempotencyKey) => api.post<{ task: TaskItem }>(`/api/v2/tasks/${encodeURIComponent(id)}/parent`, { parent }, { idempotencyKey }),
    settle: (cur: TaskPage | undefined, answer) => (cur?.tasks ? patchRow(cur, id, (t) => ({ ...t, ...answer.task })) : cur),
  });
  void queryClient.invalidateQueries({ queryKey: keys.tasks.detail(parent) });
  return task;
}

/**
 * Merge a task into another: `into` stays and gets its details; the task is closed as a duplicate. It leaves the
 * board at once and comes back if the server refuses. `owner` picks who keeps `into` when the owners differ.
 */
export async function mergeTask(id: string, into: string, owner?: string | null): Promise<TaskMergeResult> {
  const shown = listsHolding(id);
  const out = await write<TaskMergeResult>({
    key: shown.length ? shown : keys.tasks.detail(id),
    optimistic: (cur: TaskPage | undefined) => (cur?.tasks ? patchRow(cur, id, () => null) : cur),
    send: (idempotencyKey) =>
      api.post<TaskMergeResult>(`/api/v2/tasks/${encodeURIComponent(id)}/merge`, { into, ...(owner && { owner }) } satisfies TaskMerge, { idempotencyKey }),
    settle: (cur: TaskPage | undefined, answer) =>
      cur?.tasks ? { ...cur, tasks: cur.tasks.map((t) => (t.id === answer.task.id ? { ...t, ...(answer.task as TaskItem) } : t)) } : cur,
  });
  void queryClient.invalidateQueries({ queryKey: keys.tasks.detail(into) });
  return out;
}
