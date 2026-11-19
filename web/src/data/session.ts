import { queryOptions } from '@tanstack/react-query';
import { adaptMe } from '@/api/adapt';
import { api } from '@/api/client';
import type { GetMeResponse } from '@/api/schema';
import { keys } from '@/data/keys';

/** The signed-in person (GET /api/v2/me). */
export const meQuery = queryOptions({
  queryKey: keys.me(),
  queryFn: async ({ signal }) => adaptMe(await api.get<GetMeResponse>('/api/v2/me', { signal })),
});
