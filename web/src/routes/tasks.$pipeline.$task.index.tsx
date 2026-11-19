import { createFileRoute } from '@tanstack/react-router';
import { loadTask, TaskPage, taskSearch } from '@/features/task/exports';

/* `/tasks/<pipeline>/<n>`: the task, on the page that waits on the person (or its first). */
export const Route = createFileRoute('/tasks/$pipeline/$task/')({
  validateSearch: taskSearch,
  loader: ({ params, context: { queryClient } }) => loadTask(queryClient, params.task),
  component: TaskRoute,
});

function TaskRoute() {
  const { pipeline, task } = Route.useParams();
  const { file } = Route.useSearch();
  return <TaskPage task={task} pipeline={pipeline} file={file} />;
}
