import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { meQuery, useActor } from '@/data';
import { Empty } from '@/kit/Empty';
import { dmQuery } from './queries';

/**
 * The chat with a teammate, the wide sheet of their page: the direct thread with them (made the first time). Only the
 * root route can place it (the frame's chat place), so it is exported. On my own page there is none: I do not chat with
 * myself, and my assistant is Tico at the far left of every page (threads.js: the Assistant's own page is gone).
 */
export function TeammateChat({ who }: { who: string }) {
  const byKey = useActor(who);
  const byPerson = useActor(`human:${who}`);
  const actor = byKey ?? byPerson;
  const me = useQuery(meQuery);
  // Until I know who I am, any teammate could be me: the server refuses a DM with yourself (422).
  const other = !!actor && !!me.data && actor.id !== me.data.id;
  const dm = useQuery({ ...dmQuery(actor?.id ?? ''), enabled: other });
  if (!other) return null;
  if (dm.isError) return <Empty icon="alert" title="The chat did not load" />;
  if (!dm.data) return null;
  return <Conversation id={dm.data} variant="page" title={actor.name} actor={actor.id} />;
}

/** An agent's chat, by its slug. */
export function AgentChat({ slug }: { slug: string }) {
  return <TeammateChat who={slug} />;
}
