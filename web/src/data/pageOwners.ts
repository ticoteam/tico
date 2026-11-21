import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import { QUIET_STALE_MS } from './optimistic';

export interface PageOwnerRow {
  name?: string;
  /** 'default', 'none' or the chosen agent ('bot:<id>'). */
  choice?: string;
  /** The owner now ('bot:<id>'), or null when a person chose "No owner". */
  bot?: string | null;
  /** Who Default gives. */
  default?: string | null;
}

/** Each company page's owner (backend/page_owners.py): the agent in the shell's Agent bar. Settings reads it too. */
export const pageOwnersQuery = queryOptions({
  queryKey: ['settings', 'page-owners'],
  queryFn: async ({ signal }) => (await api.get<{ pages?: Record<string, PageOwnerRow> }>('/api/v2/page-owners', { signal })).pages ?? {},
  staleTime: QUIET_STALE_MS,
});
