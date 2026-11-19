import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads, threadsSearch } from '@/features/threads/routing';

export const Route = createFileRoute('/threads/')({
  validateSearch: threadsSearch,
  loaderDeps: ({ search }) => ({ q: search.q }),
  loader: ({ deps: { q }, context: { queryClient } }) => loadThreads(queryClient, { q, catchUp: true }),
  component: ThreadsIndex,
});

function ThreadsIndex() {
  const { q } = Route.useSearch();
  return <ThreadsPage pane="catch-up" q={q} />;
}
