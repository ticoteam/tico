// Whether Tico is on for this person, and turning it on. The rule is the server's (backend/assistant.py
// `availability`): it is on when the assistant agent is active; only the owner may turn it on, and not while it is held.
import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetPreferenceResponse, Task } from '@/api/schema';
import { keys, queryClient, quietRoot, write } from '@/data';
import { readTicoInfo, ticoRoomQuery, type TicoInfo } from '@/conversation';
import { onRefetch } from '@/live';
import { backSince, finishedAt } from './tracked';

// Read again after the person's own turn-on and when the stream is back.
quietRoot('tico');

export type TicoPower = Omit<TicoInfo, 'room_id'>;

/** Whether Tico is on; read with its room (conversation `readTicoInfo`), so the two cost one read. */
export const ticoPowerQuery = queryOptions({
  queryKey: ['tico', 'power'] as const,
  queryFn: async (): Promise<TicoPower> => {
    const { available, bot, can_turn_on, state } = await readTicoInfo();
    return { available, bot, can_turn_on, state };
  },
});

/* Someone turned the Assistant on or off: the server sends every stream a `bots` event naming it (backend/events.py
   `_bots`). The event's `status` is a status line, not the power, so this reads the power again instead, and the room
   too while there is none yet (turning it on opens one). Most of these events are the status line moving while Tico
   works; one whose agent state (`bot_state`) is the one already known changed nothing here, and reads nothing. */
onRefetch<{ bot?: string; status?: { bot_state?: string | null } | null }>('bots', (e) => {
  const power = queryClient.getQueryData(ticoPowerQuery.queryKey);
  const slug = power?.bot ?? 'assistant';
  if (e.bot !== slug) return [];
  if (power && e.status?.bot_state != null && e.status.bot_state === power.state) return [];
  return queryClient.getQueryData(ticoRoomQuery.queryKey)
    ? [ticoPowerQuery.queryKey]
    : [ticoPowerQuery.queryKey, ticoRoomQuery.queryKey];
});

/** Turns Tico on; resolves with its state ('active', or 'planned' when it still needs a computer). */
export async function turnOnTico(): Promise<string> {
  const answer = await write({
    key: ticoPowerQuery.queryKey,
    // nothing to show first: with no computer it stays off, and the answer says so
    optimistic: (current: TicoPower | undefined) => current,
    send: (idempotencyKey) => api.post<{ state?: string }>('/api/v2/assistant/turn-on', {}, { idempotencyKey }),
  });
  // On, the assistant's GET opens this person's room: read it again so the chat appears.
  void queryClient.invalidateQueries({ queryKey: ticoRoomQuery.queryKey });
  return answer.state ?? '';
}

/* ---------------------------------------------------------------- the tracker (Tracker.tsx) */

/** A tracked task's fields: GET /api/v2/tasks brief rows (done_at and closed_at only once set). */
export type TrackedTask = Pick<Task, 'id' | 'number' | 'title' | 'status' | 'pipeline_id' | 'owner_id' | 'updated_at'> & {
  done_at?: string | null;
  closed_at?: string | null;
};
interface TrackedList {
  tasks: TrackedTask[];
  cursor?: string;
  next_after?: string | null;
}

/* Both lists sit under the tasks root, so the stream's tasks events patch them in place (live/patch.ts). They name the
   person by actor id, never `me`, and send exact: a requester the server does not know answers 422, never every task. */
const trackedParams = (me: string) => ({ requester: me, status: 'open', snoozed: 'all', exact: 'true', brief: 'true', limit: 200 });
const backParams = (me: string) => ({ requester: me, status: 'done,closed', sort: 'finished', snoozed: 'all', exact: 'true', brief: 'true', limit: 50 });

/** How many pages a list reads before it leaves the rest. */
const PAGES = 50;

/**
 * A task list page after page (each names the next with `next_after`, passed back as `after`) in one cache entry,
 * until there is no next page or `enough` says the rows read so far suffice. The first page's `cursor` stays: the
 * stream's changes after it apply again, which is harmless.
 */
async function pages(params: Record<string, string | number>, signal: AbortSignal, enough?: (rows: TrackedTask[]) => boolean): Promise<TrackedList> {
  const read = (after?: string) => {
    const q = new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]));
    if (after) q.set('after', after);
    return api.get<TrackedList>(`/api/v2/tasks?${q}`, { signal });
  };
  const first = await read();
  let tasks = first.tasks;
  let after = first.next_after;
  for (let n = 1; after && n < PAGES && !enough?.(tasks); n++) {
    const page = await read(after);
    const seen = new Set(tasks.map((t) => t.id));
    tasks = [...tasks, ...page.tasks.filter((t) => !seen.has(t.id))];
    after = page.next_after;
  }
  return { ...first, tasks, next_after: null };
}

/** The open tasks the person asked for (`me`: their actor id), every page: the ones they hold are left out after. */
export const trackedQuery = (me: string) => {
  const params = trackedParams(me);
  return queryOptions({ queryKey: keys.tasks.list(params), queryFn: ({ signal }) => pages(params, signal) });
};

/** What came back: the tasks the person asked for, most recently finished first, read back as far as Back ever looks.
    The one read Back counts from; report-back messages can take its place without changing the tracker's groups. */
export const backQuery = (me: string) => {
  const params = backParams(me);
  return queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => {
      const floor = Date.parse(backSince(null, Date.now()));
      return pages(params, signal, (rows) => rows.length > 0 && Date.parse(finishedAt(rows[rows.length - 1]!)) <= floor);
    },
  });
};

const SEEN_KEY = 'ui.tracker_seen_at';

/** When the person last opened the tracker (preference ui.tracker_seen_at); null before the first time. */
export const trackerSeenQuery = queryOptions({
  queryKey: ['tico', 'tracker', 'seen'] as const,
  queryFn: async ({ signal }) => (await api.get<GetPreferenceResponse>(`/api/v2/preferences/${SEEN_KEY}`, { signal })).value as string | null,
});

/** Opening the tracker: Back starts counting again from `at`. */
export function markTrackerSeen(at: string): Promise<unknown> {
  return write({
    key: trackerSeenQuery.queryKey,
    optimistic: () => at,
    send: (idempotencyKey) => api.post(`/api/v2/preferences/${SEEN_KEY}`, { value: at }, { idempotencyKey }),
  });
}
