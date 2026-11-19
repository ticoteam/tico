import { createFileRoute } from '@tanstack/react-router';
import { filtersOf, pipelinesQuery, pipelineTasksQuery, TasksPage, validateTasksSearch } from '@/features/tasks/exports';

/** One pipeline: `?view=board|list` and its filters (`owner`, `tag`, `late`, `q`) live in the address. */
export const Route = createFileRoute('/tasks/$pipeline/')({
  validateSearch: validateTasksSearch,
  loaderDeps: ({ search }) => filtersOf(search),
  // A failed read is the page's own fault state (Try again), not the router's error screen.
  loader: async ({ context, params, deps }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(pipelinesQuery),
      context.queryClient.ensureQueryData(pipelineTasksQuery(params.pipeline, deps)),
    ]).catch(() => undefined);
  },
  component: Page,
});

function Page() {
  const { pipeline } = Route.useParams();
  return <TasksPage pipeline={pipeline} search={Route.useSearch()} />;
}
