import { createFileRoute } from '@tanstack/react-router';
import { TasksPage, TasksPending } from '@/features/tasks/exports';
import { pageSearch } from '@/lib';
import { filtersOf, pipelinesQuery, pipelineTasksQuery, shelfQuery, validateTasksSearch } from '@/features/tasks/routing';

/** One pipeline: `?view=board|list|archived` and its filters live in the address. */
export const Route = createFileRoute('/tasks/$pipeline/')({
  validateSearch: validateTasksSearch,
  loaderDeps: ({ search }) => ({ ...filtersOf(search), archived: search.view === 'archived' }),
  loader: ({ context: { queryClient: qc }, params: { pipeline }, deps: { archived, ...filters } }) =>
    Promise.all([
      qc.ensureQueryData(pipelinesQuery),
      ...(archived
        ? (['snoozed', 'done', 'closed'] as const).map((shelf) => qc.ensureQueryData(shelfQuery(pipeline, shelf, filters)))
        : [qc.ensureQueryData(pipelineTasksQuery(pipeline, filters))]),
    ]),
  pendingComponent: TasksPending,
  component: Page,
});

function Page() {
  const { pipeline } = Route.useParams();
  // the board's own words: a task opened over it, or its tab changing, draws nothing here
  const search = Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true });
  return <TasksPage pipeline={pipeline} search={search} />;
}
