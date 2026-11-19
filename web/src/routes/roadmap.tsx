import { createFileRoute } from '@tanstack/react-router';
import { RoadmapPage } from '@/features/tasks/exports';
import { filtersOf, pipelinesQuery, pipelineTasksQuery, roadmapPipeline, shelfQuery, validateRoadmapSearch } from '@/features/tasks/routing';

/** Roadmap: the Features pipeline as Now, Next, Later and Shipped. Its filters and `?requesters=<task>` live in the address. */
export const Route = createFileRoute('/roadmap')({
  validateSearch: validateRoadmapSearch,
  loaderDeps: ({ search }) => filtersOf(search),
  // A failed read is the page's own fault state (Try again), not the router's error screen.
  loader: async ({ context, deps }) => {
    const p = roadmapPipeline(await context.queryClient.ensureQueryData(pipelinesQuery).catch(() => []));
    if (!p) return;
    await Promise.all([
      context.queryClient.ensureQueryData(pipelineTasksQuery(p.id, deps)),
      context.queryClient.ensureQueryData(shelfQuery(p.id, 'done', deps)),
    ]).catch(() => undefined);
  },
  component: Page,
});

function Page() {
  return <RoadmapPage search={Route.useSearch()} />;
}
