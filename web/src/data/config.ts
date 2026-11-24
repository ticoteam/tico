import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetConfigResponse } from '@/api/schema';

export interface ReleaseOffer {
  current: string;
  latest: string;
  available: boolean;
  held: string;
  managed: boolean;
}

type Config = GetConfigResponse & { update?: ReleaseOffer };

/** The company's settings and release offer (GET /api/v2/config). */
export const configQuery = queryOptions({
  queryKey: ['config'] as const,
  queryFn: ({ signal }) => api.get<Config>('/api/v2/config', { signal }),
  staleTime: Infinity,
});

/** The release checker fills its cache in the background, between reads of the config. */
export const releaseConfigQuery = { ...configQuery, staleTime: 60_000, refetchInterval: 60_000 };
