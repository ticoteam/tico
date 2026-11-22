import { createFileRoute } from '@tanstack/react-router';
import { FlowPage, FlowPending } from '@/features/tasks/exports';
import { flowQuery } from '@/features/tasks/routing';

export const Route = createFileRoute('/flow/')({
  validateSearch: (s: Record<string, unknown>): { view?: 'all' } => (s.view === 'all' ? { view: 'all' } : {}),
  loaderDeps: ({ search }) => ({ all: search.view === 'all' }),
  loader: ({ context: { queryClient }, deps }) => deps.all ? undefined : queryClient.ensureQueryData(flowQuery),
  pendingComponent: FlowPending,
  component: FlowPage,
});
