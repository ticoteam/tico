import { createFileRoute, redirect } from '@tanstack/react-router';

/** A short address for Automations. */
export const Route = createFileRoute('/routines')({
  beforeLoad: () => {
    throw redirect({ to: '/company/automations', replace: true });
  },
});
