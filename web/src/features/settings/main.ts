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

/** For the route's loader: the section's read before the first paint. Never throws: a 403 or a fault is the page's to say. */
export const loadSection = (qc: QueryClient, section: SectionId) =>
  qc.prefetchQuery(mainQuery(section) as FetchQueryOptions<unknown, Error, unknown, readonly unknown[]>);

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
