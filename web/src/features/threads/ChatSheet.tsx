import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { isDm, othersOf } from './model';
import { useMe, useNameOf, useTitle } from './people';
import { chatQuery } from './queries';

/** The face in the sheet's head: the agent or person the address names, or the other one in a DM. */
function actorOf(chat: string): string | undefined {
  const [kind, ...rest] = chat.split(':');
  const id = rest.join(':');
  if (kind === 'agent') return `bot:${id}`;
  if (kind === 'person') return `human:${id}`;
  return undefined;
}

export interface ChatSheetProps {
  /** The shell's `?chat=`: agent:<slug>, person:<id> or thread:<id>. */
  chat: string;
}

/** The second chat's body: the address resolved to a thread, drawn as the side conversation (its head and the
    conversation). The root puts it in the shell's chat sheet. */
export function ChatSheet({ chat }: ChatSheetProps) {
  const { data: t, isError, refetch } = useQuery(chatQuery(chat));
  const me = useMe();
  const nameOf = useNameOf();
  const title = useTitle(t);
  if (isError)
    return (
      <Empty icon="alert" title="This chat did not load">
        <Button onClick={() => void refetch()}>Try again</Button>
      </Empty>
    );
  if (!t) return null;
  const actor = actorOf(chat) ?? (isDm(t) ? othersOf(t, me)[0] : undefined);
  const name = (actor && nameOf(actor)) || title;
  return <Conversation key={t.id} id={t.id} variant="side" title={name} actor={actor} />;
}
