// Whether Tico is on for this person, and turning it on. The rule is the server's (backend/assistant.py
// `availability`): it is on when the assistant agent is active; only the owner may turn it on, and not while it is held.
import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import { queryClient, quietRoot, write } from '@/data';
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
