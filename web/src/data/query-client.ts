import { QueryClient } from '@tanstack/react-query';
import { ApiError } from '@/api/client';

// staleTime Infinity on purpose: the live stream keeps the cache current, so nothing refetches on a timer or on focus.
// gcTime an hour: a page seen before draws from the cache with no spinner, not only for the ten minutes after.
// One retry, and only when trying again could help: a server error, never a refusal (4xx). A restart's 503 and a drop
// are not tried again here: the client already sent them again for a minute (api/client.ts).
// Offline, writes pause (networkMode 'online') and resume on reconnect. Reads try once anyway ('offlineFirst'): a page
// whose loader waits on its read then shows its fault with Try again, not loading bars until the network returns.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: Infinity, gcTime: 60 * 60_000, refetchOnWindowFocus: false, retry: (n, e) => n < 1 && (!(e instanceof ApiError) || ((e.retryable || e.status >= 500) && e.status !== 503 && e.status !== 0)), networkMode: 'offlineFirst' },
    mutations: { networkMode: 'online' },
  },
});
