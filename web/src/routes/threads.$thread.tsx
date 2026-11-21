import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads } from '@/features/threads/routing';

interface ThreadSearch {
  /** A message to open the thread at: a link to one message (a relayed reply's "Open the reply"). */
  m?: string;
}

/* One thread. New message, Mail and Messaging have routes of their own, which win over this one. */
export const Route = createFileRoute('/threads/$thread')({
  validateSearch: (s: Record<string, unknown>): ThreadSearch => ({ m: typeof s.m === 'string' && s.m ? s.m : undefined }),
  loader: ({ params, context: { queryClient }, preload }) => loadThreads(queryClient, { thread: params.thread, preload }),
  component: ThreadRoute,
});

function ThreadRoute() {
  const { thread } = Route.useParams();
  const { m } = Route.useSearch();
  return <ThreadsPage pane="thread" thread={thread} message={m} />;
}
