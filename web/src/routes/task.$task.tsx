import { createFileRoute, redirect } from '@tanstack/react-router';
import { TaskById } from '@/features/task/exports';
import { loadTask } from '@/features/task/routing';

/* `/task/<n>`: a task by its number alone. Once its pipeline is known, its own address replaces this one, on the tab
   the address names (`?tab=`) when it has one. */
export const Route = createFileRoute('/task/$task')({
  loader: async ({ params, location, preload, context: { queryClient } }) => {
    const { tab: named, ...search } = location.search as Record<string, unknown>;
    const loaded = await loadTask(queryClient, params.task, typeof named === 'string' && named ? named : undefined, !preload && !('chat' in search));
    if (!loaded) return;
    const { task, tab } = loaded;
    throw redirect({
      to: '/tasks/$pipeline/$task/$tab',
      params: { pipeline: task.pipeline_id ?? 'all', task: String(task.number ?? task.id), tab },
      // what the shell keeps (a thread beside it, lib besideThread) stays
      search: search as never,
      replace: true,
    });
  },
  component: TaskByIdRoute,
});

function TaskByIdRoute() {
  const { task } = Route.useParams();
  return <TaskById task={task} />;
}
