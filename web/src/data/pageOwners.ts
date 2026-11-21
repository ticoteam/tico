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

/** Whether the reader may change a page's owner: the owner or an admin, as the server's PUT checks. */
export const pageOwnersMayQuery = queryOptions({
  queryKey: ['settings', 'page-owners', 'may'],
  queryFn: async ({ signal }) => (await api.get<{ can_change?: boolean }>('/api/v2/page-owners', { signal })).can_change === true,
  staleTime: QUIET_STALE_MS,
});

/** Each agent with a question or an ask waiting on the reader, and how many (Needs you's `bots`, its whole list, read
    in one small page): the Agent bar opens for its owner's. A `needs` event reads it again (live/patch.ts). */
export const waitingAgentsQuery = queryOptions({
  queryKey: ['needs', 'agents'],
  queryFn: async ({ signal }) => (await api.get<{ bots?: Record<string, number> }>('/api/v2/needs-you?limit=1', { signal })).bots ?? {},
  staleTime: QUIET_STALE_MS,
});
