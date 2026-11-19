import { createMemoryHistory, createRootRoute, createRoute, createRouter } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { registry, type Ctx } from '@/actions';
import { queryClient } from '@/data';
import type { SearchHit } from '@/search/define';
import { countsQuery, docsListQuery, skillsQuery, toolsQuery, typesQuery } from './queries';
import { contactsFind, docsFind, filesFind, skillsFind, toolsFind } from './search';
import { contactsOpen, filesOpen } from './actions';

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
    queryClient.setQueryData(docsListQuery.queryKey, { pages: [{ docs: [doc, { ...doc, id: 'd2', title: 'Seed list' }], next_cursor: null }], pageParams: [''] });
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

  it('asks the server for files and for the four contact types with the most, and opens each hit', async () => {
    queryClient.setQueryData(countsQuery.queryKey, [
      { type: 'person', count: 3 },
      { type: 'organization', count: 2 },
      { type: 'place', count: 0 },
      { type: 'farm', count: 1 },
      { type: 'shop', count: 5 },
      { type: 'event', count: 4 },
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
    expect(runs(files)).toEqual([['frost-dates.csv', { id: 'files.open', input: { file: 'f1' } }]]);
    expect(contacts.map((h) => [h.title, h.meta, h.run])).toEqual([
      ['Robin Frost', 'Person', { id: 'contacts.open', input: { contact: 'c1', type: 'person' } }],
      ['Frost Farms', 'Organization', { id: 'contacts.open', input: { contact: 'c2', type: 'organization' } }],
    ]);
    const types = asked.filter((u) => u.startsWith('/api/v2/entities/find/')).map((u) => u.split('/').pop());
    expect(types.sort()).toEqual(['event', 'organization', 'person', 'shop']);
    expect([...files, ...contacts].every(valid)).toBe(true);
  });

  it('opens a file or a contact from any page, and keeps the page’s own words when already on it', async () => {
    const at = (where: string) => {
      const root = createRootRoute();
      const pages = ['library/files', 'library/contacts', 'threads/$thread'].map((path) => createRoute({ getParentRoute: () => root, path }));
      const router = createRouter({ routeTree: root.addChildren(pages), history: createMemoryHistory({ initialEntries: [where] }) });
      return { ctx: { router, queryClient, via: 'search' } as Ctx, here: () => [router.state.location.pathname, router.state.location.search] };
    };
    const away = at('/threads/t1?chat=agent:helper');
    await filesOpen.run(away.ctx, { file: 'f1' });
    expect(away.here()).toEqual(['/library/files', { file: 'f1' }]);
    await contactsOpen.run(away.ctx, { contact: 'c2', type: 'organization' });
    expect(away.here()).toEqual(['/library/contacts', { type: 'organization', panel: 'c2' }]);

    const files = at('/library/files?kind=pdf');
    await filesOpen.run(files.ctx, { file: 'f1' });
    expect(files.here()).toEqual(['/library/files', { kind: 'pdf', file: 'f1' }]);
    const contacts = at('/library/contacts?type=organization&q=frost');
    await contactsOpen.run(contacts.ctx, { contact: 'c1', type: 'person' });
    expect(contacts.here()).toEqual(['/library/contacts', { panel: 'c1' }]);
    await contactsOpen.run(contacts.ctx, { contact: null });
    expect(contacts.here()).toEqual(['/library/contacts', {}]);
  });

  it('finds nothing for no words', async () => {
    for (const p of [docsFind, filesFind, skillsFind, toolsFind, contactsFind]) expect(await p.find(' ', sig())).toEqual([]);
  });
});
