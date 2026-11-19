import { keepPreviousData, queryOptions, type QueryKey } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { NeedsYou, PipelineSettings, PipelineSettingsList, Task, Tag, TagDetail, TagList } from '@/api/schema';
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
}

type PipelineRecord = PipelineSettings & { owner_id?: string | null; manager_id?: string | null; retired_at?: string | null };

function adaptPipeline(p: PipelineRecord): Pipeline {
  return {
    id: p.id,
    name: p.name,
    stages: p.stages.map((s) => ({ id: s.id, name: s.name })),
    owner: p.owner_id ?? p.manager_id ?? null,
    done: p.done ? { id: p.done.id, name: p.done.name } : null,
    closed: p.closed ? { id: p.closed.id, name: p.closed.name } : null,
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
}

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
  return p;
}

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

const loading = new Set<string>();

/** The next page of a list, added to the same cache entry (so the stream keeps patching it). */
export async function loadMore(pipeline: string, f: Filters): Promise<void> {
  const params = listParams(pipeline, f);
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
}

/** File a task: a pending row shows in the pipeline's first stage at once, and becomes the real one when it lands. */
export async function createTask(input: NewTask, lists: QueryKey[]): Promise<TaskItem> {
  const tmp = `tmp:${newId()}`;
  const first = queryClient.getQueryData<Pipeline[]>(pipelinesQuery.queryKey)?.find((p) => p.id === input.pipeline)?.stages[0];
  const now = new Date().toISOString();
  const row: TaskItem = {
    id: tmp,
    number: null,
    title: input.title,
    status: 'open',
    pipeline_id: input.pipeline,
    stage_id: first?.id ?? null,
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
    send: (idempotencyKey) =>
      api.post<{ task: TaskItem }>(
        '/api/v2/tasks',
        { title: input.title, pipeline: input.pipeline, ...(input.owner ? { owner: input.owner } : {}) },
        { idempotencyKey },
      ),
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
  void queryClient.invalidateQueries({ queryKey: ['flow'] });
  return task;
}

/** The open list keys of one pipeline, for a new task to show in. */
export const listsOf = (pipeline: string): QueryKey[] =>
  queryClient
    .getQueriesData<TaskPage>({ queryKey: ['tasks', 'list'] })
    .filter(([k, d]) => {
      const p = k[2] as Record<string, unknown> | undefined;
      return !!d && p?.pipeline === pipeline && p.status === 'open';
    })
    .map(([k]) => k);

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
