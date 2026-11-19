import { createFileRoute } from '@tanstack/react-router';
import { TagPage } from '@/features/tasks/exports';
import { pipelinesQuery, tagQuery } from '@/features/tasks/routing';

/** One tag: its tasks, by pipeline. */
export const Route = createFileRoute('/tags/$tag')({
  // A failed read is the page's own fault state (Try again), not the router's error screen.
  loader: async ({ context, params }) => {
    await Promise.all([context.queryClient.ensureQueryData(tagQuery(params.tag)), context.queryClient.ensureQueryData(pipelinesQuery)]).catch(
      () => undefined,
    );
  },
  component: Page,
});

function Page() {
  const { tag } = Route.useParams();
  return <TagPage tag={tag} />;
}
