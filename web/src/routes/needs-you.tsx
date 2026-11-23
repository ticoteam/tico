import { createFileRoute } from '@tanstack/react-router';
import { NeedsYouPage, NeedsYouPending } from '@/features/tasks/exports';
import { flowQuery } from '@/features/tasks/routing';

export const Route = createFileRoute('/needs-you')({
  validateSearch: (s: Record<string, unknown>): { view?: 'all' } => (s.view === 'all' ? { view: 'all' } : {}),
  loaderDeps: ({ search }) => ({ all: search.view === 'all' }),
  loader: ({ context: { queryClient }, deps }) => deps.all ? undefined : queryClient.ensureQueryData(flowQuery),
  pendingComponent: NeedsYouPending,
  component: NeedsYouPage,
});
