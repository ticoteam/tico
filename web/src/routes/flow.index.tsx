import { createFileRoute, redirect } from '@tanstack/react-router';

/* An old Flow link: Needs you, with its view. */
export const Route = createFileRoute('/flow/')({
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/needs-you', search: search as never, replace: true });
  },
  component: () => null,
});
