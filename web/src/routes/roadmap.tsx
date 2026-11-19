import { createFileRoute } from '@tanstack/react-router';
import { RoadmapPage } from '@/features/tasks/exports';
import { filtersOf, pipelinesQuery, pipelineTasksQuery, roadmapPipeline, shelfQuery, validateRoadmapSearch } from '@/features/tasks/routing';

/** Roadmap: the Features pipeline as Now, Next, Later and Shipped. Its filters and `?requesters=<task>` live in the address. */
export const Route = createFileRoute('/roadmap')({
  validateSearch: validateRoadmapSearch,
  loaderDeps: ({ search }) => filtersOf(search),
  loader: async ({ context: { queryClient: qc }, deps }) => {
    const p = roadmapPipeline(await qc.ensureQueryData(pipelinesQuery));
    if (p) await Promise.all([qc.ensureQueryData(pipelineTasksQuery(p.id, deps)), qc.ensureQueryData(shelfQuery(p.id, 'done', deps))]);
  },
  component: Page,
});

function Page() {
  return <RoadmapPage search={Route.useSearch()} />;
}
