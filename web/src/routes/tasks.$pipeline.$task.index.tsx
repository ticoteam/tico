import { createFileRoute, redirect } from '@tanstack/react-router';
import { TaskPage } from '@/features/task/exports';
import { loadTask, taskSearch } from '@/features/task/routing';

/* `/tasks/<pipeline>/<n>`: the task, on the page that waits on the person (or its first). */
export const Route = createFileRoute('/tasks/$pipeline/$task/')({
  validateSearch: taskSearch,
  loaderDeps: ({ search }) => search,
  loader: async ({ params, deps, location, preload, context: { queryClient } }) => {
    const loaded = await loadTask(queryClient, params.task, undefined, !preload && !('chat' in location.search));
    if (!loaded) return;
    const { task, tab } = loaded;
    // Resolve the address before mounting the reader so replacing it cannot restart the first page reads.
    throw redirect({
      to: '/tasks/$pipeline/$task/$tab',
      params: { pipeline: task.pipeline_id ?? 'all', task: String(task.number ?? task.id), tab },
      search: deps,
      replace: true,
    });
  },
  component: TaskRoute,
});

function TaskRoute() {
  const { pipeline, task } = Route.useParams();
  const { file } = Route.useSearch();
  return <TaskPage task={task} pipeline={pipeline} file={file} />;
}
