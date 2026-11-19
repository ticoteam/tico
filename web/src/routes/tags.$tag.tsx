import { createFileRoute } from '@tanstack/react-router';
import { TagPage } from '@/features/tasks/exports';
import { pipelinesQuery, tagQuery } from '@/features/tasks/routing';

/** One tag: its tasks, by pipeline. */
export const Route = createFileRoute('/tags/$tag')({
  loader: ({ context: { queryClient: qc }, params }) => Promise.all([qc.ensureQueryData(tagQuery(params.tag)), qc.ensureQueryData(pipelinesQuery)]),
  component: Page,
});

function Page() {
  const { tag } = Route.useParams();
  return <TagPage tag={tag} />;
}
