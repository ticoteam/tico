import type { QueryClient } from '@tanstack/react-query';
import { catchUpQuery, learnedQuery, viewersQuery, threadListQuery, threadQuery, threadSearchQuery, updateListQuery, updateQuery, type UpdateTab } from './queries';

/* What the routes read before they draw, and their search params. The page's main read throws to the router's fault
   page; a side read (a search, Catch up, the open thread) is waited for but never throws: its own part of the page
   says it did not load. */

// The reads the routes and the shell's counts use. No components here: the first load imports this file (through
// the routes and the root), and a page comes through exports.ts with its route.
export {
  threadListQuery,
  threadQuery,
  catchUpQuery,
  chatQuery,
  mailboxesQuery,
  textListQuery,
  updateListQuery,
  updateQuery,
  type UpdateTab,
} from './queries';

const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v : undefined);

export interface ThreadsSearch {
  /** The list's search words. */
  q?: string;
}
export const threadsSearch = (s: Record<string, unknown>): ThreadsSearch => ({ q: text(s.q) });

export function loadThreads(qc: QueryClient, o: { q?: string; catchUp?: boolean; thread?: string }) {
  return Promise.all([
    qc.ensureInfiniteQueryData(threadListQuery),
    o.q && qc.prefetchInfiniteQuery(threadSearchQuery(o.q.trim())),
    o.catchUp && qc.prefetchQuery(catchUpQuery),
    o.thread && qc.prefetchQuery(threadQuery(o.thread)),
  ]);
}

export interface UpdatesSearch {
  /** An update open as its own page. */
  open?: string;
}
export const updatesSearch = (s: Record<string, unknown>): UpdatesSearch => ({ open: text(s.open) });

export const loadUpdates = (qc: QueryClient, tab: UpdateTab, open?: string) =>
  open ? qc.ensureQueryData(updateQuery(open)) : qc.ensureInfiniteQueryData(updateListQuery(tab));

export function loadLearned(qc: QueryClient) {
  // the readers are the page's last section: they come in after it draws
  void qc.prefetchQuery(viewersQuery);
  return qc.ensureQueryData(learnedQuery);
}
