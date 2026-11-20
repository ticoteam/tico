import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry } from '@/actions';
import { directoryQuery, queryClient, type Actor } from '@/data';
import type { SearchHit } from '@/search/define';
import { pipelinesQuery, type Pipeline } from './queries';
import { pipelinesSearch, tasksSearch, validateRoadmapSearch, validateTasksSearch } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;
const valid = (h: SearchHit) => registry.get(h.run.id)?.input.safeParse(h.run.input).success;
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
    expect(hits.map((h) => h.name)).toEqual(['#7', '#8']); // "7" or "#7" finds it by number
    expect(hits.every(valid)).toBe(true);
  });

  it('finds a pipeline by its name and opens its board', async () => {
    queryClient.setQueryData(pipelinesQuery.queryKey, [pipeline, { ...pipeline, id: 'p2', name: 'Seed orders' }]);
    const hits = await pipelinesSearch.find('garden', sig());
    expect(hits.map((h) => [h.title, h.run])).toEqual([['Garden', { id: 'tasks.open', input: { pipeline: 'p1' } }]]);
    expect(hits.every(valid)).toBe(true);
    expect(await pipelinesSearch.find('', sig())).toEqual([]);
  });

  it('finds a task by the id its board shows (G35001), first, and by the words as well', async () => {
    queryClient.setQueryData(pipelinesQuery.queryKey, [pipeline]);
    const asked: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      const words = new URL(url, 'http://x').searchParams.get('search') ?? '';
      if (url.startsWith('/api/v2/tasks?')) asked.push(words);
      const tasks = words === '35001' ? [{ id: 't1', number: 35001, title: 'Frost dates', pipeline_id: 'p1', stage_id: 's1', owner_id: null }] : [];
      return new Response(JSON.stringify({ tasks }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    const hits = await tasksSearch.find('G35001', sig());
    expect(asked.sort()).toEqual(['35001', 'G35001']);
    expect(hits.map((h) => [h.title, h.name])).toEqual([['Frost dates', 'G35001']]);
    // words that only look like an id ("v2") are words
    asked.length = 0;
    await tasksSearch.find('v2', sig());
    expect(asked).toEqual(['v2']);
  });

  it('finds nothing for no words, without asking', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    expect(await tasksSearch.find(' ', sig())).toEqual([]);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe('the tasks address', () => {
  it('keeps new=1 (the New task box up) on a pipeline, not on the Roadmap', () => {
    expect(validateTasksSearch({ new: '1', q: 'frost' })).toEqual({ q: 'frost', new: 1 });
    expect(validateTasksSearch({ new: 'yes' })).toEqual({});
    expect(validateRoadmapSearch({ new: '1' })).toEqual({});
  });
});
