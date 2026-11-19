import { createFileRoute } from '@tanstack/react-router';
import { TagsPage } from '@/features/tasks/exports';
import { tagsQuery } from '@/features/tasks/routing';

/** Tags: every tag and when it was last used. */
export const Route = createFileRoute('/tags/')({
  loader: ({ context }) => context.queryClient.ensureQueryData(tagsQuery),
  component: TagsPage,
});
