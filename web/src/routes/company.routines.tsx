import { createFileRoute } from '@tanstack/react-router';
import { RoutinesPage, loadRoutines, validateRoutinesSearch } from '@/features/routines/exports';

/** Routines: `?tab=webhooks`; one routine, a built-in one or a webhook opens in `?panel=`. */
export const Route = createFileRoute('/company/routines')({
  validateSearch: validateRoutinesSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) => loadRoutines(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <RoutinesPage search={Route.useSearch()} />;
}
