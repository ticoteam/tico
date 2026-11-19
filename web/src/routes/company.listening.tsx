import { createFileRoute } from '@tanstack/react-router';
import { ListeningPage, loadListening, validateListeningSearch } from '@/features/routines/exports';

/** Listening: the watches; `?watch=<id>` is one watch, `&tab=sources` its sources, `&match=<id>` one match open. */
export const Route = createFileRoute('/company/listening')({
  validateSearch: validateListeningSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) => loadListening(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <ListeningPage search={Route.useSearch()} />;
}
