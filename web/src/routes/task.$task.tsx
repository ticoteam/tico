import { createFileRoute, redirect } from '@tanstack/react-router';
import { TaskById } from '@/features/task/exports';
import { ownAddress } from '@/features/task/routing';

/* `/task/<n>`: a task by its number alone. Once its pipeline is known, its own address replaces this one, on the tab
   the address names (`?tab=`) when it has one. What the shell keeps (a thread beside it, lib besideThread) stays. */
export const Route = createFileRoute('/task/$task')({
  loader: async ({ params, location, preload, context: { queryClient } }) => {
    const search = location.search as Record<string, unknown>;
    const to = await ownAddress(queryClient, params.task, search, !preload && !('chat' in search));
    if (to) throw redirect({ ...to, search: to.search as never, replace: true });
  },
  component: TaskByIdRoute,
});

function TaskByIdRoute() {
  const { task } = Route.useParams();
  return <TaskById key={task} task={task} />;
}
