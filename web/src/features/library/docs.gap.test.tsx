import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { ContactsPage } from './ContactsPage';
import { DocPage } from './DocPage';
import { DocsPage } from './DocsPage';
import { FilesPage } from './FilesPage';
import { archivedDocsQuery, attributesQuery, cardQuery, contactsQuery, countsQuery, docQuery, docsListQuery, docVersionsQuery, sourcesQuery, typesQuery, type Card } from './queries';
import { filesSearch } from './search';

/* The gap wave: Docs' full-text search, a doc's Research, its Path and the unsaved-changes guard, Files' filters, and a
   contact's Rules and a place. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], {
    actors: [
      { id: 'bot:fern', kind: 'agent', name: 'Fern', hue: 1 },
      { id: 'human:sam', kind: 'person', name: 'Sam Sample', hue: 2 },
    ],
    byId: {},
  });
  queryClient.setQueryData(sourcesQuery.queryKey, { sources: [], next_cursor: null });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

interface Sent {
  url: string;
  method: string;
  body: unknown;
}
function serve(reply: (url: string, method: string) => unknown = () => ({})) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    return new Response(JSON.stringify(reply(String(url), method)), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}

function at(path: string, node: ReactNode = null) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: path.split('?')[0] ?? '/', component: () => node });
  const any = createRoute({ getParentRoute: () => root, path: '$', component: () => null });
  const router = createRouter({ routeTree: root.addChildren([page, any]), history: createMemoryHistory({ initialEntries: [path] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET');
const row = (id: string, title: string, path: string) => ({ id, title, path, locked: false, updated_at: '2026-11-01T00:00:00Z', updated_by_id: '', updated_by_name: 'Sam', version: 3 });
const full = (o: Record<string, unknown> = {}) => ({
  ...row('d1', 'Plan', 'company/plan.md'),
  body: '# Goals\n\nGrow slowly.\n\n## Next\n\nHire.',
  archived: false,
  created_at: '',
  created_by_id: '',
  created_by_name: '',
  ...o,
});

describe('Docs search', () => {
  it('searches the docs’ text on the server and opens each hit where it lives @s773', async () => {
    const sent = serve((url) =>
      url.startsWith('/api/v2/docs/search')
        ? {
            results: [
              { type: 'internal', id: 'd9', title: 'Watering schedule', path: 'garden/watering.md', excerpt: 'twice a week', score: 2 },
              { type: 'external', id: 'x1', title: 'Soil guide', path: 'soil.md', source_title: 'Garden wiki', citation_url: 'https://example.com/soil', excerpt: '', score: 1 },
            ],
          }
        : {},
    );
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [row('d1', 'Plan', 'company/plan.md')], next_cursor: null });
    at('/library/docs?q=water', <DocsPage search={{ q: 'water' }} />);
    expect((await screen.findByText('Watering schedule')).closest('a')?.getAttribute('href')).toBe('/library/d9');
    expect(screen.getByText('Soil guide').closest('a')?.getAttribute('href')).toBe('/library/ext:x1');
    expect(screen.getByText('Garden wiki')).toBeTruthy();
    expect(sent.some((x) => x.url.startsWith('/api/v2/docs/search?q=water'))).toBe(true);
  });

  it('keeps matching titles in the archive, without the server search @s773', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, { docs: [], next_cursor: null });
    queryClient.setQueryData(archivedDocsQuery.queryKey, { docs: [row('d2', 'Old plan', 'company/old.md')], next_cursor: null });
    at('/library/docs?q=old&archived=1', <DocsPage search={{ q: 'old', archived: 1 }} />);
    expect(await screen.findByText('Old plan')).toBeTruthy();
    expect(sent.some((x) => x.url.includes('/docs/search'))).toBe(false);
  });
});

describe('a doc', () => {
  it('asks the Librarian to research a section, from the doc’s ⋯ @s774 @s818', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { task_id: 't-77', bot: 'librarian', kind: 'research', target: 'd1' } : {}));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Research' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'What to find' }), { target: { value: 'Local hiring rules' } });
    fireEvent.click(screen.getByRole('button', { name: 'Start research' }));
    expect(await screen.findByText('Research started')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/librarian/research', method: 'POST', body: { target: 'd1', instructions: 'Local hiring rules' } }]);
    // A section by its heading, and Tico may start it too (ordinary work).
    expect((await run('docs.research', { doc: 'd1', section: 'Next', instructions: 'Salaries' }, { via: 'tico' })).ok).toBe(true);
    expect(writes(sent)[1]?.body).toEqual({ target: 'd1', section: 'Next', instructions: 'Salaries' });
  });

  it('moves a doc by its Path, sending only what changed @s815', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { doc: full({ path: 'company/plans/plan.md', version: 4 }) } : { docs: [], next_cursor: null }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    const router = at('/library/d1?edit=1', <DocPage doc="d1" search={{ edit: 1 }} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Path' }), { target: { value: 'company/plans/plan.md' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]).toMatchObject({ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, path: 'company/plans/plan.md' } });
    // the save's own way out is not stopped
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('asks before leaving with unsaved changes: Keep editing stays, Discard leaves @s815', async () => {
    serve();
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    const router = at('/library/d1?edit=1', <DocPage doc="d1" search={{ edit: 1 }} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Text' }), { target: { value: 'Half a thought' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(await screen.findByRole('alertdialog', { name: 'Discard changes?' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    expect(router.state.location.search).toMatchObject({ edit: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

describe('Files filters', () => {
  const Live = () => <FilesPage search={filesSearch(useSearch({ strict: false }))} />;
  const empty = { files: [], has_more: false, next_cursor: null };

  it('puts each choice of the one Filter menu in the address @s779', async () => {
    serve((url) => (url.startsWith('/api/v2/groups') ? [{ id: 'g1', name: 'Garden', bot_slugs: [], bots: [], order: 0, parent: '', people: [] }] : empty));
    const router = at('/library/files', <Live />);
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Role' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Spec' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ role: 'Spec' }));
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Agent' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Fern' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ role: 'Spec', agent: 'bot:fern' }));
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pinned' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ role: 'Spec', agent: 'bot:fern', pinned: 1 }));
  });

  it('reads the files by every filter, shows a chip for each, and clears them @s779', async () => {
    const sent = serve(() => empty);
    const router = at('/library/files?role=Spec&agent=bot%3Afern&days=7&task=t-5&archived=1', <Live />);
    expect(await screen.findByText('Spec')).toBeTruthy();
    for (const chip of ['Fern', 'Past week', 'One task', 'Archived']) expect(screen.getByText(chip)).toBeTruthy();
    await waitFor(() => expect(sent.some((x) => x.url.startsWith('/api/v2/files?'))).toBe(true));
    const read = new URL(sent.find((x) => x.url.startsWith('/api/v2/files?'))!.url, 'http://x').searchParams;
    expect(Object.fromEntries([...read].filter(([k]) => k !== 'since'))).toEqual({ limit: '100', role: 'Spec', bot: 'bot:fern', task: 't-5', archived: 'true' });
    expect(read.get('since')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Clear Spec' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('role'));
    fireEvent.click(await screen.findByRole('button', { name: 'Clear' }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

describe('a contact', () => {
  const card = (o: Partial<Card> & Record<string, unknown>): Card => ({
    id: 'c1',
    type: 'person',
    name: 'Ada Sample',
    status: 'active',
    primary_email: null,
    primary_domain: null,
    last_activity_at: null,
    summary: null,
    values: {},
    links: [],
    notes: [],
    ...o,
  });
  const seed = (type: string, c: Card) => {
    queryClient.setQueryData(typesQuery.queryKey, [{ key: type, name: type, plural_name: type }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type, count: 1 }]);
    queryClient.setQueryData(contactsQuery(type, '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    queryClient.setQueryData(cardQuery(c.id).queryKey, c);
    queryClient.setQueryData(attributesQuery(type).queryKey, []);
  };

  it('shows the rules that apply, with their effect, or None apply @s786', async () => {
    serve((url) => (url.startsWith('/api/v2/entities/rules') ? { rules: [{ text: 'Price lists go to Finance', effect: 'escalate', path: ['Ada Sample', 'Supplier'] }] } : { rows: [], next: null }));
    seed('person', card({}));
    at('/library/contacts?panel=c1', <ContactsPage search={{ panel: 'c1' }} />);
    expect(await screen.findByText('Price lists go to Finance')).toBeTruthy();
    expect(screen.getByText('Ask')).toBeTruthy();
    expect(screen.getByText('Rules')).toBeTruthy();
  });

  it('shows a place’s kind, what it is in, its bodies, what sits in it, and why its rules bind us @s786 @s810', async () => {
    serve((url) =>
      url.startsWith('/api/v2/entities/self')
        ? { id: 'us', name: 'Sample Co', operates_in: [{ id: 'p1', name: 'Fern County' }] }
        : url.startsWith('/api/v2/entities/rules')
          ? { rules: [] }
          : { rows: [], next: null },
    );
    const link = (id: string, key: string, name: string, from: string, other: [string, string, string], inverse: string | null = null) => ({
      id,
      link_key: key,
      link_name: name,
      inverse_name: inverse,
      from_entity_id: from,
      to_entity_id: from === 'p1' ? other[0] : 'p1',
      other_id: other[0],
      other_name: other[1],
      other_type: other[2],
    });
    seed(
      'place',
      card({
        id: 'p1',
        type: 'place',
        name: 'Fern County',
        place: { kind: 'county', governing_body: 'Fern County Board', shared: true },
        links: [
          link('l1', 'located_in', 'Located in', 'p1', ['p0', 'Sample State', 'place']),
          link('l2', 'regulates', 'Regulates', 'o9', ['o9', 'Water Office', 'organization'], 'Regulated by'),
          link('l3', 'located_in', 'Located in', 'p2', ['p2', 'Fern Town', 'place']),
          link('l4', 'located_in', 'Located in', 'o5', ['o5', 'Seed Depot', 'organization']),
        ],
      }),
    );
    at('/library/contacts?type=place&panel=p1', <ContactsPage search={{ type: 'place', panel: 'p1' }} />);
    expect(await screen.findByText('County')).toBeTruthy();
    expect(screen.getByText('Sample State').closest('a')).toBeTruthy();
    expect(screen.getByText('Fern County Board')).toBeTruthy();
    expect(screen.getByText('Shared')).toBeTruthy();
    expect(await screen.findByText('We operate here')).toBeTruthy();
    expect(screen.getByText('Located in')).toBeTruthy();
    for (const name of ['Bodies', 'Places inside', 'Located here', 'Why it applies']) expect(screen.getByText(name)).toBeTruthy();
    expect(screen.getByText('Water Office')).toBeTruthy();
    expect(screen.getByText('Regulated by')).toBeTruthy();
    expect(screen.getByText('Fern Town')).toBeTruthy();
    expect(screen.getByText('Seed Depot')).toBeTruthy();
    expect(screen.getByText('Sample Co')).toBeTruthy();
    expect(await screen.findByText('None apply')).toBeTruthy();
  });
});
