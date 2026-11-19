import { createFileRoute } from '@tanstack/react-router';
import { RoutinesPage } from '@/features/routines/exports';
import { loadRoutines, validateRoutinesSearch } from '@/features/routines/routing';

/** Routines: `?tab=webhooks`; one routine, a built-in one or a webhook opens in `?panel=`. */
export const Route = createFileRoute('/company/routines')({
  validateSearch: validateRoutinesSearch,
  loaderDeps: ({ search: { tab, status, range } }) => ({ tab, status, range }),
  loader: ({ context, deps }) => loadRoutines(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <RoutinesPage search={Route.useSearch()} />;
}
