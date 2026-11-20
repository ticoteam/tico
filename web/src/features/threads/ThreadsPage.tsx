import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Conversation } from '@/conversation';
import { callCard } from '@/features/calls/exports';
import { Empty } from '@/kit/Empty';
import { Icon } from '@/kit/Icon';
import { IconButton } from '@/kit/IconButton';
import { ItemHead, type PageTab } from '@/pagekinds';
import { NewMessage, NewMessageFoot, useNewDraft } from './NewMessage';
import { press, useTitle } from './people';
import { threadQuery } from './queries';
import { ThreadHead } from './ThreadHead';
import './threads.css';

/** Mail and Messaging's tabs: the sources of Threads, beside the threads themselves (whose list is the menu's). */
export function useThreadTabs(here: string): PageTab[] {
  return [
    { label: 'Threads', to: here, target: 'tab:threads' },
    { label: 'Mail', to: '/threads/mail', target: 'tab:mail' },
    { label: 'Messaging', to: '/threads/messaging', target: 'tab:messaging' },
  ];
}

export const NewButton = () => <IconButton icon="message" label="Message" data-phone onClick={() => void press('threads.compose', {})} />;

export interface ThreadsPageProps {
  /** What the page holds: the thread `thread`, New message, or nothing open yet. */
  pane: 'thread' | 'new' | 'none';
  thread?: string;
}

/** Threads: one open conversation. Its list is anchored in the menu's Threads tab (plan/threads-team.html). */
export function ThreadsPage({ pane, thread }: ThreadsPageProps) {
  const label = pane === 'new' ? 'New message' : 'Thread';
  return (
    <main className="pane page page-thread pk pk-split is-open" aria-label={label}>
      <section className="conv" aria-label={label}>
        {pane === 'thread' && thread ? (
          <>
            <ThreadHead id={thread} />
            <ThreadConversation id={thread} />
          </>
        ) : pane === 'new' ? (
          <NewPane />
        ) : (
          <div className="conv-body">
            <Empty icon="threads" title="No thread open" />
          </div>
        )}
      </section>
    </main>
  );
}

function ThreadConversation({ id }: { id: string }) {
  const { data } = useQuery(threadQuery(id));
  const title = useTitle(data);
  return <Conversation id={id} variant="page" title={title || undefined} card={callCard} canPost={data?.me?.can_post !== false} />;
}

/* ---------------------------------------------------------------- New message */

function NewPane() {
  const draft = useNewDraft();
  return (
    <>
      <ItemHead
        title="New message"
        target="page:/threads/new"
        actions={
          <Link className="ib" to="/threads" aria-label="Close" title="Close">
            <Icon name="close" />
          </Link>
        }
      />
      <div className="conv-body" data-scroll>
        <div className="conv-col">
          <NewMessage draft={draft} />
        </div>
      </div>
      <footer className="conv-foot">
        <div className="conv-col">
          <NewMessageFoot draft={draft} />
        </div>
      </footer>
    </>
  );
}
