import { createFileRoute, redirect } from '@tanstack/react-router';
import { validateRoutinesSearch } from '@/features/routines/routing';

/** Routines is Automations again: the old address and its search go there. */
export const Route = createFileRoute('/company/routines')({
  validateSearch: validateRoutinesSearch,
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/company/automations', search, replace: true });
  },
});
