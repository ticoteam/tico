// Whether Tico is on for this person, and turning it on. The rule is the server's (backend/assistant.py
// `availability`): it is on when the assistant agent is active; only the owner may turn it on, and not while it is held.
import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetPreferenceResponse, Task } from '@/api/schema';
import { keys, queryClient, quietRoot, write } from '@/data';
import { readTicoInfo, ticoRoomQuery, type TicoInfo } from '@/conversation';
import { onRefetch } from '@/live';

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
}

/* Both lists sit under the tasks root, so the stream's tasks events patch them in place (live/patch.ts). They name the
   person by actor id, never `me`, and send exact: a requester the server does not know answers 422, never every task. */
const trackedParams = (me: string) => ({ requester: me, status: 'open', snoozed: 'all', exact: 'true', brief: 'true', limit: 500 });
const backParams = (me: string) => ({ requester: me, status: 'done,closed', sort: 'finished', snoozed: 'all', exact: 'true', brief: 'true', limit: 50 });

const taskList = (params: Record<string, string | number>) =>
  queryOptions({
    queryKey: keys.tasks.list(params),
    queryFn: ({ signal }) => api.get<TrackedList>(`/api/v2/tasks?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`, { signal }),
  });

/** The open tasks the person asked for (`me`: their actor id). */
export const trackedQuery = (me: string) => taskList(trackedParams(me));

/** What came back: the tasks the person asked for, most recently finished first. The one read Back counts from;
    report-back messages can take its place without changing the tracker's groups. */
export const backQuery = (me: string) => taskList(backParams(me));

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
