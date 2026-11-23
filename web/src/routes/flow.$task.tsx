import { createFileRoute, redirect } from '@tanstack/react-router';

/* An old Flow link to a task: the task's own address, with its tab, ask, version and chat. */
export const Route = createFileRoute('/flow/$task')({
  beforeLoad: ({ params, search }) => {
    throw redirect({ to: '/task/$task', params, search: search as never, replace: true });
  },
  component: () => null,
});
