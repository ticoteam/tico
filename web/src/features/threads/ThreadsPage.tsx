import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { callCard } from '@/features/calls/exports';
import { approvalCard, useApprovalCards } from '@/features/task/exports';
import { AgentBox } from './AgentBox';
import { agentOf, outsideOf, teamOf } from './model';
import { useMe, useTitle } from './people';
import { threadQuery } from './queries';
import { TeamThread } from './TeamThread';
import { ThreadHead } from './ThreadHead';
import { ThreadPane, ThreadsIndex } from './ThreadPane';

export interface ThreadsPageProps {
  /** What the page holds: the thread `thread`, or nothing open yet. */
  pane: 'thread' | 'none';
  thread?: string;
}

/** Threads: one open conversation. Its list is anchored in the menu's Threads tab (plan/threads-team.html). */
export function ThreadsPage({ pane, thread }: ThreadsPageProps) {
  if (pane === 'none' || !thread) return <ThreadsIndex />;
  return (
    <ThreadPane>
      <ThreadHead id={thread} />
      <ThreadConversation id={thread} />
    </ThreadPane>
  );
}

/** A team's thread draws its updates and meetings as cards; a thread from outside has a box only to its agent. */
function ThreadConversation({ id }: { id: string }) {
  const { data } = useQuery(threadQuery(id));
  const title = useTitle(data);
  // A thread with an agent shows what it asked you to approve, as cards among its messages; a card's page opens
  // beside this thread.
  const approvals = useApprovalCards({ agent: agentOf(data, useMe()) }, `thread:${id}`);
  const team = data && teamOf(data);
  if (team) return <TeamThread who={team} />;
  const outside = outsideOf(data);
  return (
    <Conversation
      id={id}
      variant="page"
      title={title || undefined}
      card={(m) => approvalCard(m) ?? callCard(m)}
      extra={approvals}
      canPost={!outside && data?.me?.can_post !== false}
      foot={outside?.handler ? <AgentBox agent={outside.handler} from={{ page: 'thread', label: 'Thread', item: { kind: 'thread', id } }} /> : undefined}
    />
  );
}
