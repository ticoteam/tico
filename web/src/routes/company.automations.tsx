import { createFileRoute, redirect } from '@tanstack/react-router';
import { AutomationsPage } from '@/features/routines/exports';
import { loadRoutines, validateRoutinesSearch } from '@/features/routines/routing';

/** Automations: `?tab=runs` and `?tab=webhooks`; Tico's own jobs, a webhook and a new routine open in `?panel=`. An
    automation is its own page (`/company/automations/<id>`): an older `?panel=routine:<id>` link goes there. */
export const Route = createFileRoute('/company/automations')({
  validateSearch: validateRoutinesSearch,
  beforeLoad: ({ search }) => {
    const p = search.panel ?? '';
    if (p.startsWith('routine:') && p !== 'routine:new') throw redirect({ to: '/company/automations/$id', params: { id: p.slice(8) }, replace: true });
  },
  loaderDeps: ({ search: { tab, status, range } }) => ({ tab, status, range }),
  loader: ({ context, deps }) => loadRoutines(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <AutomationsPage search={Route.useSearch()} />;
}
