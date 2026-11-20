import { queryOptions } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { queryClient } from '@/data';
import { fetchFor } from './helpers';
import { pages } from './pages';
import { loadProviders, providers } from './providers';

// Registration is what this test exercises. The queries run only when a provider searches.
vi.mock('@/features/calls/queries', () => ({}));
vi.mock('@/features/company/queries', () => ({}));
vi.mock('@/features/library/queries', () => ({}));
vi.mock('@/features/routines/queries', () => ({}));
vi.mock('@/features/tasks/queries', () => ({}));
vi.mock('@/features/threads/queries', () => ({}));

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('search helpers', () => {
  it('cancels the server read when the person types on', async () => {
    let seen: AbortSignal | null | undefined;
    vi.stubGlobal('fetch', (_url: string, init?: RequestInit) => {
      seen = init?.signal;
      return new Promise((_ok, fail) => init?.signal?.addEventListener('abort', () => fail(new DOMException('aborted', 'AbortError'))));
    });
    const typed = new AbortController();
    const read = fetchFor(queryOptions({ queryKey: ['test', 'search', 'fro'], queryFn: ({ signal }) => api.get('/api/v2/x', { signal }) }), typed.signal);
    await vi.waitFor(() => expect(seen).toBeTruthy());
    typed.abort();
    await expect(read).rejects.toBeTruthy();
    expect(seen?.aborted).toBe(true);
  });

  it('finds a menu page by name, and nothing for no words', async () => {
    const hits = await pages.find('finance', new AbortController().signal);
    expect(hits.map((h) => [h.title, h.run])).toEqual([['Company, Finance', { id: 'shell.goTo', input: { to: '/company/finance' } }]]);
    expect(await pages.find('  ', new AbortController().signal)).toEqual([]);
  });

  it('has people and pages from the start, and loads the features’ providers once and keeps them', async () => {
    expect(providers.map((p) => p.group)).toEqual(['People', 'Pages']);
    const first = loadProviders();
    expect(loadProviders()).toBe(first);
    const all = await first;
    expect(providers).toBe(all);
    expect(all.map((p) => p.group)).toEqual(expect.arrayContaining(['People', 'Pages', 'Tasks', 'Threads', 'Files', 'Contacts', 'Meetings']));
  });
});
