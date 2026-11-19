import { createFileRoute, redirect } from '@tanstack/react-router';

// Library has no page of its own: it opens on Docs.
export const Route = createFileRoute('/library/')({
  beforeLoad: () => {
    throw redirect({ to: '/library/docs', replace: true });
  },
  component: () => null,
});
