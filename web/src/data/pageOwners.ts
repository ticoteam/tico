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
  /** The owner is the page's own (its choice or its default), not the General Manager standing in for none. */
  own?: boolean;
}

export interface PageOwners {
  pages: Record<string, PageOwnerRow>;
  /** The reader may change a page's owner: the owner or an admin, as the server's PUT checks. */
  canChange: boolean;
}

/** Each company page's owner (backend/page_owners.py): the agent in the shell's Agent bar. Settings and an agent's
    page read it too, and whether the reader may change one. */
export const pageOwnersQuery = queryOptions({
  queryKey: ['settings', 'page-owners'],
  queryFn: async ({ signal }): Promise<PageOwners> => {
    const r = await api.get<{ pages?: Record<string, PageOwnerRow>; can_change?: boolean }>('/api/v2/page-owners', { signal });
    return { pages: r.pages ?? {}, canChange: r.can_change === true };
  },
  staleTime: QUIET_STALE_MS,
});
