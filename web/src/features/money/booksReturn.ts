import { useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { toast } from '@/kit/Toast';
import { BOOKS, rereadSources, type SourceGroup } from './queries';
import type { BooksReason, FinanceSearch } from './search';

/* Back from connecting QuickBooks or Xero: the server sends the browser to Finance with `?books=connected` or
   `?books=error&reason=<code>` (finance-admin.ts finBooksReturn in the old app). The page says how it went once, from
   the server's own read of the sources, and takes the words out of the address so a reload or Back does not repeat it.
   The address is never proof: "connected" shows only when the sources read holds a live books connection. */

const AGAIN = 'The sign-in did not hold. Connect again.';
const RETRY = 'The connection did not finish. Try again.';

/** Each reason in plain words, with the next step. */
export const BOOKS_WHY: Record<BooksReason, string> = {
  access_denied: 'Access was not allowed. Connect again and allow access.',
  already_connected: 'Finance already has books connected. Disconnect them first.',
  connected_elsewhere: 'These books are connected to another company. Disconnect them there first.',
  reconnect_required: AGAIN,
  not_connected: AGAIN,
  no_refresh_token: AGAIN,
  access_expired: AGAIN,
  vault_write_failed: 'The sign-in was not saved. Connect again.',
  gateway_refused: 'The books service did not answer. Try again in a moment.',
  bad_answer: RETRY,
  superseded: RETRY,
  unknown: RETRY,
};

/** The live QuickBooks or Xero connection's name, when the sources hold one. */
export function liveBooks(groups: readonly SourceGroup[] | undefined): string | null {
  for (const g of groups ?? [])
    for (const r of g.rows) {
      const provider = r.provider ?? '';
      if (r.kind === 'connection' && Object.hasOwn(BOOKS, provider) && (r.sync?.status === 'active' || r.sync?.status === 'new'))
        return BOOKS[provider as keyof typeof BOOKS];
    }
  return null;
}

/** Reads a books return once: a toast when the server shows the books connected, else the failure for the Sources
    panel. Returns that failure (or null) and a way to put it away. */
export function useBooksReturn(search: FinanceSearch): [string | null, () => void] {
  const router = useRouter();
  const [failure, setFailure] = useState<string | null>(null);
  const seen = useRef(false);
  const { books, reason, as } = search;
  useEffect(() => {
    if (books === undefined && reason === undefined) {
      seen.current = false;
      return;
    }
    if (seen.current) return; // StrictMode runs this twice before the cleaned address lands
    seen.current = true;
    const known = books === 'connected' || books === 'error';
    void router.navigate({
      to: '.',
      search: ((prev: Record<string, unknown>) => ({ ...prev, ...(known ? { tab: 'sources', panel: undefined } : {}), books: undefined, reason: undefined })) as never,
      replace: true,
    });
    if (!known) return;
    const said = books === 'error' ? BOOKS_WHY[reason ?? 'unknown'] : RETRY;
    void rereadSources(as).then(
      (data) => {
        const name = liveBooks(data?.groups);
        if (books === 'connected' && name) toast({ text: 'Connected', name });
        else setFailure(said);
      },
      () => setFailure(books === 'error' ? said : null),
    );
  }, [books, reason, as, router]);
  return [failure, () => setFailure(null)];
}
