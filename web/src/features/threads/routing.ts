import type { QueryClient } from '@tanstack/react-query';
import { timelineQuery } from '@/conversation';
import { catchUpQuery, learnedQuery, viewersQuery, threadListQuery, threadQuery, threadSearchQuery, updateListQuery, updateQuery, type UpdateTab } from './queries';

/* What the routes read before they draw, and their search params. The standalone list throws to the router's fault
   page. A side read leaves its error for the part of the page that draws it. */

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
export const threadsSearch = (s: Record<string, unknown>): ThreadsSearch => ({ q: text(s.q)?.trim() });

export function loadThreads(qc: QueryClient, o: { q?: string; catchUp?: boolean; thread?: string; preload?: boolean }) {
  // Only open conversations follow messages. Read the timeline on navigation so a hover cannot cache an old page.
  if (o.thread && !o.preload) void qc.ensureInfiniteQueryData(timelineQuery(o.thread)).catch(() => undefined);
  // A thread can open while the sidebar list is still loading (on a phone the list is hidden).
  const list = qc.ensureInfiniteQueryData(threadListQuery);
  if (o.thread) void list.catch(() => undefined);
  return Promise.all([
    o.thread ? undefined : list,
    o.q && qc.prefetchInfiniteQuery(threadSearchQuery(o.q)),
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
