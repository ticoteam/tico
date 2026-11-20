import { createFileRoute } from '@tanstack/react-router';
import { AutomationsPage } from '@/features/routines/exports';

/** Beside the sidebar when no automation is open: All, Runs or Webhooks (`?tab=`). */
export const Route = createFileRoute('/company/automations/')({
  component: Page,
});

function Page() {
  return <AutomationsPage search={Route.useSearch()} />;
}
