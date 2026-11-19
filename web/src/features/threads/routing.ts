import { isCancelledError, type QueryClient } from '@tanstack/react-query';
import { learnedQuery, viewersQuery, threadListQuery, threadQuery, updateListQuery, updateQuery, type UpdateTab } from './queries';

/* What the routes read before they draw, and their search params. The page's main read throws to the router's fault
   page; a side read (the open thread) is waited for but never throws: its own part of the page says it did not load. */

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

export { asksOf, type Asks } from './model';

/** A thread's page: the list (the menu's Threads tab) and the open thread. The menu reads the list too: its observer
    can detach during mount while the route still waits on the read, which cancels it; the route reads again. */
export function loadThreads(qc: QueryClient, o: { thread?: string } = {}) {
  const list = () => qc.ensureInfiniteQueryData(threadListQuery);
  return Promise.all([
    list().catch((error: unknown) => (isCancelledError(error) ? list() : Promise.reject(error))),
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
