import { queryOptions, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { api } from '@/api/client';
import type { GetConfigResponse } from '@/api/schema';
import { quietRoot } from './optimistic';

export interface ReleaseOffer {
  current: string;
  latest: string;
  checking?: boolean;
  available: boolean;
  managed: boolean;
  scheduled?: string;
}

type Config = GetConfigResponse & { update?: ReleaseOffer };

quietRoot('config');

const CONFIG = '/api/v2/config';

/** The company's settings and release offer (GET /api/v2/config). */
export const configQuery = queryOptions({
  queryKey: ['config'] as const,
  queryFn: ({ signal }) => api.get<Config>(CONFIG, { signal }),
  staleTime: Infinity,
});

export async function readConfigOnce(client: QueryClient, signal: AbortSignal) {
  const key = configQuery.queryKey;
  const before = client.getQueryState(key)?.dataUpdateCount;
  const config = await api.get<Config>(CONFIG, { signal, once: true });
  // A concurrent config refresh may already have supplied a newer answer.
  if (!signal.aborted && client.getQueryState(key)?.dataUpdateCount === before) client.setQueryData(key, config);
  return client.getQueryData(key);
}

export function useInitialReleaseRefresh(enabled: boolean) {
  const client = useQueryClient();
  const { data: ready } = useQuery({ ...configQuery, enabled, select: () => true });
  useEffect(() => {
    if (!enabled || !ready) return;
    const waiting = () => {
      const update = client.getQueryData(configQuery.queryKey)?.update;
      return update && (!update.latest || update.checking);
    };
    if (!waiting()) return;
    const controller = new AbortController();
    const refresh = () => {
      if (waiting()) void readConfigOnce(client, controller.signal).catch(() => {});
    };
    const timers = [30_000, 120_000].map((delay) => setTimeout(refresh, delay));
    return () => {
      for (const timer of timers) clearTimeout(timer);
      controller.abort();
    };
  }, [client, enabled, ready]);
}
