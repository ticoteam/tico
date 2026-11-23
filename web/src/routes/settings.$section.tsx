import { createFileRoute, redirect } from '@tanstack/react-router';
import { SettingsPage } from '@/features/settings/exports';
import { isSection, loadSection, subscriptionsQuery } from '@/features/settings/routing';

// Older migrated subscriptions used UUIDs without hyphens.
const SUBSCRIPTION_ID = /^(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i;

/* `/settings/<section>`: one section of Settings. `?open=<id>` is the row opened in it (a person, a computer, a
   subscription); `?who=people|agents` filters History. An unknown section goes to the list. */
export const Route = createFileRoute('/settings/$section')({
  validateSearch: (raw: Record<string, unknown>): { open?: string; who?: 'people' | 'agents' } => ({
    ...(typeof raw.open === 'string' && raw.open ? { open: raw.open } : {}),
    ...(raw.who === 'people' || raw.who === 'agents' ? { who: raw.who } : {}),
  }),
  beforeLoad: async ({ params, search, context: { queryClient } }) => {
    const { open } = search;
    const subscription = params.section === 'agents' && open && (
      open.startsWith('subscription:') ||
      (SUBSCRIPTION_ID.test(open) && await queryClient.ensureQueryData(subscriptionsQuery)
        .then((data) => data.subscriptions.some((s) => s.id === open), () => false))
    );
    if (params.section === 'models' || subscription) {
      throw redirect({ to: '/settings/$section', params: { section: 'providers' }, search, replace: true });
    }
    if (!isSection(params.section)) throw redirect({ to: '/settings' });
  },
  loader: ({ params, context: { queryClient } }) => (isSection(params.section) ? loadSection(queryClient, params.section) : undefined),
  component: SettingsSectionRoute,
});

function SettingsSectionRoute() {
  const { section } = Route.useParams();
  const { open, who } = Route.useSearch();
  return isSection(section) ? <SettingsPage section={section} open={open} who={who} /> : null;
}
