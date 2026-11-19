import { createFileRoute } from '@tanstack/react-router';
import { ThreadsPage } from '@/features/threads/exports';
import { loadThreads } from '@/features/threads/routing';

export const Route = createFileRoute('/threads/new')({
  loader: ({ context: { queryClient } }) => loadThreads(queryClient),
  component: () => <ThreadsPage pane="new" />,
});
