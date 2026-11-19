// Whether Tico is on for this person, and turning it on. The rule is the server's (backend/assistant.py
// `availability`): it is on when the assistant agent is active; only the owner may turn it on, and not while it is held.
import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { Assistant } from '@/api/schema';
import { QUIET_STALE_MS, queryClient, quietRoot, write } from '@/data';
import { ticoRoomQuery } from '@/conversation';
import { onRefetch } from '@/live';

// Read again after the person's own turn-on and when the stream is back.
quietRoot('tico');

export type TicoPower = Pick<Assistant, 'available' | 'bot' | 'can_turn_on' | 'state'> & {
  /** The owner's earlier chat (with the Strategy Owner), read only; null for anyone else or when it has nothing. */
  earlier?: Assistant['earlier'];
};

export const ticoPowerQuery = queryOptions({
  queryKey: ['tico', 'power'] as const,
  queryFn: async ({ signal }): Promise<TicoPower> => {
    const a = await api.get<Assistant>('/api/v2/assistant', { signal });
    return { available: a.available, bot: a.bot, can_turn_on: a.can_turn_on, state: a.state, earlier: a.earlier ?? null };
  },
  staleTime: QUIET_STALE_MS,
});

/* Someone turned the Assistant on or off: the server sends every stream a `bots` event naming it (backend/events.py
   `_bots`). The event's `status` is a status line, not the power, so this reads the power again instead. */
onRefetch<{ bot?: string }>('bots', (e) => {
  const slug = queryClient.getQueryData(ticoPowerQuery.queryKey)?.bot ?? 'assistant';
  return e.bot === slug ? [ticoPowerQuery.queryKey] : [];
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
