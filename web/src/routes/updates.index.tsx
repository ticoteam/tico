import { createFileRoute } from '@tanstack/react-router';
import { UpdatesPage } from '@/features/threads/exports';
import { loadUpdates, updatesSearch } from '@/features/threads/routing';
import { PAGE_SEARCH } from '@/lib';

export const Route = createFileRoute('/updates/')({
  validateSearch: updatesSearch,
  loaderDeps: ({ search }) => ({ open: search.open }),
  loader: ({ deps: { open }, context: { queryClient } }) => loadUpdates(queryClient, 'all', open),
  component: UpdatesRoute,
});

function UpdatesRoute() {
  const { open } = Route.useSearch(PAGE_SEARCH);
  return <UpdatesPage tab="all" open={open} />;
}
