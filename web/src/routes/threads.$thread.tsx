import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads } from '@/features/threads/routing';

/* One thread. New message, Mail and Messaging have routes of their own, which win over this one. */
export const Route = createFileRoute('/threads/$thread')({
  loader: ({ params, context: { queryClient }, preload }) => loadThreads(queryClient, { thread: params.thread, preload }),
  component: ThreadRoute,
});

function ThreadRoute() {
  const { thread } = Route.useParams();
  return <ThreadsPage pane="thread" thread={thread} />;
}
