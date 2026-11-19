import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry } from '@/actions';
import { directoryQuery, queryClient, type Actor } from '@/data';
import type { SearchHit } from '@/search/define';
import { pipelinesQuery, type Pipeline } from './queries';
import { tasksSearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;
const valid = (h: SearchHit) => registry.get(h.run.id)?.input.parse(h.run.input).ok;
const pipeline: Pipeline = { id: 'p1', name: 'Garden', stages: [{ id: 's1', name: 'Sowing' }], owner: null, done: null, closed: null, mayChange: true };
const owner: Actor = { id: 'bot:helper', kind: 'agent', name: 'Seed Helper', hue: 3 };

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('tasks search', () => {
  it('asks the server for the words and opens the task in its pipeline', async () => {
    queryClient.setQueryData(pipelinesQuery.queryKey, [pipeline]);
    queryClient.setQueryData(directoryQuery.queryKey, { actors: [owner], byId: { [owner.id]: owner } });
    const urls: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      urls.push(url);
      const tasks = [
        { id: 't7', number: 7, title: 'Frost dates', pipeline_id: 'p1', stage_id: 's1', owner_id: 'bot:helper' },
        { id: 't8', number: 8, title: 'Frost cloth', pipeline_id: null, stage_id: null, owner_id: null },
      ];
      return new Response(JSON.stringify({ tasks, cursor: 'c' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const hits = await tasksSearch.find('frost', sig());
    expect(new URL(urls[0]!, 'http://x').searchParams.get('search')).toBe('frost');
    expect(hits.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['Frost dates', 'Sowing', { id: 'task.open', input: { task: 7, pipeline: 'p1' } }],
      ['Frost cloth', undefined, { id: 'task.open', input: { task: 8 } }],
    ]);
    expect(hits[0]!.lead).toMatchObject({ face: { initials: 'SH', bot: true } });
    expect(hits.every(valid)).toBe(true);
  });

  it('finds nothing for no words, without asking', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    expect(await tasksSearch.find(' ', sig())).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});
