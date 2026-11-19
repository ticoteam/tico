import { createFileRoute } from '@tanstack/react-router';
import { PipelinePage } from '@/features/tasks/exports';
import { pipelinesQuery } from '@/features/tasks/routing';

/** Edit pipeline, a page; `?new=1` is New pipeline, and the pipeline in the address is where Back goes. */
export const Route = createFileRoute('/tasks/$pipeline/edit')({
  validateSearch: (raw: Record<string, unknown>): { new?: true } =>
    raw.new === true || raw.new === 'true' || raw.new === 1 || raw.new === '1' ? { new: true } : {},
  loader: ({ context }) => context.queryClient.ensureQueryData(pipelinesQuery),
  component: Page,
});

function Page() {
  const { pipeline } = Route.useParams();
  return <PipelinePage pipeline={pipeline} fresh={!!Route.useSearch().new} />;
}
