import { createFileRoute, redirect } from '@tanstack/react-router';
import { loadTask, TaskById, taskAddress } from '@/features/task/exports';

/* `/task/<n>`: a task by its number alone. Once its pipeline is known, its own address replaces this one. */
export const Route = createFileRoute('/task/$task')({
  loader: async ({ params, context: { queryClient } }) => {
    const t = await loadTask(queryClient, params.task);
    if (t) throw redirect({ href: taskAddress(t, undefined, false).to, replace: true });
  },
  component: TaskByIdRoute,
});

function TaskByIdRoute() {
  const { task } = Route.useParams();
  return <TaskById task={task} />;
}
