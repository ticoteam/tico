import { createFileRoute, redirect } from '@tanstack/react-router';

/* An old Flow link: Tasks, where Needs you is on top; one that named a view (All) opens that view. The shell's own
   words (an open chat) come along. */
export const Route = createFileRoute('/flow/')({
  beforeLoad: ({ search }) => {
    const { view, ...shell } = search as Record<string, unknown>;
    if (view === 'all' || view === 'snoozed' || view === 'done') throw redirect({ to: '/needs-you', search: { ...shell, view } as never, replace: true });
    throw redirect({ to: '/tasks', search: shell as never, replace: true });
  },
  component: () => null,
});
