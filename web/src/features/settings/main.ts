import type { FetchQueryOptions, QueryClient } from '@tanstack/react-query';
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

/** For the route's loader: the section's read before the first paint. Never throws: a 403 or a fault is the page's to say.
    A section read before draws from the cache at once; one gone stale (the quiet reads) is read again behind it. */
export const loadSection = (qc: QueryClient, section: SectionId) =>
  qc
    .ensureQueryData({ ...(mainQuery(section) as FetchQueryOptions<unknown, Error, unknown, readonly unknown[]>), revalidateIfStale: true })
    .then(
      () => undefined,
      () => undefined,
    );

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
