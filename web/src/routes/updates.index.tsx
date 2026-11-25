import { createFileRoute } from '@tanstack/react-router';
import { UpdatesPage } from '@/features/threads/exports';
import { loadUpdates, updatesSearch } from '@/features/threads/routing';
import { pageSearch } from '@/lib';

export const Route = createFileRoute('/updates/')({
  validateSearch: updatesSearch,
  loaderDeps: ({ search }) => ({ open: search.open }),
  loader: ({ deps: { open }, context: { queryClient } }) => loadUpdates(queryClient, 'all', open),
  component: UpdatesRoute,
});

function UpdatesRoute() {
  const { open } = Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true });
  return <UpdatesPage tab="all" open={open} />;
}
