import { afterEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { catchUpQuery, readAll, type CatchUpCount } from './queries';

// Made-up threads only.
afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("Catch up's count", () => {
  it('is read from the count route, not the page of cards', async () => {
    const asked: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      asked.push(new URL(String(url), 'http://x').pathname);
      return new Response(JSON.stringify({ count: 0, ids: [], mentions: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    await queryClient.fetchQuery(catchUpQuery);
    expect(asked).toEqual(['/api/v2/threads/catch-up/count']);
  });

  it('Mark read takes the threads out of the count, its roots and its mentions at once', async () => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    vi.stubGlobal('fetch', async () => {
      await held;
      return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData<CatchUpCount>(catchUpQuery.queryKey, {
      count: 3,
      ids: ['t1', 't2', 't3'],
      mentions: [{ id: 't1', audience: 'members' }, { id: 't3', audience: 'members' }],
    });
    const done = readAll(['t1', 't2']);
    await vi.waitFor(() => expect(queryClient.getQueryData<CatchUpCount>(catchUpQuery.queryKey)?.count).toBe(1));
    expect(queryClient.getQueryData(catchUpQuery.queryKey)).toEqual({ count: 1, ids: ['t3'], mentions: [{ id: 't3', audience: 'members' }] });
    release();
    await done;
  });
});
