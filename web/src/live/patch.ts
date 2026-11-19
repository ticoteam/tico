import type { QueryKey } from '@tanstack/react-query';
import { queryClient } from '@/data/query-client';
import { keys } from '@/data/keys';
import { withStatus, type Directory, type StatusLine } from '@/data/directory';
import type { Topic, LiveEvent } from './stream';

// How the stream's rows reach the cache. A feature registers, once, what a topic's events do to its query keys:
// - a topic that carries rows (tasks, messages, letters, threads, bots, needs): `onTopic`, patching rows in place
//   by id and version (the tasks and messages patchers below are the ones to copy);
// - a topic that only says "read again" (calendar, setup, meeting_items, presence): `onRefetch`, which marks the
//   keys stale and refetches only what is on screen, at most once a second per key;
// - an area with no topic (goals, KPIs, budgets, finance, records): `quietRoot` in data/optimistic.ts.

/** A handler's channel: a topic, or `reset` (the stream could not replay: everything was marked stale). */
export type Channel = Topic | 'reset';
type Handler = (batch: never[]) => void;

const handlers = new Map<Channel, Set<Handler>>();

/** `handler(batch)` once per frame with that frame's events of `channel`, oldest first. Returns the unsubscribe. */
export function onTopic<E = LiveEvent>(channel: Channel, handler: (batch: E[]) => void): () => void {
  let set = handlers.get(channel);
  if (!set) handlers.set(channel, (set = new Set()));
  const h = handler as Handler;
  set.add(h);
  return () => void handlers.get(channel)?.delete(h);
}

export function dispatch(channel: string, batch: unknown[]): void {
  for (const h of [...(handlers.get(channel as Channel) ?? [])]) {
    try {
      h(batch as never[]);
    } catch (err) {
      console.error(err);
    }
  }
}

// ---------------------------------------------------------------- refetch-only topics

const REFETCH_GAP_MS = 1000;
const refetchedAt = new Map<string, number>();
const trailing = new Map<string, ReturnType<typeof setTimeout>>();

function refetchSoon(key: QueryKey): void {
  const id = JSON.stringify(key);
  if (trailing.has(id)) return;
  const wait = (refetchedAt.get(id) ?? -Infinity) + REFETCH_GAP_MS - Date.now();
  const go = () => {
    trailing.delete(id);
    refetchedAt.set(id, Date.now());
    void queryClient.invalidateQueries({ queryKey: key, refetchType: 'active' });
  };
  if (wait <= 0) return go();
  // Stale now, so a page opened meanwhile reads it; what is on screen reads once the gap has passed.
  void queryClient.invalidateQueries({ queryKey: key, refetchType: 'none' });
  trailing.set(id, setTimeout(go, wait));
}

/** For a topic that only signals: the keys each event makes stale. Returns the unsubscribe. */
export function onRefetch<E = LiveEvent>(topic: Topic, keysFor: (event: E) => QueryKey[]): () => void {
  return onTopic<E>(topic, (batch) => {
    const seen = new Map<string, QueryKey>();
    for (const e of batch) for (const k of keysFor(e)) seen.set(JSON.stringify(k), k);
    for (const k of seen.values()) refetchSoon(k);
  });
}

export function resetPatchState(): void {
  for (const t of trailing.values()) clearTimeout(t);
  trailing.clear();
  refetchedAt.clear();
}

// ---------------------------------------------------------------- tasks

/** The fields of a task row the patcher reads (GET /api/v2/tasks rows carry many more). */
export interface TaskRow {
  id: string;
  version?: number;
  status?: string;
  pipeline_id?: string | null;
  stage_id?: string | null;
  owner_id?: string | null;
  requester_id?: string | null;
  lane?: string;
  snoozed?: boolean;
}
/** GET /api/v2/tasks: `cursor` is "<change number>.<access tag>" (backend/task_reads.py cursor). */
export interface TaskList<T extends TaskRow = TaskRow> {
  tasks: T[];
  cursor?: string;
}
/** backend/api_schemas/live_events.py TaskEvent; a followed task's access loss is `{task_id, gone}` with no seq. */
export interface TaskEvent<T extends TaskRow = TaskRow> {
  seq?: number;
  actor?: string | null;
  at?: string;
  id?: string;
  task_id?: string;
  kind?: string;
  kinds?: string[];
  task?: T;
  gone?: boolean;
  bulk?: boolean;
  was?: { owner: string | null; status: string | null };
}

/** The change number a task list was read at; -1 when it does not say. */
export function listSeq(list: { cursor?: unknown } | undefined): number {
  const n = parseInt(String(list?.cursor ?? ''), 10);
  return Number.isFinite(n) ? n : -1;
}

const PAGING = new Set(['limit', 'sort', 'brief', 'offset', 'after', 'snoozed']);
const isActor = (v: unknown) => typeof v === 'string' && /^(human|bot):/.test(v);

/**
 * Whether a task belongs in a list read with these GET /api/v2/tasks params: true, false, or null when a filter is
 * one only the server can answer (search, labels, Late, Waiting on...). Rules from backend task_filters.
 */
export function taskMatches(t: TaskRow, params: Record<string, unknown> | undefined): boolean | null {
  const p = params ?? {};
  let unsure = false;
  for (const [k, v] of Object.entries(p)) {
    if (v == null || v === '' || PAGING.has(k)) continue;
    const s = String(v);
    if (k === 'status') {
      if (s !== 'all' && !s.split(',').includes(String(t.status))) return false;
    } else if (k === 'pipeline') {
      if (t.pipeline_id !== s) return false;
    } else if (k === 'stage') {
      if (t.stage_id !== s) return false;
    } else if (k === 'lane') {
      if (t.lane !== s) return false;
    } else if (k === 'owner' || k === 'requester') {
      if (!isActor(v)) unsure = true;
      else if ((k === 'owner' ? t.owner_id : t.requester_id) !== s) return false;
    } else unsure = true;
  }
  const snoozed = p.snoozed ?? (p.status === 'all' ? 'all' : 'hide');
  if (snoozed === 'hide' && t.snoozed) return false;
  if (snoozed === 'only' && !t.snoozed) return false;
  return unsure ? null : true;
}

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object' || Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  const ra = a as Record<string, unknown>;
  const rb = b as Record<string, unknown>;
  return ka.every((k) => Object.hasOwn(rb, k) && same(ra[k], rb[k]));
}

const older = (incoming: { version?: number }, held: { version?: number }) =>
  incoming.version != null && held.version != null && incoming.version < held.version;

/**
 * Fold a frame's task events into one list. Returns the same list when nothing changed, and keeps every row
 * that did not change, so only changed rows redraw. `unsure`: an event touched the list in a way only the server
 * can settle (its filters), so the caller reads the list again.
 */
export function patchTaskList<T extends TaskRow>(
  list: TaskList<T>,
  events: TaskEvent<T>[],
  params?: Record<string, unknown>,
): { list: TaskList<T>; unsure: boolean } {
  const at = listSeq(list);
  let tasks = list.tasks;
  let unsure = false;
  const edit = () => {
    if (tasks === list.tasks) tasks = [...tasks];
    return tasks;
  };
  for (const e of events) {
    if (e.seq != null && at >= e.seq) continue; // the list was read after this change
    const id = e.id ?? e.task_id;
    if (!id) continue;
    const i = tasks.findIndex((t) => t.id === id);
    if (e.gone || !e.task) {
      if (e.gone && i >= 0) edit().splice(i, 1);
      continue;
    }
    const fits = taskMatches(e.task, params);
    if (i < 0) {
      if (fits) edit().push(e.task);
      else if (fits === null) unsure = true;
      continue;
    }
    const held = tasks[i] as T;
    if (fits === false) edit().splice(i, 1);
    else if (!older(e.task, held) && !same(held, e.task)) edit()[i] = e.task;
    if (fits === null) unsure = true;
  }
  return { list: tasks === list.tasks ? list : { ...list, tasks }, unsure };
}

function patchTasks(batch: TaskEvent[]): void {
  if (batch.some((e) => e.bulk)) {
    void queryClient.invalidateQueries({ queryKey: ['tasks'] });
    batch = batch.filter((e) => !e.bulk);
  }
  if (!batch.length) return;
  for (const [key, data] of queryClient.getQueriesData<TaskList>({ queryKey: ['tasks', 'list'] })) {
    if (!data || !Array.isArray(data.tasks)) continue;
    const { list, unsure } = patchTaskList(data, batch, key[2] as Record<string, unknown> | undefined);
    if (list !== data) queryClient.setQueryData(key, list);
    if (unsure) refetchSoon(key);
  }
  const last = new Map<string, TaskEvent>();
  for (const e of batch) {
    const id = e.id ?? e.task_id;
    if (id) last.set(id, e);
  }
  for (const [id, e] of last) {
    const detail = ['tasks', 'detail', id];
    if (e.gone) {
      queryClient.removeQueries({ queryKey: detail });
      continue;
    }
    const task = e.task;
    if (!task) continue;
    const held = queryClient.getQueryData<TaskRow>(detail);
    // Writing the same data still counts as a write (and a redraw): only a real change is set.
    if (held && !older(task, held) && !same({ ...held, ...task }, held)) queryClient.setQueryData(detail, { ...held, ...task });
    // A file or a linked page changed: whatever the task view reads under its detail key (not the detail itself).
    if (e.kinds?.includes('files'))
      void queryClient.invalidateQueries({
        predicate: (q) => q.queryKey.length > 3 && same(q.queryKey.slice(0, 3), detail),
      });
  }
}

// ---------------------------------------------------------------- messages

/** A timeline row (GET /api/v2/threads/{id}/timeline `messages`), and a message as a `messages` event carries it. */
export interface MessageRow {
  id: string;
  seq?: number | null;
  revision?: number;
  conversation_id?: string;
  thread_id?: string;
  depth?: number;
  deleted?: boolean;
}
export interface Timeline<M extends MessageRow = MessageRow> {
  messages: M[];
  next_cursor?: string | number | null;
}
/** One timeline read, or the pages of an infinite one (in either order: rows are placed by seq). */
export type TimelineData<M extends MessageRow = MessageRow> =
  | Timeline<M>
  | { pages: Timeline<M>[]; pageParams: unknown[] };
/** docs/dev/threads-contract.md section 9. A chat goal change is `goal_id` and `goal` instead. */
export interface MessageEvent<M extends MessageRow = MessageRow> {
  seq?: number;
  actor?: string | null;
  at?: string;
  id?: string;
  conversation_id?: string;
  thread_id?: string;
  root_id?: string;
  message_seq?: number | null;
  revision?: number;
  message?: M;
  deleted?: boolean;
  goal_id?: string;
}

const pagesOf = <M extends MessageRow>(d: TimelineData<M>): Timeline<M>[] => ('pages' in d ? d.pages : [d]);
const rebuild = <M extends MessageRow>(d: TimelineData<M>, pages: Timeline<M>[]): TimelineData<M> =>
  'pages' in d ? { ...d, pages } : (pages[0] as Timeline<M>);
const convOf = (m: MessageRow) => m.thread_id ?? m.conversation_id;
const seqOf = (m: MessageRow) => (typeof m.seq === 'number' ? m.seq : Infinity);

/**
 * Put one message into a timeline of `threadId`: replace it where it is (a revision older than the one held is
 * ignored, an equal one with the same content changes nothing), or place it after the last message of its own
 * thread with a lower seq, past that message's replies. A message of another thread the timeline holds nothing
 * of is left out. Returns the same data when nothing changed.
 */
export function upsertMessage<M extends MessageRow>(data: TimelineData<M>, threadId: string, msg: M): TimelineData<M> {
  const pages = pagesOf(data);
  for (let p = 0; p < pages.length; p++) {
    const rows = (pages[p] as Timeline<M>).messages;
    const i = rows.findIndex((m) => m.id === msg.id);
    if (i < 0) continue;
    const held = rows[i] as M;
    if ((msg.revision ?? 0) < (held.revision ?? 0)) return data;
    const next = { ...held, ...msg };
    if (same(next, held)) return data;
    const copy = [...pages];
    copy[p] = { ...(pages[p] as Timeline<M>), messages: rows.with(i, next) };
    return rebuild(data, copy);
  }
  const conv = convOf(msg);
  const seq = seqOf(msg);
  // The anchor: the last row of the message's own thread with a lower seq, in the page that holds it.
  let ap = -1;
  let ai = -1;
  let best = -Infinity;
  for (const [p, page] of pages.entries())
    for (const [i, m] of page.messages.entries()) {
      const s = seqOf(m);
      if (convOf(m) === conv && s < seq && s > best) [best, ap, ai] = [s, p, i];
    }
  const copy = [...pages];
  if (ap >= 0) {
    const [p, i] = [ap, ai];
    const rows = (pages[p] as Timeline<M>).messages;
    const depth = rows[i]?.depth ?? 0;
    let j = i + 1;
    while (j < rows.length && ((rows[j]?.depth ?? 0) > depth || seqOf(rows[j] as M) < seq)) j++;
    copy[p] = { ...(pages[p] as Timeline<M>), messages: rows.toSpliced(j, 0, { depth, ...msg }) };
    return rebuild(data, copy);
  }
  if (conv !== threadId) return data;
  // The thread's first message we hold, or older than all of them: into the page with the newest rows.
  if (!pages.length) return data;
  let p = 0;
  let newest = -Infinity;
  for (const [k, page] of pages.entries()) {
    const top = Math.max(-Infinity, ...page.messages.map(seqOf).filter(Number.isFinite));
    if (top > newest) [newest, p] = [top, k];
  }
  const rows = (pages[p] as Timeline<M>).messages;
  const j = rows.findIndex((m) => convOf(m) === conv && seqOf(m) > seq);
  copy[p] = { ...(pages[p] as Timeline<M>), messages: rows.toSpliced(j < 0 ? rows.length : j, 0, { depth: 0, ...msg }) };
  return rebuild(data, copy);
}

/** Take one row out (a pending row whose real message has landed). Returns the same data when it is not there. */
export function removeMessage<M extends MessageRow>(data: TimelineData<M>, id: string): TimelineData<M> {
  const pages = pagesOf(data);
  const p = pages.findIndex((page) => page.messages.some((m) => m.id === id));
  if (p < 0) return data;
  const copy = [...pages];
  const page = pages[p] as Timeline<M>;
  copy[p] = { ...page, messages: page.messages.filter((m) => m.id !== id) };
  return rebuild(data, copy);
}

function patchMessages(batch: MessageEvent[]): void {
  const timelines = queryClient
    .getQueriesData<TimelineData>({ queryKey: ['thread'] })
    .filter(([key, data]) => key[2] === 'timeline' && typeof key[1] === 'string' && data);
  if (!timelines.length) return;
  for (const [key, data] of timelines) {
    const threadId = key[1] as string;
    let next = data as TimelineData;
    for (const e of batch) {
      if (!e.id || e.goal_id) continue;
      if (e.conversation_id !== threadId && e.thread_id !== threadId && e.root_id !== threadId) continue;
      const row: MessageRow = e.deleted
        ? { id: e.id, deleted: true, ...(e.revision != null && { revision: e.revision }) }
        : { ...(e.message as MessageRow), id: e.id };
      if (e.deleted && !pagesOf(next).some((pg) => pg.messages.some((m) => m.id === e.id))) continue;
      next = upsertMessage(next, threadId, row);
    }
    if (next !== data) queryClient.setQueryData(key, next);
  }
}

// ---------------------------------------------------------------- agents' status lines

/** backend/events.py `_bots`: an agent's status line; `status` is null before it has one. */
interface BotEvent {
  bot?: string;
  status?: StatusLine | null;
}

function patchDirectory(batch: BotEvent[]): void {
  const held = queryClient.getQueryData<Directory>(keys.directory());
  if (!held) return;
  let dir: Directory | null = held;
  for (const e of batch) if (dir && e.bot) dir = withStatus(dir, e.bot, e.status);
  if (!dir) refetchSoon(keys.directory()); // an agent it does not hold yet
  else if (dir !== held) queryClient.setQueryData(keys.directory(), dir);
}

// ---------------------------------------------------------------- the built-in patchers

let installed = false;
/** Tasks and messages patch rows in place; calendar, setup, meeting items and presence only mark keys stale. */
export function installPatchers(): void {
  if (installed) return;
  installed = true;
  onTopic<TaskEvent>('tasks', patchTasks);
  onTopic<MessageEvent>('messages', patchMessages);
  onRefetch('calendar', () => [['calendar']]);
  onRefetch('setup', () => [['setup']]);
  onTopic<BotEvent>('bots', patchDirectory);
  // An agent added, archived or brought back.
  onRefetch<{ area?: string }>('setup', (e) => (e.area === 'org' ? [keys.directory()] : []));
  onRefetch<{ meeting_id?: string }>('meeting_items', (e) => [e.meeting_id ? ['meetings', e.meeting_id] : ['meetings']]);
  onRefetch<{ place?: string }>('presence', (e) => [e.place ? ['presence', e.place] : ['presence']]);
}
