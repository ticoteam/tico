import { createFileRoute } from '@tanstack/react-router';
import { SettingsPage } from '@/features/settings/exports';
import { loadSection } from '@/features/settings/routing';

/* `/settings`: a phone shows the section list; a wide screen lands on the first section, General. */
export const Route = createFileRoute('/settings/')({
  loader: ({ context: { queryClient } }) => loadSection(queryClient, 'general'),
  component: () => <SettingsPage section="general" list />,
});
