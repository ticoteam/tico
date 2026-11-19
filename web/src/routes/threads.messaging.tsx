import { createFileRoute } from '@tanstack/react-router';
import { MessagingPage } from '@/features/threads/exports';
import { textListQuery } from '@/features/threads/routing';

interface MessagingSearch {
  /** The open contact: who the texts came from. */
  open?: string;
}

export const Route = createFileRoute('/threads/messaging')({
  validateSearch: (s: Record<string, unknown>): MessagingSearch => ({ open: typeof s.open === 'string' && s.open ? s.open : undefined }),
  // A read that fails shows the page's own fault state, so the loader never throws.
  loader: ({ context: { queryClient } }) => queryClient.ensureInfiniteQueryData(textListQuery).catch(() => undefined),
  component: MessagingRoute,
});

function MessagingRoute() {
  const { open } = Route.useSearch();
  return <MessagingPage open={open} />;
}
