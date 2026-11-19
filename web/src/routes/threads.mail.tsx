import { createFileRoute } from '@tanstack/react-router';
import { MailPage, mailboxesQuery } from '@/features/threads/exports';

interface MailSearch {
  /** The mailbox read. */
  box?: string;
  /** The open mail thread. */
  open?: string;
}

const text = (v: unknown) => (typeof v === 'string' && v ? v : undefined);

export const Route = createFileRoute('/threads/mail')({
  validateSearch: (s: Record<string, unknown>): MailSearch => ({ box: text(s.box), open: text(s.open) }),
  // A read that fails shows the page's own fault state, so the loader never throws.
  loader: ({ context: { queryClient } }) => queryClient.ensureQueryData(mailboxesQuery).catch(() => undefined),
  component: MailRoute,
});

function MailRoute() {
  const { box, open } = Route.useSearch();
  return <MailPage box={box} open={open} />;
}
