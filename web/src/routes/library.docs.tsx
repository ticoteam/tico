import { createFileRoute } from '@tanstack/react-router';
import { DocsPage } from '@/features/library/exports';
import { docsSearch, loadDocs } from '@/features/library/routing';

export const Route = createFileRoute('/library/docs')({
  validateSearch: docsSearch,
  loader: ({ context: { queryClient } }) => loadDocs(queryClient),
  component: Docs,
});

function Docs() {
  return <DocsPage search={Route.useSearch()} />;
}
