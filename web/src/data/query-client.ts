import { QueryClient } from '@tanstack/react-query';

// staleTime Infinity on purpose: the live stream keeps the cache current, so nothing refetches on a timer or on focus.
// Offline, writes pause (networkMode 'online') and resume on reconnect.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: Infinity, gcTime: 10 * 60_000, refetchOnWindowFocus: false, retry: 1, networkMode: 'online' },
    mutations: { networkMode: 'online' },
  },
});
