import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads, threadsSearch } from '@/features/threads/routing';

export const Route = createFileRoute('/threads/new')({
  validateSearch: threadsSearch,
  loaderDeps: ({ search }) => ({ q: search.q }),
  loader: ({ deps: { q }, context: { queryClient } }) => loadThreads(queryClient, { q }),
  component: NewRoute,
});

function NewRoute() {
  const { q } = Route.useSearch();
  return <ThreadsPage pane="new" q={q} open />;
}
