import { useQuery } from '@tanstack/react-query';
import { Conversation, MessageBones } from '@/conversation';
import { callCard } from '@/features/calls/exports';
import { approvalCard, useApprovalCards } from '@/features/task/exports';
import { Button } from '@/kit/Button';
import { Empty } from '@/kit/Empty';
import { isDm, othersOf } from './model';
import { useMe, useNameOf, useTitle } from './people';
import { chatQuery } from './queries';
import { chatActor } from '@/lib';

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
  // The face in the head: the agent or person the address names, or the other one in a DM.
  const actor = chatActor(chat) ?? (t && isDm(t) ? othersOf(t, me)[0] : undefined);
  // With an agent: what it asked you to approve, as cards among its messages.
  const approvals = useApprovalCards({ agent: actor });
  if (isError)
    return (
      <Empty icon="alert" title="This chat did not load">
        <Button onClick={() => void refetch()}>Try again</Button>
      </Empty>
    );
  if (!t) return <MessageBones />;
  const name = (actor && nameOf(actor)) || title;
  return <Conversation key={t.id} id={t.id} variant="side" title={name} actor={actor} card={(m) => approvalCard(m) ?? callCard(m)} extra={approvals} canPost={t.me?.can_post !== false} />;
}
