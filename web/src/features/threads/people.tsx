import { useQuery } from '@tanstack/react-query';
import { useLocation } from '@tanstack/react-router';
import { refusal, run } from '@/actions';
import { directoryQuery, meQuery, useActor } from '@/data';
import { Avatar, type AvatarSize } from '@/kit/Avatar';
import { OwnerButton } from '@/kit/OwnerButton';
import { toast } from '@/kit/Toast';
import { chatAddress } from '@/lib';
import { titleOf, type Thread } from './model';

const bare = (actor: string) => actor.slice(actor.indexOf(':') + 1);

/** A name for any actor id, from the directory. */
export function useNameOf(): (actor: string) => string | undefined {
  const { data } = useQuery(directoryQuery);
  return (actor) => data?.byId[actor]?.name;
}

export function useMe(): string | undefined {
  return useQuery(meQuery).data?.id;
}

/** The thread's one name, as every place shows it. */
export function useTitle(t: Thread | undefined): string {
  const me = useMe();
  const nameOf = useNameOf();
  return t ? titleOf(t, me, nameOf) : '';
}

/** A teammate's face from the directory; a person outside it is a plain circle with their initials. */
export function Face({ actor, size = 22, name }: { actor: string; size?: AvatarSize; name?: string }) {
  const a = useActor(actor);
  const kind = a?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person');
  return <Avatar kind={kind} name={a?.name ?? name ?? bare(actor)} hue={a?.hue} live={a?.working} size={size} />;
}

/** Press an action as the person's click; say so when the server refuses it. */
export async function press(id: string, input: unknown): Promise<boolean> {
  const r = await run(id, input, { via: 'click' });
  if (!r.ok) toast(refusal(r, "Couldn't save"));
  return r.ok;
}

/** A teammate's face in a head that opens their chat beside the page (an update's writer, a mailbox's agent). */
export function ChatWith({ actor }: { actor: string }) {
  const who = useActor(actor);
  const me = useMe();
  // `chat` is the shell's, in no route's search schema: built as an address, it keeps the page's own search.
  const at = chatAddress(actor, who?.kind);
  const href = useLocation({
    select: (l) => {
      const q = new URLSearchParams(l.searchStr);
      q.set('chat', at);
      return `${l.pathname}?${q.toString()}`;
    },
  });
  const on = useLocation({ select: (l) => new URLSearchParams(l.searchStr).get('chat') === at });
  if (actor === me) return null;
  const kind = who?.kind ?? (actor.startsWith('bot:') ? 'agent' : 'person');
  return <OwnerButton name={who?.name ?? bare(actor)} kind={kind} hue={who?.hue} live={who?.working} href={href} on={on} />;
}
