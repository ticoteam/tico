import { createFileRoute } from '@tanstack/react-router';
import { DocsPage } from '@/features/library/exports';
import { docsSearch, loadDocs } from '@/features/library/routing';

export const Route = createFileRoute('/library/docs')({
  validateSearch: docsSearch,
  loaderDeps: ({ search }) => ({ tab: search.tab, archived: search.archived }),
  loader: ({ deps, context: { queryClient } }) => loadDocs(queryClient, deps),
  component: Docs,
});

function Docs() {
  return <DocsPage search={Route.useSearch()} />;
}
