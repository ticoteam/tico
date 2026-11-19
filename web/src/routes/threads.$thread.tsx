import { createFileRoute } from '@tanstack/react-router';
import {
  MailPage,
  MessagingPage,
  ThreadsPage,
  catchUpQuery,
  mailboxesQuery,
  textListQuery,
  threadListQuery,
  threadQuery,
} from '@/features/threads/exports';

interface ThreadSearch {
  /** Mail: the mailbox read. */
  box?: string;
  /** Mail and Messaging: the open mail thread or text. */
  open?: string;
}

const text = (v: unknown) => (typeof v === 'string' && v ? v : undefined);

/* `/threads/<id>` is a thread; `new`, `catch-up`, `mail` and `messaging` are the pages that share its list column. */
export const Route = createFileRoute('/threads/$thread')({
  validateSearch: (s: Record<string, unknown>): ThreadSearch => ({ box: text(s.box), open: text(s.open) }),
  loader: ({ params, context: { queryClient } }) => {
    const id = params.thread;
    const reads: Promise<unknown>[] =
      id === 'mail'
        ? [queryClient.ensureQueryData(mailboxesQuery)]
        : id === 'messaging'
          ? [queryClient.ensureInfiniteQueryData(textListQuery)]
          : [
              queryClient.ensureInfiniteQueryData(threadListQuery),
              id === 'new' || id === 'catch-up' ? queryClient.ensureQueryData(catchUpQuery) : queryClient.ensureQueryData(threadQuery(id)),
            ];
    // A read that fails shows the page's own fault state, so the loader never throws.
    return Promise.all(reads).catch(() => undefined);
  },
  component: ThreadRoute,
});

function ThreadRoute() {
  const { thread } = Route.useParams();
  const { box, open } = Route.useSearch();
  if (thread === 'mail') return <MailPage box={box} open={open} />;
  if (thread === 'messaging') return <MessagingPage open={open} />;
  return <ThreadsPage open={thread} />;
}
