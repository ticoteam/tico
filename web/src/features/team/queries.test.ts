import { focusManager, QueryClient, QueryObserver } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { paidByQuery, sidebarPinsQuery, TASK_COUNTS_EVERY } from './queries';

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

it("reads a sidebar counting tasks again every few minutes and on coming back to the page; another sidebar waits for its events", async () => {
  vi.useFakeTimers();
  const client = new QueryClient();
  client.mount(); // hears the page come back into focus
  const sidebar = (slug: string, template: string) => {
    const options = sidebarPinsQuery(slug);
    const place = { pins: [{ item: 'card:c1', card: { template } }] } as never;
    client.setQueryData(options.queryKey, place);
    const read = vi.fn(async () => place);
    const stop = new QueryObserver(client, { ...options, queryFn: read, staleTime: Infinity, refetchOnMount: false }).subscribe(() => {});
    return { read, stop };
  };
  const counts = sidebar('ops', 'tasks_by_stage');
  const other = sidebar('sales', 'stat');
  try {
    await vi.advanceTimersByTimeAsync(TASK_COUNTS_EVERY);
    expect([counts.read.mock.calls.length, other.read.mock.calls.length]).toEqual([1, 0]);
    focusManager.setFocused(false);
    focusManager.setFocused(true);
    await vi.advanceTimersByTimeAsync(0);
    expect([counts.read.mock.calls.length, other.read.mock.calls.length]).toEqual([2, 0]);
  } finally {
    counts.stop();
    other.stop();
    focusManager.setFocused(undefined);
    client.unmount();
    client.clear();
    vi.useRealTimers();
  }
});
