import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { paidByQuery } from './queries';

it('refreshes a payer wait once at its advertised reset', async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  const options = paidByQuery('ops', true);
  const until = new Date(Date.now() + 5_000).toISOString();
  const wait = { subscriptions: {}, last_turn: { payers: [], at: null, waits: [{ reason: 'At limit', until, fixer: 'Owner' }] } };
  client.setQueryData(options.queryKey, wait);
  const read = vi.fn(async () => wait);
  const observer = new QueryObserver(client, { ...options, queryFn: read, refetchOnMount: false });
  const stop = observer.subscribe(() => {});
  try {
    await vi.advanceTimersByTimeAsync(4_999);
    expect(read).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(read).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000);
    expect(read).toHaveBeenCalledTimes(1);
  } finally {
    stop();
    client.clear();
    vi.useRealTimers();
  }
});
