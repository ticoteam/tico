import { queryOptions, type QueryClient, type QueryKey } from '@tanstack/react-query';
import { ApiError, api } from '@/api/client';
import type { NeedsYou, OkPreview, PageShown, PipelineSettingsList, Task, TaskDetail, TaskFileList, TaskResult } from '@/api/schema';
import { keys, queryClient, QUIET_STALE_MS, quietRoot, write } from '@/data';

/* One open task: its detail (GET /api/v2/tasks/{tid}, kept current by the stream under keys.tasks.detail), the files
   and pages it shows (under the detail key, re-read when the stream says its files changed), the stages it can move
   to, and what waits on the person (Flow's next and previous). Every change is a write shown first. */

quietRoot('flow');
quietRoot('pipelines');

const enc = encodeURIComponent;
const taskPath = (id: string) => `/api/v2/tasks/${enc(id)}` as const;

/** The task as this view holds it: the task row and what its detail adds, flat, so a live patch of the row lands. */
export type TaskData = Task & {
  stage_since?: string | null;
  runs: NonNullable<TaskDetail['runs']>;
  parent: TaskDetail['parent'];
  children: TaskChild[];
  can_comment: boolean;
  mover: boolean;
  /** The task's chat (sent, not typed in the schema). */
  conversation_id?: string | null;
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

/** A link on the task (GET /api/v2/tasks/{tid}/links; not in the schema). Pull requests and sessions have their own sections. */
export interface TaskLink {
  id: string;
  kind: string;
  url: string;
  title?: string | null;
  added_by?: string | null;
  created_at?: string | null;
}

export const taskLinksQuery = (id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(id), 'links'] as const,
    queryFn: async ({ signal }) => (await api.get<{ links: TaskLink[] }>(`${taskPath(id)}/links`, { signal })).links,
  });

/** The task's files, pages first (GET /api/v2/tasks/{tid}/files). */
export const taskFilesQuery = (id: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(id), 'files'] as const,
    queryFn: async ({ signal }) => (await api.get<TaskFileList>(`${taskPath(id)}/files`, { signal })).files,
  });

/** One of the task's pages, with the address it is drawn from (GET /api/v2/pages/{fid}). */
export const taskPageQuery = (task: string, file: string) =>
  queryOptions({
    queryKey: [...keys.tasks.detail(task), 'page', file] as const,
    queryFn: async ({ signal }) => (await api.get<PageShown>(`/api/v2/pages/${enc(file)}`, { signal })).page,
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

/** Add a link; it shows at once and the server's list replaces it. */
export function linkTask(id: string, url: string, title?: string) {
  const key = taskLinksQuery(id).queryKey;
  const shown: TaskLink = { id: `pending:${url}`, kind: 'link', url, title: title ?? null, created_at: new Date().toISOString() };
  return write<{ links: TaskLink[] }>({
    key,
    optimistic: (cur: TaskLink[] | undefined) => [...(cur ?? []), shown],
    send: (idem) => api.post<{ links: TaskLink[] }>(`${taskPath(id)}/links`, { url, ...(title && { title }) }, { idempotencyKey: idem }),
    settle: (_cur, answer) => answer.links,
  });
}

export function dropLink(id: string, link: string) {
  return write<{ links: TaskLink[] }>({
    key: taskLinksQuery(id).queryKey,
    optimistic: (cur: TaskLink[] | undefined) => cur?.filter((l) => l.id !== link),
    send: (idem) => api.post<{ links: TaskLink[] }>(`${taskPath(id)}/links`, { remove: link }, { idempotencyKey: idem }),
    settle: (_cur, answer) => answer.links,
  });
}
