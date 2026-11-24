import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetConfigResponse } from '@/api/schema';
import { quietRoot } from './optimistic';

export interface ReleaseOffer {
  current: string;
  latest: string;
  available: boolean;
  held: string;
  managed: boolean;
  scheduled?: string;
}

type Config = GetConfigResponse & { update?: ReleaseOffer };

quietRoot('config');

/** The company's settings and release offer (GET /api/v2/config). */
export const configQuery = queryOptions({
  queryKey: ['config'] as const,
  queryFn: ({ signal }) => api.get<Config>('/api/v2/config', { signal }),
  staleTime: Infinity,
});
