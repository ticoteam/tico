import type { QueryClient } from '@tanstack/react-query';
import { limitsQuery, usageQuery, type UsageRange } from './queries';

// What the routes and the shell use before a page's code loads: the sections, the first reads and Tico support's
// access line. No components here: the first load imports this file, and a page comes through exports.ts.
export { SECTION_IDS, isSection, type SectionId } from './sections';
export { loadSection } from './main';
export { supportAccessQuery, type SupportAccessView } from './queries';

export interface UsageSearch {
  view?: 'limits';
  range?: UsageRange;
  by?: 'model';
  open?: string;
}

/** For the route's loader: the view's first read. Never throws. Read before, it draws from the cache at once; gone
    stale, it is read again behind the page. */
export const loadUsage = (qc: QueryClient, search: UsageSearch) =>
  (search.view === 'limits'
    ? qc.ensureQueryData({ ...limitsQuery, revalidateIfStale: true })
    : qc.ensureQueryData({ ...usageQuery(search.range ?? '7d', 'bot'), revalidateIfStale: true })
  ).then(
    () => undefined,
    () => undefined,
  );
