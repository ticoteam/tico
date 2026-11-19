import { useQuery } from '@tanstack/react-query';
import { Conversation, ticoRoomQuery } from '@/conversation';
import { meQuery, useActor } from '@/data';
import { Empty } from '@/kit/Empty';
import { dmQuery } from './queries';

/**
 * The chat with a teammate, the wide sheet of their page: the direct thread with them (made the first time), or
 * Tico's room on my own page. Only the root route can place it (the frame's chat place), so it is exported.
 */
export function TeammateChat({ who }: { who: string }) {
  const byKey = useActor(who);
  const byPerson = useActor(`human:${who}`);
  const actor = byKey ?? byPerson;
  const { data: me } = useQuery(meQuery);
  const mine = !!actor && actor.id === me?.id;
  const dm = useQuery({ ...dmQuery(actor?.id ?? ''), enabled: !!actor && !mine });
  const room = useQuery({ ...ticoRoomQuery, enabled: mine });
  const id = mine ? room.data : dm.data;
  if (!actor) return null;
  if (dm.isError || room.isError) return <Empty icon="alert" title="The chat did not load" />;
  if (!id) return null;
  return <Conversation id={id} variant="page" title={actor.name} actor={actor.id} />;
}

/** An agent's chat, by its slug. */
export function AgentChat({ slug }: { slug: string }) {
  return <TeammateChat who={slug} />;
}
