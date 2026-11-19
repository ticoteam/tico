import { createFileRoute } from '@tanstack/react-router';
import { loadSection, SettingsPage } from '@/features/settings/exports';

/* `/settings`: a phone shows the section list; a wide screen lands on the first section, General. */
export const Route = createFileRoute('/settings/')({
  loader: ({ context: { queryClient } }) => loadSection(queryClient, 'general'),
  component: () => <SettingsPage section="general" list />,
});
