import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { isDm, othersOf } from './model';
import { useMe, useNameOf, useTitle } from './people';
import { chatQuery } from './queries';
import './threads.css';

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

/**
 * The second chat: the address resolved to a thread, drawn as the side conversation. Features may not import
 * `@/shell`, so the sheet's own element (shell `Sheet kind="chat"`) is drawn here.
 */
export function ChatSheet({ chat }: ChatSheetProps) {
  const { data: t } = useQuery(chatQuery(chat));
  const label = useTitle(t);
  return (
    <aside className="pane side chat chat-2" aria-label={label ? `Chat with ${label}` : 'Chat'}>
      <ChatBody chat={chat} />
    </aside>
  );
}

function ChatBody({ chat }: ChatSheetProps) {
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
