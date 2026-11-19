import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';

// staleTime Infinity on purpose: the live stream keeps the cache current, so nothing refetches on a timer or on focus.
// gcTime an hour: a page seen before draws from the cache with no spinner, not only for the ten minutes after.
// One retry, and only when trying again could help: a network failure or a server error, never a refusal (4xx).
// Offline, writes pause (networkMode 'online') and resume on reconnect. Reads try once anyway ('offlineFirst'): a page
// whose loader waits on its read then shows its fault with Try again, not loading bars until the network returns.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: Infinity, gcTime: 60 * 60_000, refetchOnWindowFocus: false, retry: (n, e) => n < 1 && (!(e instanceof ApiError) || e.retryable || e.status >= 500), networkMode: 'offlineFirst' },
    mutations: { networkMode: 'online' },
  },
});
