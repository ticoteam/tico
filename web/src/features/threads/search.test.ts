import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry } from '@/actions';
import { directoryQuery, meQuery, queryClient, type Actor } from '@/data';
import type { SearchHit } from '@/search/define';
import type { Thread } from './model';
import { threadListQuery } from './queries';
import { threadMessagesSearch, threadsSearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;
const valid = (h: SearchHit) => registry.get(h.run.id)?.input.safeParse(h.run.input).success;
const pal: Actor = { id: 'human:2', kind: 'person', name: 'Robin Frost', hue: 5 };
const rows = [
  { id: 'th1', title: 'Seed order', audience: 'company', last_message_at: '2026-01-02T10:00:00Z' },
  { id: 'th2', title: 'Chat with Robin', audience: 'personal', dm_with: [{ actor: 'human:2', name: 'Robin Frost' }], last_message_at: '2026-01-03T10:00:00Z' },
  { id: 'th3', title: 'Frost dates', audience: 'company', task_id: 't7', last_message_at: '2026-01-01T10:00:00Z' },
] as unknown as Thread[];

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe('threads search', () => {
  it('finds threads and DMs by the name each shows, newest first, and opens them', async () => {
    queryClient.setQueryData(threadListQuery.queryKey, { pages: [{ rows, next: null }], pageParams: [null] });
    queryClient.setQueryData(meQuery.queryKey, { id: 'human:1' } as never);
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [pal], byId: { [pal.id]: pal } });
    const hits = await threadsSearch.find('frost', sig());
    expect(hits.map((h) => [h.title, h.run])).toEqual([
      ['Robin Frost', { id: 'threads.open', input: { thread: 'th2' } }],
      ['Frost dates', { id: 'threads.open', input: { thread: 'th3' } }],
    ]);
    expect(hits[0]!.lead).toMatchObject({ face: { initials: 'RF' } });
    expect(hits[1]!.lead).toEqual({ icon: 'tasks' });
    expect(hits.every(valid)).toBe(true);
    expect(await threadsSearch.find('', sig())).toEqual([]);
  });

  it('finds any thread the viewer may read by what was said in it, one row a thread, from the server', async () => {
    const asked: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      asked.push(String(url));
      const hit = (message: string, thread: string, excerpt: string, extra = {}) => ({ message_id: message, thread_id: thread, excerpt, ...extra });
      const hits = [
        hit('m3', 'old', 'the first\nfrost came early', { title: 'Seed order 2024', sender_id: 'human:2' }),
        hit('m2', 'old', 'frost again', { title: 'Seed order 2024', sender_id: 'human:2' }),
        hit('m1', 'gone', '…a late frost', { sender_id: 'bot:unknown' }),
      ];
      return new Response(JSON.stringify({ hits, next_cursor: null }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [pal], byId: { [pal.id]: pal } });
    expect(threadMessagesSearch.remote).toBe(true);
    const hits = await threadMessagesSearch.find(' frost ', sig());
    expect(new URL(asked[0]!, 'http://x').searchParams.get('q')).toBe('frost');
    expect(hits.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['the first frost came early', 'Seed order 2024', { id: 'threads.open', input: { thread: 'old' } }],
      ['…a late frost', undefined, { id: 'threads.open', input: { thread: 'gone' } }],
    ]);
    expect(hits[0]!.lead).toMatchObject({ face: { initials: 'RF' } });
    expect(hits[1]!.lead).toEqual({ icon: 'threads' });
    expect(hits.every(valid)).toBe(true);
    expect(await threadMessagesSearch.find('  ', sig())).toEqual([]);
    expect(asked).toHaveLength(1);
  });
});
