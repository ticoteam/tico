import { createFileRoute } from '@tanstack/react-router';
import { TaskPage } from '@/features/task/exports';
import { loadTask, taskSearch } from '@/features/task/routing';

/* `/flow/<n>`: the task inside Flow, with Flow's previous and next. The tab is in the search: `?tab=details`. */
export const Route = createFileRoute('/flow/$task')({
  validateSearch: taskSearch,
  loaderDeps: ({ search }) => ({ tab: search.tab }),
  loader: ({ params, deps, context: { queryClient } }) => loadTask(queryClient, params.task, deps.tab),
  component: FlowTaskRoute,
});

function FlowTaskRoute() {
  const { task } = Route.useParams();
  const { tab, file } = Route.useSearch();
  return <TaskPage task={task} tab={tab} file={file} flow />;
}
