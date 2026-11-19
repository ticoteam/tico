import { createFileRoute, redirect } from '@tanstack/react-router';
import { SettingsPage } from '@/features/settings/exports';
import { isSection, loadSection } from '@/features/settings/routing';

/* `/settings/<section>`: one section of Settings. `?open=<id>` is the row opened in it (a person, a computer, a
   subscription); `?who=people|agents` filters History. An unknown section goes to the list. */
export const Route = createFileRoute('/settings/$section')({
  validateSearch: (raw: Record<string, unknown>): { open?: string; who?: 'people' | 'agents' } => ({
    ...(typeof raw.open === 'string' && raw.open ? { open: raw.open } : {}),
    ...(raw.who === 'people' || raw.who === 'agents' ? { who: raw.who } : {}),
  }),
  beforeLoad: ({ params }) => {
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
