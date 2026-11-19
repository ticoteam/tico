import { createFileRoute } from '@tanstack/react-router';
import { loadTask, TaskPage, taskSearch } from '@/features/task/exports';

/* `/flow/<n>`: the task inside Flow, with Flow's previous and next. The tab is in the search: `?tab=details`. */
export const Route = createFileRoute('/flow/$task')({
  validateSearch: taskSearch,
  loader: ({ params, context: { queryClient } }) => loadTask(queryClient, params.task),
  component: FlowTaskRoute,
});

function FlowTaskRoute() {
  const { task } = Route.useParams();
  const { tab, file } = Route.useSearch();
  return <TaskPage task={task} tab={tab} file={file} flow />;
}
