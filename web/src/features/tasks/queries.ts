import { infiniteQueryOptions, keepPreviousData, queryOptions, type InfiniteData, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import { ApiError } from '@/api/client';
import type { TagCreate, TagResult, TagUpdate } from '@/api/schema';
import type {
  FactoryMap,
  NeedsYou,
  PipelineSettings,
  PipelineSettingsList,
  PipelineSettingsResult,
  RequesterInput,
  TaskRequester,
  TaskRequesterAdded,
  TaskRequesterList,
  Task,
  TaskList,
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
  /** The teammate who holds it (`bot:<id>` or `human:<id>`); none when it is held by a role. */
  owner?: string | null;
}

/** A stage's holder when it is a teammate, not a role such as "assignee". */
const holder = (owner: string | null | undefined) => (owner && /^(bot|human):./.test(owner) ? owner : null);

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
  /** The preset it was made from ('features', 'engineering'...): the Roadmap reads the Product one. */
  preset?: string | null;
  /** Who manages it: an actor id. */
  manager?: string | null;
}

type PipelineRecord = PipelineSettings & {
  owner_id?: string | null;
  manager_id?: string | null;
  retired_at?: string | null;
  may_change?: boolean;
  preset?: string | null;
};

function adaptPipeline(p: PipelineRecord): Pipeline {
  return {
    id: p.id,
    name: p.name,
    stages: p.stages.map((s) => ({ id: s.id, name: s.name, side: s.side, owner: holder(s.owner) })),
    owner: p.owner_id ?? p.manager_id ?? null,
    done: p.done ? { id: p.done.id, name: p.done.name } : null,
    closed: p.closed ? { id: p.closed.id, name: p.closed.name } : null,
    mayChange: p.may_change !== false,
    preset: p.preset ?? null,
    manager: p.manager_id ?? null,
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
  Partial<Pick<Task, 'stuck' | 'stage_since' | 'snoozed' | 'created_at' | 'due_at' | 'visibility'>>;

/** The cache's shape for a list: the server's page, kept as is so the stream's patches apply (live/patch.ts). */
export interface TaskPage {
  tasks: TaskItem[];
  cursor: string;
  next_after?: string | null;
  /** Open tasks per stage under the read's filters (`counts=stage`), past what was read too. */
  counts?: NonNullable<TaskList['counts']>;
  /** Kept here, not sent: the rows per stage when `counts` was read, so a move since (shown first or live) shows in it. */
  counted?: Record<string, number>;
}

const perStage = (tasks: readonly TaskItem[], into: Record<string, number> = {}) => {
  for (const t of tasks) if (t.stage_id) into[t.stage_id] = (into[t.stage_id] ?? 0) + 1;
  return into;
};

/**
 * A stage's open tasks: the server's count as read, moved by every row that has come or gone since (a move shown
 * first, a live one, a new task); the next read of the list sets it again. None when the read had no counts.
 */
export function stageCount(page: TaskPage | undefined, stage: string): number | undefined {
  if (!page?.counts) return undefined;
  const now = page.tasks.reduce((n, t) => (t.stage_id === stage ? n + 1 : n), 0);
  return Math.max(0, (page.counts.stage[stage] ?? 0) + now - (page.counted?.[stage] ?? 0));
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
  const p: Record<string, string | number> = { pipeline, status: 'open', limit: PAGE, counts: 'stage' };
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
    queryFn: async ({ signal }) => {
      const page = await api.get<TaskPage>(`/api/v2/tasks?${query(params)}`, { signal });
      return page.counts ? { ...page, counted: perStage(page.tasks) } : page;
    },
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
      const fresh = page.tasks.filter((t) => !seen.has(t.id));
      // the server counted these already: they are not a change
      const counted = cur.counted && perStage(fresh, { ...cur.counted });
      return { ...cur, tasks: [...cur.tasks, ...fresh], next_after: page.next_after ?? null, ...(counted && { counted }) };
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
  // `counts` stays: a shelf past one page still says how many it holds (its `total`).
  const base: Record<string, string | number> = { ...listParams(pipeline, f), limit: SHELF_PAGE };
  if (shelf === 'snoozed') return { ...base, snoozed: 'only' };
  return { ...base, status: shelf, sort: 'finished' };
}

/** How many a shelf holds: its rows, or past one page the server's total; none when the server gave no total. */
export const shelfTotal = (page: TaskPage | undefined): number | undefined =>
  page && (page.next_after ? page.counts?.total : page.tasks.length);

export const shelfQuery = (pipeline: string, shelf: Shelf, f: Filters) => {
  const params = shelfParams(pipeline, shelf, f);
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<TaskPage>(`/api/v2/tasks?${query(params)}`, { signal }),
  });
};

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

/** A stage as the editor holds it: one already saved keeps its id; a new one has none until the save. A field left out
    keeps what the record has. `owner` is a teammate (`bot:<id>`, `human:<id>`), `assignee` (the task's), `pick` (one of
    several, set where it was) or null (none: the manager picks). */
export interface StageDraft {
  id?: string;
  name: string;
  side: boolean;
  /** The stage's goal, stored as `purpose`. */
  purpose?: string;
  owner?: string | null;
  /** At most this many tasks at once; not on the first stage or a side stage. */
  task_limit?: number | null;
}

/** The pipeline's own facts, as the editor holds them. A field left out keeps what the record has. */
export interface PipelineFacts {
  name?: string;
  goal?: string;
  manager_id?: string | null;
  factory_id?: string | null;
  /** One or two letters its tasks are numbered with (E-12); null for none. */
  prefix?: string | null;
}

const defined = <T extends object>(o: T): Partial<T> => Object.fromEntries(Object.entries(o).filter(([, v]) => v !== undefined)) as Partial<T>;

/** A drafted stage over the record's (or a new one): what the editor does not show is sent back as it was read. */
function stageOut(d: StageDraft, was: Record<string, unknown> | undefined): Record<string, unknown> {
  const base = was ?? { purpose: '', owner: null, task_limit: null, requirements: [] };
  const out: Record<string, unknown> = { ...base, ...defined(d) };
  if (!d.id) delete out.id;
  // One of several keeps its list; any other owner has none (a side stage never picks). The next-pick line is the server's.
  if (out.owner !== 'pick') delete out.pick;
  delete out.pick_next;
  if (out.side) out.task_limit = null;
  return out;
}

/** What a save sends, as the editor sets it. `version` is the settings_version the person read; left out, the record's. */
export interface PipelineSave {
  stages: readonly StageDraft[];
  facts?: PipelineFacts;
  version?: number;
  moveTo?: { pipeline: string; stage: string };
}

/**
 * Save a pipeline: its facts (Goal, Manager, Factory, Prefix) and its stages (names, order, side, goal, owner, limit,
 * added and removed). The record goes back whole, with the version the person read it at: someone else's save in
 * between is refused (409 task_changed) and nothing is lost. The board shows the new stages at once. A removed stage that
 * holds tasks is refused (409 stage_has_tasks) until `moveTo` says where its tasks go.
 */
export async function savePipeline(pipeline: string, { stages, facts = {}, version, moveTo }: PipelineSave): Promise<Pipeline> {
  const rec = await queryClient.ensureQueryData(pipelineSettingsQuery(pipeline));
  const held = new Map(rec.stages.map((s) => [s.id, s as unknown as Record<string, unknown>]));
  const next = stages.map((d) => stageOut(d, d.id ? held.get(d.id) : undefined));
  const { bot_families: _families, may_change: _may, ...kept } = rec;
  const shown = (list: Pipeline[] | undefined) =>
    list?.map((p) =>
      p.id === pipeline
        ? {
            ...p,
            ...(facts.name ? { name: facts.name } : {}),
            ...(facts.manager_id !== undefined ? { manager: facts.manager_id } : {}),
            stages: stages.map((d, i) => ({
              id: d.id ?? `tmp:${i}`,
              name: d.name,
              side: d.side,
              owner: d.owner !== undefined ? holder(d.owner) : (p.stages.find((st) => st.id === d.id)?.owner ?? null),
            })),
          }
        : p,
    );
  const out = await write<PipelineSettingsResult>({
    key: pipelinesQuery.queryKey,
    optimistic: shown,
    send: (idempotencyKey) =>
      api.post<PipelineSettingsResult>(
        `/api/v2/pipelines/${encodeURIComponent(pipeline)}`,
        { ...kept, ...defined(facts), stages: next, settings_version: version ?? rec.settings_version, ...(moveTo ? { move_tasks_to: moveTo } : {}) },
        { idempotencyKey },
      ),
    settle: (list: Pipeline[] | undefined, answer) => list?.map((p) => (p.id === pipeline ? adaptPipeline(answer.pipeline as PipelineRecord) : p)),
  });
  queryClient.setQueryData(pipelineSettingsQuery(pipeline).queryKey, out.pipeline as PipelineSettingsRecord);
  return adaptPipeline(out.pipeline as PipelineRecord);
}

/** The record as it is on the server now, past the cache (someone saved first): the editor compares it with what it read. */
export const pipelineSettingsNow = (pipeline: string) => queryClient.fetchQuery({ ...pipelineSettingsQuery(pipeline), staleTime: 0 });

/** What changed between two reads of a record, as a person names it: a fact, or a stage by its name (the old editor's
    peChanged). */
export function pipelineChanged(a: PipelineSettingsRecord, b: PipelineSettingsRecord): string[] {
  const out: string[] = [];
  const facts: [string, string][] = [['name', 'Name'], ['goal', 'Goal'], ['manager_id', 'Manager'], ['factory_id', 'Factory'], ['prefix', 'Prefix'], ['private', 'Private'], ['owner_id', 'Owner']];
  for (const [key, label] of facts) if (JSON.stringify(a[key] ?? null) !== JSON.stringify(b[key] ?? null)) out.push(label);
  const own = ({ pick_next: _next, ...s }: Record<string, unknown>) => JSON.stringify(s);
  const before = new Map(a.stages.map((s) => [s.id, s as unknown as Record<string, unknown>]));
  for (const s of b.stages) {
    const was = before.get(s.id);
    if (!was) out.push(`${s.name} (added)`);
    else if (own(was) !== own(s as unknown as Record<string, unknown>)) out.push(s.name);
  }
  const kept = new Set(b.stages.map((s) => s.id));
  for (const s of a.stages) if (!kept.has(s.id)) out.push(`${s.name} (removed)`);
  if (!out.length && a.stages.map((s) => s.id).join() !== b.stages.map((s) => s.id).join()) out.push('The order of the stages');
  return out;
}

/* ---------------------------------------------------------------- New pipeline */

/** The ids the server keeps for paths of its own (backend/task_pipelines_routes.py RESERVED). */
const RESERVED = new Set(['overview', 'setup', 'decisions', 'review', 'tasks', 'new', 'log', 'retire']);

/** A new pipeline's id from its name: lowercase words joined by dashes, never one in use (a retired one included), since
    a save to a taken id changes that pipeline. */
export function pipelineIdFor(name: string, taken: ReadonlySet<string>): string {
  const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 56) || 'pipeline';
  let id = base;
  for (let n = 2; taken.has(id) || RESERVED.has(id); n += 1) id = `${base}-${n}`;
  return id;
}

/** Make a pipeline (POST /api/v2/pipelines/{new id}: the save that makes one). Only the company's owner and Admins may;
    the server says so otherwise. It shows in the pipeline menu at once. */
export async function createPipeline(facts: PipelineFacts & { name: string }, stages: readonly StageDraft[]): Promise<Pipeline> {
  const all = await api.get<PipelineSettingsList>('/api/v2/pipelines');
  const id = pipelineIdFor(facts.name, new Set(all.pipelines.map((p) => p.id)));
  const record = { ...defined(facts), stages: stages.map((d) => stageOut({ ...d, id: undefined }, undefined)) };
  const made: Pipeline = { id, name: facts.name, stages: [], owner: facts.manager_id ?? null, done: null, closed: null, mayChange: true, manager: facts.manager_id ?? null };
  const out = await write<PipelineSettingsResult>({
    key: pipelinesQuery.queryKey,
    optimistic: (list: Pipeline[] | undefined) => (list ? [...list, made] : list),
    send: (idempotencyKey) => api.post<PipelineSettingsResult>(`/api/v2/pipelines/${encodeURIComponent(id)}`, record, { idempotencyKey }),
    settle: (list: Pipeline[] | undefined, answer) => list?.map((p) => (p.id === id ? adaptPipeline(answer.pipeline as PipelineRecord) : p)),
  });
  queryClient.setQueryData(pipelineSettingsQuery(id).queryKey, out.pipeline as PipelineSettingsRecord);
  return adaptPipeline(out.pipeline as PipelineRecord);
}

/** The live factories a pipeline can sit in (GET /api/v2/factories); null when the read fails (the field is left out). */
export const factoryChoicesQuery = queryOptions({
  queryKey: ['pipelines', 'factories'] as const,
  queryFn: async ({ signal }) => {
    try {
      return (await api.get<FactoryMap>('/api/v2/factories?period=7d', { signal })).factories.map((f) => ({ id: f.id, name: f.name }));
    } catch {
      return null;
    }
  },
  staleTime: QUIET_STALE_MS,
});

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

/* ---------------------------------------------------------------- Roadmap: who asked for a task */

// Who asked for a task has no live topic: read again after the person's own writes and on reconnect.
quietRoot('requesters');

/** Who asked: a teammate (`actor`) or someone outside the team; `important` is 1 for an important requester. */
export type Requester = TaskRequester;
/** One page, oldest first; `next_offset` goes back as offset and is null on the last page. */
export type RequesterList = TaskRequesterList;

/* The answer also carries a removal token, for a person outside the team who asked on a public page. A teammate
   removes with Remove, so the token is never read here: it is not kept, shown or handed on. */
type RequesterAdded = Pick<TaskRequesterAdded, 'id'>;

/** The server's most per page (backend/roadmaps.py). */
export const REQUESTERS_PAGE = 100;

const requestersKey = (task: string) => ['requesters', task] as const;

/** GET /api/v2/tasks/{tid}/requesters: oldest first, a page at a time, following `next_offset` (backend/roadmaps.py). */
export const requestersQuery = (task: string) =>
  infiniteQueryOptions({
    queryKey: requestersKey(task),
    queryFn: ({ pageParam, signal }) =>
      api.get<RequesterList>(
        `/api/v2/tasks/${encodeURIComponent(task)}/requesters?limit=${REQUESTERS_PAGE}${pageParam ? `&offset=${pageParam}` : ''}`,
        { signal },
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_offset ?? undefined,
    staleTime: QUIET_STALE_MS,
  });

type RequesterPages = InfiniteData<RequesterList, number>;

const blank: Omit<Requester, 'id' | 'created_at'> = {
  kind: 'external',
  actor: null,
  name: '',
  email: '',
  phone: '',
  other: '',
  organization: '',
  note: '',
  source: 'manual',
  important: 0,
};

const asRow = (input: RequesterInput, base: Omit<Requester, 'id' | 'created_at'>) => ({
  ...base,
  ...Object.fromEntries(Object.entries(input).filter(([k, v]) => v !== undefined && k !== 'important' && k !== 'actor')),
  ...(input.actor !== undefined && { actor: input.actor ?? null, kind: input.actor ? ('teammate' as const) : ('external' as const) }),
  ...(input.important !== undefined && { important: input.important ? (1 as const) : (0 as const) }),
});

/** `change` each loaded page's rows; `page` is its place and `last` whether it is the last page read. */
const withRows = (cur: RequesterPages | undefined, change: (rows: Requester[], page: number, last: boolean) => Requester[]) =>
  cur && { ...cur, pages: cur.pages.map((p, i) => ({ ...p, requesters: change(p.requesters, i, i === cur.pages.length - 1) })) };

/** Add who asked for a task: the row shows at once, and is taken back if refused. */
export async function addRequester(task: string, input: RequesterInput): Promise<void> {
  const tmp = `tmp:${newId()}`;
  const row: Requester = { ...asRow(input, blank), id: tmp, created_at: new Date().toISOString() };
  await write<RequesterAdded>({
    key: requestersKey(task),
    // a new requester is the newest: it goes at the end of the last page read
    optimistic: (cur: RequesterPages | undefined) => withRows(cur, (rows, _page, last) => (last ? [...rows, row] : rows)),
    send: (idempotencyKey) => api.post<RequesterAdded>(`/api/v2/tasks/${encodeURIComponent(task)}/requesters`, input, { idempotencyKey }),
    settle: (cur: RequesterPages | undefined, answer) =>
      withRows(cur, (rows) => (answer.id ? rows.map((r) => (r.id === tmp ? { ...r, id: answer.id as string } : r)) : rows.filter((r) => r.id !== tmp))),
    revert: (cur: RequesterPages | undefined) => withRows(cur, (rows) => rows.filter((r) => r.id !== tmp)),
  });
}

/** Change a requester's details (not who they are): shown at once, taken back if refused. */
export async function updateRequester(task: string, id: string, input: Omit<RequesterInput, 'actor'>): Promise<void> {
  let before: Requester | undefined;
  await write<unknown>({
    key: requestersKey(task),
    optimistic: (cur: RequesterPages | undefined) =>
      withRows(cur, (rows) =>
        rows.map((r) => {
          if (r.id !== id) return r;
          before = r;
          return { ...asRow(input, r), id: r.id, created_at: r.created_at };
        }),
      ),
    send: (idempotencyKey) =>
      api.post(`/api/v2/tasks/${encodeURIComponent(task)}/requesters/${encodeURIComponent(id)}`, input, { idempotencyKey }),
    revert: (cur: RequesterPages | undefined) => withRows(cur, (rows) => rows.map((r) => (r.id === id && before ? before : r))),
  });
}

/** Remove a requester: gone at once, back if refused. */
export async function removeRequester(task: string, id: string): Promise<void> {
  let from = -1;
  let at = -1;
  let gone: Requester | undefined;
  const there = (cur: RequesterPages | undefined) => !!cur?.pages.some((p) => p.requesters.some((r) => r.id === id));
  await write<unknown>({
    key: requestersKey(task),
    optimistic: (cur: RequesterPages | undefined) =>
      withRows(cur, (rows, page) => {
        const i = rows.findIndex((r) => r.id === id);
        if (i < 0) return rows;
        [from, at, gone] = [page, i, rows[i]];
        return rows.toSpliced(i, 1);
      }),
    send: (idempotencyKey) =>
      api.post(`/api/v2/tasks/${encodeURIComponent(task)}/requesters/${encodeURIComponent(id)}/delete`, {}, { idempotencyKey }),
    revert: (cur: RequesterPages | undefined) =>
      gone && !there(cur) ? withRows(cur, (rows, page) => (page === from && gone ? rows.toSpliced(at, 0, gone) : rows)) : cur,
  });
}

/* ---------------------------------------------------------------- tags: making and changing them (ui/app/tags.ts) */

/** The templates a tag is made from (`is_template=true`). Read when someone looks for one, not with the Tags list. */
export const templatesQuery = queryOptions({
  queryKey: ['tags', { templates: true }] as const,
  queryFn: async ({ signal }): Promise<Tag[]> =>
    (await api.get<TagList>('/api/v2/tags?is_template=true', { signal })).tags.filter((t) => t.is_template),
  staleTime: QUIET_STALE_MS,
});

const tagsAgain = () => void queryClient.invalidateQueries({ queryKey: ['tags'] });

/** A key as the old form made it from a name: lowercase words joined by hyphens, cut at 40 (hubdb cuts there). */
export const tagKeyOf = (name: string) =>
  name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 40)
    .replace(/-+$/, '');

/** A new tag or template (POST /api/v2/tags). */
export async function createTag(body: TagCreate): Promise<Tag> {
  const { tag } = await api.post<TagResult>('/api/v2/tags', body);
  tagsAgain();
  return tag;
}

/** A tag made from a template: the template's notes, under its own key (POST /api/v2/tags/{id}/instances). */
export async function makeTagFrom(template: string, body: Pick<TagCreate, 'key' | 'metadata'>): Promise<Tag> {
  const { tag } = await api.post<TagResult>(`/api/v2/tags/${encodeURIComponent(template)}/instances`, body);
  tagsAgain();
  return tag;
}

export const RELEASE_KEY = 'release-checklist';
const RELEASE_NOTES = '- [ ] Run release scripts and migrations\n- [ ] Deploy\n- [ ] Run smoke checks\n- [ ] Tell the team';

/** The release checklist starter: the template when it is there, made when it is not (someone may make it meanwhile). */
export async function releaseChecklist(): Promise<Tag> {
  const there = (await queryClient.fetchQuery(templatesQuery)).find((t) => t.key === RELEASE_KEY);
  if (there) return there;
  try {
    return await createTag({ key: RELEASE_KEY, label: 'release', is_template: true, markdown: RELEASE_NOTES });
  } catch (e) {
    if (!(e instanceof ApiError && e.code === 'duplicate')) throw e;
    return (await api.get<TagDetail>(`/api/v2/tags/${RELEASE_KEY}`)).tag;
  }
}

/** A tag's name, notes or metadata, against the version on screen (409 when someone changed it): shown at once. */
export async function saveTag(id: string, fields: Omit<TagUpdate, 'version' | 'owner' | 'bot_writers'>): Promise<Tag> {
  const cur = queryClient.getQueryData(tagQuery(id).queryKey);
  const version = cur?.tag.version ?? (await queryClient.fetchQuery(tagQuery(id))).tag.version;
  const patch = Object.fromEntries(Object.entries(fields).filter(([, v]) => v !== undefined && v !== null));
  const { tag } = await write<TagResult>({
    key: tagQuery(id).queryKey,
    optimistic: (d: TagDetail | undefined) => d && { ...d, tag: { ...d.tag, ...patch } },
    send: (idempotencyKey) => api.post<TagResult>(`/api/v2/tags/${encodeURIComponent(id)}`, { version, ...patch }, { idempotencyKey }),
    settle: (d: TagDetail | undefined, answer) => d && { ...d, tag: answer.tag },
  });
  void queryClient.invalidateQueries({ queryKey: tagsQuery.queryKey, exact: true });
  return tag;
}

/** The next 500 of a tag's tasks (`offset`), added under the ones read. */
export async function moreTagTasks(id: string): Promise<void> {
  const cur = queryClient.getQueryData(tagQuery(id).queryKey);
  if (cur?.next_offset == null) return;
  const next = await api.get<TagDetail>(`/api/v2/tags/${encodeURIComponent(id)}?offset=${cur.next_offset}`);
  queryClient.setQueryData(tagQuery(id).queryKey, (d: TagDetail | undefined) => {
    if (!d) return d;
    const seen = new Set(d.tasks.map((t) => t.id));
    return { ...d, tasks: [...d.tasks, ...next.tasks.filter((t) => !seen.has(t.id))], next_offset: next.next_offset };
  });
}

/** A task filed with this tag on it (POST /api/v2/tasks `labels`): it shows on the tag's page at once. */
export async function createTaggedTask(id: string, key: string, input: NewTask): Promise<TaskItem> {
  const tmp = `tmp:${newId()}`;
  const first = queryClient.getQueryData<Pipeline[]>(pipelinesQuery.queryKey)?.find((p) => p.id === input.pipeline)?.stages[0];
  const now = new Date().toISOString();
  const row = {
    id: tmp,
    number: null,
    title: input.title,
    status: 'open',
    pipeline_id: input.pipeline,
    stage_id: first?.id ?? null,
    owner_id: input.owner ?? null,
    labels: [key],
    late: null,
    turn: null,
    updated_at: now,
    version: 0,
  } as unknown as Task;
  const { task } = await write<{ task: TaskItem }>({
    key: tagQuery(id).queryKey,
    optimistic: (d: TagDetail | undefined) => d && { ...d, tasks: [row, ...d.tasks] },
    send: (idempotencyKey) =>
      api.post<{ task: TaskItem }>(
        '/api/v2/tasks',
        { title: input.title, pipeline: input.pipeline, labels: [key], ...(input.owner ? { owner: input.owner } : {}) },
        { idempotencyKey },
      ),
    settle: (d: TagDetail | undefined, answer) => d && { ...d, tasks: d.tasks.map((t) => (t.id === tmp ? (answer.task as unknown as Task) : t)) },
  });
  return task;
}
