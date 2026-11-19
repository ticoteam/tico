import { createFileRoute } from '@tanstack/react-router';
import { TasksPage } from '@/features/tasks/exports';
import { filtersOf, pipelinesQuery, pipelineTasksQuery, validateTasksSearch } from '@/features/tasks/routing';

/** One pipeline: `?view=board|list|archived` and its filters live in the address. */
export const Route = createFileRoute('/tasks/$pipeline/')({
  validateSearch: validateTasksSearch,
  loaderDeps: ({ search }) => ({ ...filtersOf(search), archived: search.view === 'archived' }),
  // A failed read is the page's own fault state (Try again), not the router's error screen.
  // Archived reads its own three lists on the page.
  loader: async ({ context, params, deps: { archived, ...filters } }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(pipelinesQuery),
      archived ? null : context.queryClient.ensureQueryData(pipelineTasksQuery(params.pipeline, filters)),
    ]).catch(() => undefined);
  },
  component: Page,
});

function Page() {
  const { pipeline } = Route.useParams();
  return <TasksPage pipeline={pipeline} search={Route.useSearch()} />;
}
