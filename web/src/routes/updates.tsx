import { createFileRoute } from '@tanstack/react-router';
import { UpdatesPage, updateListQuery, updateQuery } from '@/features/threads/exports';

interface UpdatesSearch {
  /** An update open as its own page. */
  open?: string;
}

export const Route = createFileRoute('/updates')({
  validateSearch: (s: Record<string, unknown>): UpdatesSearch => ({ open: typeof s.open === 'string' && s.open ? s.open : undefined }),
  loaderDeps: ({ search }) => ({ open: search.open }),
  // A read that fails shows the page's own fault state, so the loader never throws.
  loader: ({ deps: { open }, context: { queryClient } }) =>
    (open ? queryClient.ensureQueryData(updateQuery(open)) : queryClient.ensureInfiniteQueryData(updateListQuery('all'))).catch(() => undefined),
  component: UpdatesRoute,
});

function UpdatesRoute() {
  const { open } = Route.useSearch();
  return <UpdatesPage tab="all" open={open} />;
}
