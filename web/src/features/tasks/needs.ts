import { useQuery, type QueryKey } from '@tanstack/react-query';
import { useEffect, useMemo, useSyncExternalStore } from 'react';
import { keepOut, letIn, queryClient } from '@/data';
import { hearPlaces, heldRow, holdRow, keptOrder, type ListRow } from '@/lib';
import { flowCountQuery, flowQuery, type Flow } from './queries';

/* One dot, one meaning: a task waits on the person when Needs you names it (GET /api/v2/needs-you), never by its turn
   alone. Every place that marks a task with the Dot reads this one set: a board's cards and column counts, a pipeline's
   list, the list beside an open task, Roadmap, Tags, a task's own status line, a teammate's tasks, the Company floor's
   stage and Tico's tracker. The menu's count is Needs you's own rows. */

/** Every task Needs you names: each row's task, and each task a grouped row names (a paused agent's `task_ids`). An
    array, so a read that names the same tasks keeps the same one (the query's structural sharing) and nothing redraws. */
export const needsYouIds = (flow: Pick<Flow, 'items' | 'task_ids'>): string[] => flow.task_ids ?? flow.items.map((i) => i.id);

/** The same, as a set. */
export const needsYouTasks = (flow: Pick<Flow, 'items' | 'task_ids'>): ReadonlySet<string> => new Set(needsYouIds(flow));

const asSet = (ids: readonly string[] | undefined) => (ids ? new Set(ids) : undefined);

/** The tasks waiting on the signed-in person; undefined until Needs you is read (no Dot until then). Reads it when it
    is not read yet. */
export function useNeedsYou(): ReadonlySet<string> | undefined {
  const ids = useQuery({ ...flowQuery, select: needsYouIds }).data;
  return useMemo(() => asSet(ids), [ids]);
}

/** The same, from what is already read only (never a read of its own, so a task's page costs nothing more); undefined
    until the app has read Needs you (the shell reads it once), and so no Dot rather than the turn's. */
export function useNeedsYouRead(): ReadonlySet<string> | undefined {
  const ids = useQuery({ ...flowQuery, select: needsYouIds, enabled: false }).data;
  return useMemo(() => asSet(ids), [ids]);
}

/** Whether a task waits on the person: Needs you names it. */
export const waitsOnMe = (t: { id: string }, needs: ReadonlySet<string> | undefined): boolean => !!needs?.has(t.id);

/** A board's shown order for one stage: what waits on the person leads it, each part in the stage's own order. The
    board's columns and the list beside a task opened from it (in Board order) both draw it, so they match. */
export const yoursFirst = <T extends { id: string }>(list: readonly T[], needs: ReadonlySet<string> | undefined): T[] => [
  ...list.filter((t) => waitsOnMe(t, needs)),
  ...list.filter((t) => !waitsOnMe(t, needs)),
];

/** Needs you's rows in Tico's order (the server's `order`; requests first, then tasks, from a read that has none). */
const latestOf = (flow: Pick<Flow, 'items' | 'requests' | 'order'>): string[] =>
  flow.order ?? [...(flow.requests ?? []), ...flow.items].map((it) => it.id);

/** Needs you's rows (requests and tasks, by id) in the order drawn: Tico's, with the rows down to the one the person is
    on kept where they were (a sort that lands meanwhile moves only the rows below it). The same order wherever it is
    drawn (Tasks, beside a task) or read for the row below an answer (the shell's NeedsYouOrder). */
export function useNeedsYouDrawn(flow: Pick<Flow, 'items' | 'requests' | 'order'> | undefined): string[] {
  const held = useSyncExternalStore(hearPlaces, heldRow);
  const order = flow ? keptOrder(flow, () => latestOf(flow), held) : NONE;
  // the row the person was on left the list: on none now
  const gone = !!flow && !!held && !order.includes(held);
  useEffect(() => {
    if (gone) holdRow(null);
  }, [gone]);
  return order;
}
const NONE: string[] = [];

/** Needs you's task rows in `order` (useNeedsYouDrawn; Tico's own without it), each with its number as its address
    names it and its pipeline: the order an answer goes on down, wherever the list is drawn or not (a phone, a tucked
    list, a row past the cap). A request has no task to open: the row below a task is the next task drawn. */
export const needsYouRows = (flow: Pick<Flow, 'items' | 'requests' | 'order'>, order: readonly string[] = latestOf(flow)): ListRow[] => {
  const byId = new Map(flow.items.map((it) => [it.id, it] as const));
  return order.flatMap((id) => {
    const it = byId.get(id);
    return it ? [{ id: it.id, n: String(it.number ?? it.id) }] : [];
  });
};

/** What else Needs you, as read now, has the person do on `task` once `wait` is answered (null: all of it, as a
    review decided or an agent released): the other items it names the task by. Read as the person acts, so what the
    open task does after the answer (stay, or go on) never turns on a read that lands meanwhile (TaskPage). */
export const needsYouLeft = (task: string, wait: string | null, read: Pick<Flow, 'waits'> | undefined = queryClient.getQueryData(flowQuery.queryKey)): string[] =>
  wait === null ? [] : (read?.waits?.[task]?.filter((r) => r !== wait) ?? []);

/** Whether a read kept under ['flow'] is Needs you's own: its list (rows and a count), or the menu's count
    (`wakeAt`); not the snoozed asks or a row's origin. */
export const isNeedsYouRead = (cur: unknown): boolean => {
  if (!cur || typeof cur !== 'object') return false;
  const f = cur as { items?: unknown; count?: unknown };
  return 'wakeAt' in cur || (Array.isArray(f.items) && typeof f.count === 'number');
};

/** Needs you with `wait` on `task` answered (null: all of it), as a change to each Needs you read (isNeedsYouRead):
    one answered of several leaves the task's row, showing what still waits on it (the next approval, the next ❓); its
    last takes the row, its approvals, its Dot (`task_ids`), its place (`order`) and what it waits on. The menu's count
    is then the list's own count, when the list is read (so a count a live read already brought down never falls
    twice), else one less. The one change an answer, a hold decided and a release make, so the row and the Dot go
    everywhere at once. `read`: the list as read before the answer. */
export function needsYouWithout(task: string, wait: string | null, read: Flow | undefined = queryClient.getQueryData(flowQuery.queryKey)): (cur: unknown) => unknown {
  const left = needsYouLeft(task, wait, read);
  const leaves = !left.length;
  // a task only a grouped row names (a paused agent's) is no row of its own: no count falls
  const row = read?.items.some((w) => w.id === task) ?? true;
  const apply = (f: Flow): Flow => {
    const approvals = f.approvals?.filter((a) => !(a.id === task && (wait === null || a.ask.requirement === wait)));
    // the Agent bar's line to it goes with it
    const asks = f.asks?.filter((a) => !(a.task === task && (wait === null || a.wait === wait)));
    if (!leaves) {
      const next = approvals?.find((a) => a.id === task);
      const items = f.items.map((w) => {
        if (w.id !== task) return w;
        const asked = w.questions?.find((q) => q.id === wait);
        // the row stood for that approval: it shows the next one, else what else waits there
        const base = w.ask?.requirement === wait ? (next ?? { ...w, ask: undefined, about: undefined }) : w;
        // an answered ❓ goes from the row, so its card shows the next one, and its line no longer names the row
        return { ...base, questions: w.questions?.filter((q) => q.id !== wait), ...(asked && base.reason === asked.line && { reason: undefined }) };
      });
      return { ...f, items, ...(approvals && { approvals }), ...(asks && { asks }), ...(f.waits && { waits: { ...f.waits, [task]: left } }) };
    }
    const items = f.items.filter((w) => w.id !== task);
    const waits = f.waits && Object.fromEntries(Object.entries(f.waits).filter(([t]) => t !== task));
    return {
      ...f,
      items,
      // Needs you's requests (access, key, key-drop) count too: they still wait
      count: items.length + (f.requests?.length ?? 0),
      ...(f.task_ids && { task_ids: f.task_ids.filter((t) => t !== task) }),
      ...(f.order && { order: f.order.filter((t) => t !== task) }),
      ...(waits && { waits }),
      ...(approvals && { approvals }),
      ...(asks && { asks }),
    };
  };
  return (cur) => {
    if (!isNeedsYouRead(cur)) return cur;
    if ('wakeAt' in (cur as object)) {
      const c = cur as { count?: unknown };
      if (!leaves || !row || typeof c.count !== 'number') return cur;
      return { ...c, count: read ? apply(read).count : Math.max(0, c.count - 1) };
    }
    return apply(cur as Flow);
  };
}

/** Needs you's two reads: the list, and the menu's count. */
const READS: readonly QueryKey[] = [flowQuery.queryKey, flowCountQuery.queryKey];

/** Stops what Needs you was reading (the revert is synchronous), so no read begun before lands over what follows. */
const stop = () => READS.forEach((queryKey) => void queryClient.cancelQueries({ queryKey, exact: true }));

/** Which answer a read keeps out while it is sent (data keepOut). */
const sendingKey = (task: string, wait: string | null) => `${task} ${wait ?? ''}`;

/** The task's row, what it waits on, its approvals and its asks, as `before` had them, put back into `cur` (a list read
    since): an answer refused after the list moved on. Rows read since stay; the row goes back before the first row
    that followed it then and is still listed. */
export function needsYouWith(task: string, before: Flow, cur: Flow): Flow {
  const row = before.items.find((w) => w.id === task);
  if (!row) return cur;
  const at = cur.items.findIndex((w) => w.id === task);
  let items: typeof cur.items;
  let order = cur.order;
  if (at >= 0) items = cur.items.map((w) => (w.id === task ? row : w));
  else {
    const was = latestOf(before);
    const next = was.slice(was.indexOf(task) + 1).find((id) => cur.items.some((w) => w.id === id) || cur.order?.includes(id));
    const i = next === undefined ? -1 : cur.items.findIndex((w) => w.id === next);
    items = i < 0 ? [...cur.items, row] : [...cur.items.slice(0, i), row, ...cur.items.slice(i)];
    if (cur.order) {
      const j = next === undefined ? -1 : cur.order.indexOf(next);
      order = j < 0 ? [...cur.order, task] : [...cur.order.slice(0, j), task, ...cur.order.slice(j)];
    }
  }
  // in a list of ids, before the first that followed it then
  const placed = (ids: readonly string[]) => {
    const was = latestOf(before);
    const next = was.slice(was.indexOf(task) + 1).find((id) => ids.includes(id));
    const j = next === undefined ? -1 : ids.indexOf(next);
    return j < 0 ? [...ids, task] : [...ids.slice(0, j), task, ...ids.slice(j)];
  };
  const mine = <T,>(list: readonly T[] | undefined, of: (x: T) => boolean) => [...(list ?? []).filter((x) => !of(x))];
  return {
    ...cur,
    items,
    count: items.length + (cur.requests?.length ?? 0),
    ...(order && { order }),
    ...(cur.task_ids && { task_ids: cur.task_ids.includes(task) ? cur.task_ids : placed(cur.task_ids) }),
    ...(cur.waits && before.waits?.[task] && { waits: { ...cur.waits, [task]: before.waits[task] } }),
    ...(cur.approvals && { approvals: [...mine(cur.approvals, (a) => a.id === task), ...(before.approvals ?? []).filter((a) => a.id === task)] }),
    ...(cur.asks && { asks: [...mine(cur.asks, (a) => a.task === task), ...(before.asks ?? []).filter((a) => a.task === task)] }),
  };
}

/** An answer to `wait` on `task` given (null: all of it; `how`: Approve or Request changes, which its card and row show
    while it is sent): it leaves Needs you's reads now (needsYouWithout), and stays out of every list read that lands
    until its reply is heard (settleNeedsYou, or the undo), worked out on each read, so an ask that read adds stays.
    Returns the undo for an answer never taken: each read as it was, or, where a newer read landed since, the task put
    back into it (needsYouWith), so it is listed again even when the read after the refusal fails. */
export function leaveNeedsYou(task: string, wait: string | null = null, how?: 'ok' | 'no'): () => void {
  stop();
  const change = needsYouWithout(task, wait);
  const key = sendingKey(task, wait);
  keepOut(key, { task, how, change: (cur) => (isNeedsYouRead(cur) && !('wakeAt' in (cur as object)) ? needsYouWithout(task, wait, cur as Flow)(cur) : change(cur)) });
  const was = READS.map((k) => queryClient.getQueryData(k));
  const now = READS.map((k) => queryClient.setQueryData(k, (cur: unknown) => (cur === undefined ? cur : change(cur))));
  return () => {
    letIn(key);
    READS.forEach((k, i) => {
      const cur = queryClient.getQueryData(k);
      if (cur === now[i]) queryClient.setQueryData(k, was[i]);
      else if (i === 0 && cur && was[0] && isNeedsYouRead(was[0])) queryClient.setQueryData(k, needsYouWith(task, was[0] as Flow, cur as Flow));
    });
  };
}

/** The answer's reply heard: what was read meanwhile is stopped, what the server took (`gone`) stays out of the list
    (the count already fell once), and each read is read again once (after a refusal too, which brings back what it
    took out). The only reads of Needs you after an answer. */
export function settleNeedsYou(gone?: { task: string; wait: string | null }): void {
  stop();
  if (gone) letIn(sendingKey(gone.task, gone.wait));
  if (gone) queryClient.setQueryData(flowQuery.queryKey, (cur) => (cur === undefined ? cur : (needsYouWithout(gone.task, gone.wait)(cur) as Flow)));
  for (const queryKey of READS) void queryClient.invalidateQueries({ queryKey, exact: true, refetchType: gone ? 'active' : 'all' });
}
