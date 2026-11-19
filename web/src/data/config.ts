import { queryOptions } from '@tanstack/react-query';
import { api } from '@/api/client';
import type { GetConfigResponse } from '@/api/schema';

/** The company's name and Tico's (GET /api/v2/config): the menu's head. Changes only with a server's settings. */
export const configQuery = queryOptions({
  queryKey: ['config'] as const,
  queryFn: ({ signal }) => api.get<GetConfigResponse>('/api/v2/config', { signal }),
  staleTime: Infinity,
});
