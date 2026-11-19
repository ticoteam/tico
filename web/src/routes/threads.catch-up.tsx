import { createFileRoute, redirect } from '@tanstack/react-router';

/* Catch up is Tico's now (its chip on Tico's box): an old link lands on Threads. */
export const Route = createFileRoute('/threads/catch-up')({
  beforeLoad: () => {
    throw redirect({ to: '/threads', replace: true });
  },
});
