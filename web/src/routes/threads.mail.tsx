import { createFileRoute } from '@tanstack/react-router';
import { MailPage, mailboxesQuery } from '@/features/threads/exports';

interface MailSearch {
  /** The mailbox read. */
  box?: string;
  /** The open mail thread. */
  open?: string;
  /** The mailbox agent's instructions, open in place of a mail. */
  view?: 'instructions';
}

const text = (v: unknown) => (typeof v === 'string' && v ? v : undefined);

export const Route = createFileRoute('/threads/mail')({
  validateSearch: (s: Record<string, unknown>): MailSearch => ({
    box: text(s.box),
    open: text(s.open),
    view: s.view === 'instructions' ? 'instructions' : undefined,
  }),
  // A read that fails shows the page's own fault state, so the loader never throws.
  loader: ({ context: { queryClient } }) => queryClient.ensureQueryData(mailboxesQuery).catch(() => undefined),
  component: MailRoute,
});

function MailRoute() {
  const { box, open, view } = Route.useSearch();
  return <MailPage box={box} open={open} view={view} />;
}
