import { createFileRoute, notFound } from '@tanstack/react-router';
import { UpdatesPage, loadUpdates, updatesSearch, type UpdateTab } from '@/features/threads/exports';

const TABS: readonly string[] = ['daily', 'weekly'] satisfies UpdateTab[];

/* Updates' Daily and Weekly tabs; All is /updates. */
export const Route = createFileRoute('/updates/$tab')({
  validateSearch: updatesSearch,
  beforeLoad: ({ params }) => {
    if (!TABS.includes(params.tab)) throw notFound();
  },
  loaderDeps: ({ search }) => ({ open: search.open }),
  loader: ({ params, deps: { open }, context: { queryClient } }) => loadUpdates(queryClient, params.tab as UpdateTab, open),
  component: UpdatesTabRoute,
});

function UpdatesTabRoute() {
  const { tab } = Route.useParams();
  const { open } = Route.useSearch();
  return <UpdatesPage tab={tab as UpdateTab} open={open} />;
}
