import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry } from '@/actions';
import { queryClient } from '@/data';
import type { SearchHit } from '@/search/define';
import { countsQuery, docsListQuery, skillsQuery, toolsQuery, typesQuery } from './queries';
import { contactsFind, docsFind, filesFind, skillsFind, toolsFind } from './search';

// Made-up rows only.
const sig = () => new AbortController().signal;
const valid = (h: SearchHit) => registry.get(h.run.id)?.input.parse(h.run.input).ok;
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
const runs = (hits: SearchHit[]) => hits.map((h) => [h.title, h.run]);

afterEach(() => {
  vi.unstubAllGlobals();
  queryClient.clear();
});

describe('library search', () => {
  it('finds docs, skills and tools from what Library read, each opening itself', async () => {
    const doc = { id: 'd1', title: 'Frost guide', path: '', locked: false, updated_at: '2026-01-02T10:00:00Z', updated_by_id: '', updated_by_name: '', version: 1 };
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [doc, { ...doc, id: 'd2', title: 'Seed list' }] } as never);
    queryClient.setQueryData(skillsQuery.queryKey, [{ id: 'k1', name: 'frost-date-lookup', description: '', latest: null, installs_count: 0, on: [] }]);
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [{ id: 'tl1', name: 'Frost Weather', vendor_name: 'Weather Co' }, { id: 'tl2', name: 'Mailer' }] } as never);

    const docs = await docsFind.find('frost', sig());
    const skills = await skillsFind.find('frost', sig());
    const tools = await toolsFind.find('frost', sig());
    expect(runs(docs)).toEqual([['Frost guide', { id: 'docs.open', input: { doc: 'd1' } }]]);
    expect(runs(skills)).toEqual([['frost-date-lookup', { id: 'skills.open', input: { skill: 'frost-date-lookup' } }]]);
    expect(runs(tools)).toEqual([['Frost Weather', { id: 'tools.open', input: { tool: 'tl1' } }]]);
    expect(tools[0]!.meta).toBe('Weather Co');
    expect([...docs, ...skills, ...tools].every(valid)).toBe(true);
  });

  it('asks the server for files and for each contact type that has any', async () => {
    queryClient.setQueryData(countsQuery.queryKey, [
      { type: 'person', count: 3 },
      { type: 'organization', count: 1 },
      { type: 'place', count: 0 },
    ]);
    queryClient.setQueryData(typesQuery.queryKey, [
      { key: 'person', name: 'Person', plural_name: 'People' },
      { key: 'organization', name: 'Organization', plural_name: 'Organizations' },
    ]);
    const asked: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      asked.push(new URL(url, 'http://x').pathname);
      if (url.startsWith('/api/v2/files/search')) return json({ results: [{ id: 'f1', title: 'frost-dates.csv' }] });
      if (url.startsWith('/api/v2/entities/find/person')) return json({ rows: [{ id: 'c1', name: 'Robin Frost', type: 'person' }] });
      if (url.startsWith('/api/v2/entities/find/organization')) return json({ rows: [{ id: 'c2', name: 'Frost Farms', type: 'organization' }] });
      return json({});
    });
    const files = await filesFind.find('frost', sig());
    const contacts = await contactsFind.find('frost', sig());
    expect(runs(files)).toEqual([['frost-dates.csv', { id: 'files.list', input: { q: 'frost-dates.csv' } }]]);
    expect(contacts.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['Robin Frost', 'Person', { id: 'contacts.list', input: { q: 'Robin Frost' } }],
      ['Frost Farms', 'Organization', { id: 'contacts.list', input: { type: 'organization', q: 'Frost Farms' } }],
    ]);
    expect(asked).not.toContain('/api/v2/entities/find/place');
    expect([...files, ...contacts].every(valid)).toBe(true);
  });

  it('finds nothing for no words', async () => {
    for (const p of [docsFind, filesFind, skillsFind, toolsFind, contactsFind]) expect(await p.find(' ', sig())).toEqual([]);
  });
});
