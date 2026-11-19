import { createFileRoute } from '@tanstack/react-router';
import { UpdatesPage, loadUpdates, updatesSearch } from '@/features/threads/exports';

export const Route = createFileRoute('/updates/')({
  validateSearch: updatesSearch,
  loaderDeps: ({ search }) => ({ open: search.open }),
  loader: ({ deps: { open }, context: { queryClient } }) => loadUpdates(queryClient, 'all', open),
  component: UpdatesRoute,
});

function UpdatesRoute() {
  const { open } = Route.useSearch();
  return <UpdatesPage tab="all" open={open} />;
}
