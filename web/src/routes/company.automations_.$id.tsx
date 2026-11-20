import { createFileRoute, redirect } from '@tanstack/react-router';
import { AutomationPage } from '@/features/routines/exports';
import { loadAutomation, validateAutomationSearch } from '@/features/routines/routing';

/** One automation: its flow, `?view=runs` its runs, `?edit=` the part open in the panel. Tico's own jobs are panels on
    the list. */
export const Route = createFileRoute('/company/automations_/$id')({
  validateSearch: validateAutomationSearch,
  beforeLoad: ({ params }) => {
    const built: Record<string, string> = { maintenance: 'builtin:maintenance', heartbeat: 'builtin:heartbeat', 'system-jobs': 'builtin:system', system: 'builtin:system' };
    if (built[params.id]) throw redirect({ to: '/company/automations', search: { panel: built[params.id] }, replace: true });
    if (params.id === 'webhooks') throw redirect({ to: '/company/automations', search: { tab: 'webhooks' }, replace: true });
  },
  loader: ({ params, context }) => loadAutomation(context.queryClient, params.id),
  component: Page,
});

function Page() {
  const { id } = Route.useParams();
  return <AutomationPage id={id} search={Route.useSearch()} />;
}
