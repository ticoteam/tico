import { createFileRoute } from '@tanstack/react-router';
import { TaskPage } from '@/features/task/exports';
import { loadTask, taskSearch } from '@/features/task/routing';

/* `/tasks/<pipeline>/<n>/<tab>`: one of the task's pages, or Details (with `?file=` for a file opened from it). */
export const Route = createFileRoute('/tasks/$pipeline/$task/$tab')({
  validateSearch: taskSearch,
  loader: ({ params, context: { queryClient } }) => loadTask(queryClient, params.task, params.tab),
  component: TaskTabRoute,
});

function TaskTabRoute() {
  const { pipeline, task, tab } = Route.useParams();
  const { file } = Route.useSearch();
  return <TaskPage task={task} pipeline={pipeline} tab={tab} file={file} />;
}
