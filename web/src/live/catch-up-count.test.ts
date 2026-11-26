import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryObserver } from '@tanstack/react-query';
import { api } from '@/api/client';
import { queryClient } from '@/data/query-client';
import { catchUpQuery } from '@/features/threads/routing';
import { applyThreadEvents, resetPatchState } from './patch';

// The menu's Catch up badge and asks (spec: the menu counts Catch up without loading it): the app reads only the
// count route, never the page of cards, on load and as the live stream reports new messages, reads and archives.
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  resetPatchState();
});

afterEach(() => {
  resetPatchState();
  queryClient.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('the menu\'s Catch up read', () => {
  it('reads the count route, never Catch up\'s cards, and at most once a second through 10 live events', async () => {
    const reads: { url: string; at: number }[] = [];
    vi.spyOn(api, 'get').mockImplementation(async (url: string) => {
      reads.push({ url, at: Date.now() });
      return { count: 1, ids: ['t1'], mentions: [] } as never;
    });
    const watching = new QueryObserver(queryClient, catchUpQuery);
    const stop = watching.subscribe(() => undefined);
    await vi.advanceTimersByTimeAsync(0);
    expect(reads.map((r) => r.url)).toEqual(['/api/v2/threads/catch-up/count']);
    const kinds = ['activity', 'read', 'archived'];
    for (let n = 0; n < 10; n++) {
      applyThreadEvents([{ kind: kinds[n % 3]!, thread_id: `t${n}`, root_id: `t${n}` }]);
      await vi.advanceTimersByTimeAsync(300);
    }
    await vi.advanceTimersByTimeAsync(2000);
    stop();
    expect(reads.length).toBeGreaterThan(1);
    expect(reads.every((r) => r.url === '/api/v2/threads/catch-up/count')).toBe(true);
    // The stream's reads (after the page's own first read) are a second apart at least: 10 events in 3 s, 4 reads.
    const stream = reads.slice(1);
    const gaps = stream.slice(1).map((r, i) => r.at - stream[i]!.at);
    expect(stream.length).toBeLessThanOrEqual(4);
    expect(Math.min(...gaps)).toBeGreaterThanOrEqual(1000);
  });
});
