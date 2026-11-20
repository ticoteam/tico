import { isCancelledError, type FetchQueryOptions, type QueryClient } from '@tanstack/react-query';
import { configQuery, meQuery } from '@/data';
import {
  accessQuery,
  billingQuery,
  changelogQuery,
  computersQuery,
  contactTypesQuery,
  healthQuery,
  historyQuery,
  modelsQuery,
  roadmapQuery,
  screeningQuery,
  subscriptionsQuery,
} from './queries';
import type { SectionId } from './sections';

/** The server said this read is for owners and admins. */
export const isForbidden = (e: unknown) => (e as { status?: number } | null)?.status === 403;

/** For the route's loader: the person and the section's read before the first paint. A fault is the router's to show;
    a 403 is not thrown, since the section says it beside the section list. A section read before draws from the cache
    at once; one gone stale (the quiet reads) is read again behind it. */
export const loadSection = (qc: QueryClient, section: SectionId) => {
  const read = () => Promise.all([
    qc.ensureQueryData(meQuery),
    qc.ensureQueryData({ ...(mainQuery(section) as FetchQueryOptions<unknown, Error, unknown, readonly unknown[]>), revalidateIfStale: true }),
  ]);
  return read()
    .catch((error: unknown) => {
      // A shell observer can detach during mount while the route is waiting for the same read.
      if (isCancelledError(error)) return read();
      throw error;
    })
    .then(
      () => undefined,
      (e: unknown) => {
        if (!isForbidden(e)) throw e;
      },
    );
};

/** The one read a section cannot draw without: the route loads it first, and its state is the page's state. */
export function mainQuery(section: SectionId) {
  switch (section) {
    case 'general':
      return configQuery;
    case 'people':
      return accessQuery;
    case 'agents':
      return subscriptionsQuery;
    case 'computers':
      return computersQuery;
    case 'models':
      return modelsQuery;
    case 'tasks':
      return roadmapQuery;
    case 'contacts':
      return contactTypesQuery;
    case 'security':
      return screeningQuery;
    case 'billing':
      return billingQuery;
    case 'history':
      return historyQuery;
    case 'health':
      return healthQuery;
    case 'changelog':
      return changelogQuery;
    case 'account':
      return meQuery;
  }
}
