import type { QueryClient } from '@tanstack/react-query';
import { catchUpQuery, learnedQuery, viewersQuery, threadListQuery, threadQuery, threadSearchQuery, updateListQuery, updateQuery, type UpdateTab } from './queries';

/* What the routes read before they draw, and their search params. A read that fails shows the page's own fault
   state, so a loader never throws. */

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
  const reads: Promise<unknown>[] = [qc.ensureInfiniteQueryData(threadListQuery)];
  if (o.q) reads.push(qc.ensureInfiniteQueryData(threadSearchQuery(o.q.trim())));
  if (o.catchUp) reads.push(qc.ensureQueryData(catchUpQuery));
  if (o.thread) reads.push(qc.ensureQueryData(threadQuery(o.thread)));
  return Promise.all(reads).catch(() => undefined);
}

export interface UpdatesSearch {
  /** An update open as its own page. */
  open?: string;
}
export const updatesSearch = (s: Record<string, unknown>): UpdatesSearch => ({ open: text(s.open) });

export const loadUpdates = (qc: QueryClient, tab: UpdateTab, open?: string) =>
  (open ? qc.ensureQueryData(updateQuery(open)) : qc.ensureInfiniteQueryData(updateListQuery(tab))).catch(() => undefined);

export const loadLearned = (qc: QueryClient) =>
  Promise.all([qc.ensureQueryData(learnedQuery), qc.ensureQueryData(viewersQuery)]).catch(() => undefined);
