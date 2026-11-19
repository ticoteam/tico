import { createFileRoute, redirect } from '@tanstack/react-router';
import { Empty } from '@/kit/Empty';
import { firstPipeline, pipelinesQuery } from '@/features/tasks/routing';

/** Tasks lands on the pipeline opened last, else the first; replace, so Back skips this hop. */
export const Route = createFileRoute('/tasks/')({
  loader: async ({ context }) => {
    const pipeline = firstPipeline(await context.queryClient.ensureQueryData(pipelinesQuery));
    if (pipeline) throw redirect({ to: '/tasks/$pipeline', params: { pipeline }, replace: true });
  },
  component: () => <Empty title="No pipelines yet" />,
});
