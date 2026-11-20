import { createFileRoute } from '@tanstack/react-router';
import { FlowPage, FlowPending } from '@/features/tasks/exports';
import { flowQuery, pipelinesQuery } from '@/features/tasks/routing';

/** Flow: what waits on the person, across every pipeline. */
export const Route = createFileRoute('/flow/')({
  loader: ({ context: { queryClient: qc } }) => Promise.all([qc.ensureQueryData(flowQuery), qc.ensureQueryData(pipelinesQuery)]),
  pendingComponent: FlowPending,
  component: FlowPage,
});
