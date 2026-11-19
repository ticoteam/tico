import { createFileRoute } from '@tanstack/react-router';
import { flowQuery, FlowPage, pipelinesQuery } from '@/features/tasks/exports';

/** Flow: what waits on the person, across every pipeline. */
export const Route = createFileRoute('/flow/')({
  loader: async ({ context }) => {
    // A failed read is the page's own fault state (Try again), not the router's error screen.
    await Promise.all([context.queryClient.ensureQueryData(flowQuery), context.queryClient.ensureQueryData(pipelinesQuery)]).catch(() => undefined);
  },
  component: FlowPage,
});
