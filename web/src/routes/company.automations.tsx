import { createFileRoute, Outlet, redirect, useParams } from '@tanstack/react-router';
import { AutomationsShell } from '@/features/routines/exports';
import { loadRoutines, validateRoutinesSearch } from '@/features/routines/routing';

/** Automations: the sidebar of automations, and beside it the one open (`/company/automations/<id>`) or All, `?tab=runs`,
    `?tab=webhooks`. Tico's own jobs, a webhook and a new routine open in `?panel=`; an older `?panel=routine:<id>` link
    opens the automation. The sidebar stays while the content beside it changes. */
export const Route = createFileRoute('/company/automations')({
  validateSearch: validateRoutinesSearch,
  beforeLoad: ({ search }) => {
    const p = search.panel ?? '';
    if (p.startsWith('routine:') && p !== 'routine:new') throw redirect({ to: '/company/automations/$id', params: { id: p.slice(8) }, replace: true });
  },
  loaderDeps: ({ search: { tab, status, range } }) => ({ tab, status, range }),
  loader: ({ context, deps }) => loadRoutines(context.queryClient, deps),
  component: Layout,
});

function Layout() {
  const id = useParams({ strict: false, select: (p) => (p as { id?: string }).id });
  return (
    <AutomationsShell search={Route.useSearch()} id={id}>
      <Outlet />
    </AutomationsShell>
  );
}
