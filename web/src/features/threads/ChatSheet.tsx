import { useQuery } from '@tanstack/react-query';
import { Conversation, MessageBones, SideHead, type FromPage, type Session } from '@/conversation';
import { callCard } from '@/features/calls/exports';
import { approvalCard, useApprovalCards } from '@/features/task/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { agentOf, isDm, othersOf } from './model';
import { useMe, useNameOf, useTitle } from './people';
import { chatQuery } from './queries';
import { chatActor } from '@/lib';

export interface ChatSheetProps {
  /** The shell's `?chat=`: agent:<slug>, person:<id> or thread:<id>; or the page owner's, in the Agent bar. */
  chat: string;
  /** The Agent bar's: the page its messages carry, the words to start with while it is empty, and `dm`: each message
      goes to the viewer's DM with exactly this agent, which the server finds as it writes. */
  from?: FromPage;
  starters?: readonly string[];
  dm?: boolean;
}

/** The second chat's body: the address resolved to a thread, drawn as the side conversation (its head and the
    conversation). The root puts it in the shell's chat sheet. */
export function ChatSheet({ chat, from, starters, dm = false }: ChatSheetProps) {
  const { data: t, isError, refetch } = useQuery(chatQuery(chat, dm ? 'private' : undefined));
  const me = useMe();
  const nameOf = useNameOf();
  const title = useTitle(t);
  // The face in the head: the agent or person the address names, or the other one in a DM.
  const actor = chatActor(chat) ?? (t && isDm(t) ? othersOf(t, me)[0] : undefined);
  // With an agent: what it asked you to approve, as cards among its messages; a card's page opens beside this chat.
  const approvals = useApprovalCards({ agent: actor }, chat);
  if (isError)
    return (
      <>
        {/* the head stays, with the frame's fold or close control */}
        <SideHead actor={actor} name={(actor && nameOf(actor)) || 'Chat'} />
        <Empty icon="alert" title="This chat did not load">
          <Button onClick={() => refetch()}>Try again</Button>
        </Empty>
      </>
    );
  if (!t) return <MessageBones />;
  const name = (actor && nameOf(actor)) || title;
  // Under the name, which chat this is: a task's own (unless the head already names it by the task), or an agent's main chat.
  const session: Session | undefined = t.task_id
    ? name !== title
      ? { task: String(t.task_id), title }
      : undefined
    : chatActor(chat)?.startsWith('bot:') || agentOf(t, me)
      ? 'main'
      : undefined;
  return (
    <Conversation
      key={t.id}
      id={t.id}
      variant="side"
      chat={chat}
      title={name}
      actor={actor}
      session={session}
      card={(m) => approvalCard(m) ?? callCard(m)}
      extra={approvals}
      canPost={t.me?.can_post !== false}
      from={from}
      starters={starters}
      {...(dm && actor && { toDm: actor, onMoved: () => void refetch() })}
    />
  );
}
