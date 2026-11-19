import { createFileRoute } from '@tanstack/react-router';
import { LearnedPage } from '@/features/threads/exports';
import { loadLearned } from '@/features/threads/routing';

/* What Tico has learned: the page Tico opens. */
export const Route = createFileRoute('/learned')({
  loader: ({ context: { queryClient } }) => loadLearned(queryClient),
  component: LearnedPage,
});
