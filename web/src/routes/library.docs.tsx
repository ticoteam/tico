import { createFileRoute } from '@tanstack/react-router';
import { DocsPage, docsSearch, loadDocs } from '@/features/library/exports';

export const Route = createFileRoute('/library/docs')({
  validateSearch: docsSearch,
  loader: ({ context: { queryClient } }) => loadDocs(queryClient),
  component: Docs,
});

function Docs() {
  return <DocsPage search={Route.useSearch()} />;
}
