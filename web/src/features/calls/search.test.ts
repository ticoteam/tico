import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry } from '@/actions';
import { queryClient } from '@/data';
import { meetingsSearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('meetings search', () => {
  it("uses List's server search, title matches first, and opens the meeting beside List", async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      urls.push(url);
      const meetings = [
        { id: 'm1', title: 'Weekly sync', review_state: 'live', started: '2026-01-02T10:00:00Z' },
        { id: 'm2', title: 'Frost planning', review_state: 'live', started: '2026-01-01T10:00:00Z' },
      ];
      return new Response(JSON.stringify({ meetings, count: 2, pending_count: 0 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const hits = await meetingsSearch.find('frost', sig());
    expect(urls[0]).toContain('q=frost');
    expect(hits.map((h) => [h.title, h.run])).toEqual([
      ['Frost planning', { id: 'calls.openMeeting', input: { id: 'm2' } }],
      ['Weekly sync', { id: 'calls.openMeeting', input: { id: 'm1' } }],
    ]);
    expect(hits.every((h) => registry.get(h.run.id)?.input.parse(h.run.input).ok)).toBe(true);
    expect(await meetingsSearch.find('', sig())).toEqual([]);
  });
});
