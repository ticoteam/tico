import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage, catchUpQuery, threadListQuery } from '@/features/threads/exports';

export const Route = createFileRoute('/threads/')({
  // A read that fails shows the page's own fault state, so the loader never throws.
  loader: ({ context: { queryClient } }) =>
    Promise.all([queryClient.ensureInfiniteQueryData(threadListQuery), queryClient.ensureQueryData(catchUpQuery)]).catch(() => undefined),
  component: () => <ThreadsPage />,
});
