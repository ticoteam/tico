import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage, loadThreads, threadsSearch } from '@/features/threads/exports';

/* One thread. New message, Catch up, Mail and Messaging have routes of their own, which win over this one. */
export const Route = createFileRoute('/threads/$thread')({
  validateSearch: threadsSearch,
  loaderDeps: ({ search }) => ({ q: search.q }),
  loader: ({ params, deps: { q }, context: { queryClient } }) => loadThreads(queryClient, { q, thread: params.thread }),
  component: ThreadRoute,
});

function ThreadRoute() {
  const { thread } = Route.useParams();
  const { q } = Route.useSearch();
  return <ThreadsPage pane="thread" thread={thread} q={q} open />;
}
