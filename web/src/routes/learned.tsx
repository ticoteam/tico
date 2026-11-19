import { createFileRoute } from '@tanstack/react-router';
import { LearnedPage, loadLearned } from '@/features/threads/exports';

/* What Tico has learned: the page Tico opens. */
export const Route = createFileRoute('/learned')({
  loader: ({ context: { queryClient } }) => loadLearned(queryClient),
  component: LearnedPage,
});
