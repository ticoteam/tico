import { createFileRoute } from '@tanstack/react-router';
import { ThreadsIndex } from '@/features/threads/exports';
import { loadThreads } from '@/features/threads/routing';

/* No thread open: the list is the menu's Threads tab. */
export const Route = createFileRoute('/threads/')({
  loader: ({ context: { queryClient } }) => loadThreads(queryClient),
  component: ThreadsIndex,
});
