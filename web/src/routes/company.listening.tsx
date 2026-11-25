import { createFileRoute } from '@tanstack/react-router';
import { ListeningPage } from '@/features/routines/exports';
import { loadListening, validateListeningSearch } from '@/features/routines/routing';
import { PAGE_SEARCH } from '@/lib';

/** Listening: the watches; `?watch=<id>` is one watch, `&tab=sources` its sources, `&match=<id>` one match open. */
export const Route = createFileRoute('/company/listening')({
  validateSearch: validateListeningSearch,
  loaderDeps: ({ search: { watch, tab } }) => ({ watch, tab }),
  loader: ({ context, deps }) => loadListening(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <ListeningPage search={Route.useSearch(PAGE_SEARCH)} />;
}
