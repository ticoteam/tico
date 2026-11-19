import { createFileRoute } from '@tanstack/react-router';
import { DocPage } from '@/features/library/exports';
import { docSearch, loadDoc } from '@/features/library/routing';

// One doc by id; `sources` is External sources and `ext:<id>` a doc read from one of them.
export const Route = createFileRoute('/library/$doc')({
  validateSearch: docSearch,
  loader: ({ params: { doc }, context: { queryClient } }) => loadDoc(queryClient, doc),
  component: Doc,
});

function Doc() {
  const { doc } = Route.useParams();
  return <DocPage key={doc} doc={doc} search={Route.useSearch()} />;
}
