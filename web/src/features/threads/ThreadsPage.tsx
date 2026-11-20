import { useQuery } from '@tanstack/react-query';
import { Conversation } from '@/conversation';
import { callCard } from '@/features/calls/exports';
import { Empty } from '@/kit/Empty';
import { AgentBox } from './AgentBox';
import { outsideOf, teamOf } from './model';
import { useTitle } from './people';
import { threadQuery } from './queries';
import { TeamThread } from './TeamThread';
import { ThreadHead } from './ThreadHead';
import './threads.css';

export interface ThreadsPageProps {
  /** What the page holds: the thread `thread`, or nothing open yet. */
  pane: 'thread' | 'none';
  thread?: string;
}

/** Threads: one open conversation. Its list is anchored in the menu's Threads tab (plan/threads-team.html). */
export function ThreadsPage({ pane, thread }: ThreadsPageProps) {
  return (
    <main className="pane page page-thread pk pk-split is-open" aria-label="Thread">
      <section className="conv" aria-label="Thread">
        {pane === 'thread' && thread ? (
          <>
            <ThreadHead id={thread} />
            <ThreadConversation id={thread} />
          </>
        ) : (
          <div className="conv-body">
            <Empty icon="threads" title="No thread open" />
          </div>
        )}
      </section>
    </main>
  );
}

/** A team's thread draws its updates and meetings as cards; a thread from outside has a box only to its agent. */
function ThreadConversation({ id }: { id: string }) {
  const { data } = useQuery(threadQuery(id));
  const title = useTitle(data);
  const team = data && teamOf(data);
  if (team) return <TeamThread who={team} />;
  const outside = outsideOf(data);
  return (
    <Conversation
      id={id}
      variant="page"
      title={title || undefined}
      card={callCard}
      canPost={!outside && data?.me?.can_post !== false}
      foot={outside?.handler ? <AgentBox agent={outside.handler} page={{ path: `/threads/${id}`, title }} /> : undefined}
    />
  );
}
