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
export function loadUsage(qc: QueryClient, { view, range = '7d', by }: UsageSearch) {
  // Start the chart with the figures so it can load while the route is opening.
  if (view !== 'limits') void qc.prefetchQuery(usageQuery(range, 'day'));
  return (view === 'limits'
    ? qc.ensureQueryData({ ...limitsQuery, revalidateIfStale: true })
    : qc.ensureQueryData({ ...usageQuery(range, by === 'model' ? 'model' : 'bot'), revalidateIfStale: true })
  ).then(
    () => undefined,
    () => undefined,
  );
}
