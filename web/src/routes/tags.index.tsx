import { createFileRoute } from '@tanstack/react-router';
import { TagsPage } from '@/features/tasks/exports';
import { tagsQuery } from '@/features/tasks/routing';

/** Tags: every tag and when it was last used. */
export const Route = createFileRoute('/tags/')({
  // A failed read is the page's own fault state (Try again), not the router's error screen.
  loader: async ({ context }) => {
    await context.queryClient.ensureQueryData(tagsQuery).catch(() => undefined);
  },
  component: TagsPage,
});
