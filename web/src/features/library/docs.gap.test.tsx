import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { ContactsPage } from './ContactsPage';
import { DocPage } from './DocPage';
import { DocsPage } from './DocsPage';
import { archivedDocsQuery, attributesQuery, cardQuery, contactsQuery, countsQuery, docQuery, docsListQuery, docVersionsQuery, sourcesQuery, typesQuery, type Card } from './queries';

/* Docs' full-text search, a doc typed in place, and a contact's Rules, Links and a place. Sample rows only. */

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
    queryClient.setQueryData(docsListQuery.queryKey, { pages: [{ docs: [row('d1', 'Plan', 'company/plan.md')], next_cursor: null }], pageParams: [''] });
    at('/library/docs?q=water', <DocsPage search={{ q: 'water' }} />);
    expect((await screen.findByText('Watering schedule')).closest('a')?.getAttribute('href')).toBe('/library/d9');
    expect(screen.getByText('Soil guide').closest('a')?.getAttribute('href')).toBe('/library/ext:x1');
    expect(screen.getByText('Garden wiki')).toBeTruthy();
    expect(sent.some((x) => x.url.startsWith('/api/v2/docs/search?q=water'))).toBe(true);
  });

  it('keeps matching titles in the archive, without the server search @s773', async () => {
    const sent = serve();
    queryClient.setQueryData(docsListQuery.queryKey, { pages: [{ docs: [], next_cursor: null }], pageParams: [''] });
    queryClient.setQueryData(archivedDocsQuery.queryKey, { pages: [{ docs: [row('d2', 'Old plan', 'company/old.md')], next_cursor: null }], pageParams: [''] });
    at('/library/docs?q=old&archived=1', <DocsPage search={{ q: 'old', archived: 1 }} />);
    expect(await screen.findByText('Old plan')).toBeTruthy();
    expect(sent.some((x) => x.url.includes('/docs/search'))).toBe(false);
  });

  it('searches document bodies for words written without ASCII letters', async () => {
    const sent = serve((url) =>
      url.startsWith('/api/v2/docs/search')
        ? { results: [{ type: 'internal', id: 'd1', title: 'The garden', path: 'garden.md', excerpt: '庭園', score: 1 }] }
        : {},
    );
    queryClient.setQueryData(docsListQuery.queryKey, { pages: [{ docs: [row('d1', 'The garden', 'garden.md')], next_cursor: null }], pageParams: [''] });
    at('/library/docs?q=庭園', <DocsPage search={{ q: '庭園' }} />);
    expect(await screen.findByText('The garden')).toBeTruthy();
    expect(sent.some((x) => x.url.startsWith('/api/v2/docs/search?q=' + encodeURIComponent('庭園')))).toBe(true);
  });
});

describe('a doc', () => {
  it('renames from its title in the head, sending only the title and the version it read @s815', async () => {
    const sent = serve((_url, method) => (method === 'PATCH' ? { doc: full({ title: 'Plan B', version: 4 }) } : { docs: [], next_cursor: null }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    fireEvent.click(await screen.findByRole('heading', { name: 'Plan' }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Plan B' } });
    fireEvent.keyDown(title, { key: 'Enter' });
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]).toMatchObject({ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, title: 'Plan B' } });
  });

  it('is typed in place, with no edit mode, and saves on leaving the text @s815', async () => {
    // the write reads the doc again after: the saved text
    const sent = serve((url) => (url === '/api/v2/docs/d1' ? { doc: full({ body: 'Grow fast.', version: 4 }) } : { docs: [], next_cursor: null, versions: [] }));
    queryClient.setQueryData(docQuery('d1').queryKey, full());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    fireEvent.focus(await screen.findByLabelText('Text'));
    const text = await screen.findByRole('textbox', { name: 'Text' });
    fireEvent.change(text, { target: { value: 'Grow fast.' } });
    fireEvent.blur(text);
    await waitFor(() => expect(writes(sent)).toHaveLength(1));
    expect(writes(sent)[0]).toMatchObject({ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, body: 'Grow fast.' } });
    expect(await screen.findByText('Grow fast.')).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Text' })).toBeNull();
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

  it('shows the rules that apply to a place, a pill only for Block @s786', async () => {
    const rules = [
      { text: 'Price lists go to Finance', effect: 'escalate' },
      { text: 'No signs on the green', effect: 'block' },
    ];
    serve((url) => (url.startsWith('/api/v2/entities/rules') ? { rules } : url.startsWith('/api/v2/entities/self') ? { id: 'us', name: 'Sample Co', operates_in: [] } : { rows: [], next: null }));
    seed('place', card({ type: 'place', place: { kind: 'county', governing_body: '', shared: false } }));
    at('/library/contacts?type=place&panel=c1', <ContactsPage search={{ type: 'place', panel: 'c1' }} />);
    expect(await screen.findByText('Price lists go to Finance')).toBeTruthy();
    expect(screen.getByText('Ask').closest('.pill')).toBeNull();
    expect(screen.getByText('Block').closest('.pill')).toBeTruthy();
  });

  it('a person has Rules too, and the threads that name them sit in Links @s786', async () => {
    serve((url) =>
      url.startsWith('/api/v2/entities/rules')
        ? { rules: [] }
        : url.startsWith('/api/v2/threads/search')
          ? { hits: [{ thread_id: 'th1', title: 'Spring order', excerpt: '', created_at: null }] }
          : { rows: [], next: null },
    );
    seed('person', card({}));
    at('/library/contacts?panel=c1', <ContactsPage search={{ panel: 'c1' }} />);
    expect(await screen.findByText('None apply')).toBeTruthy();
    expect((await screen.findByText('Spring order')).closest('a')?.getAttribute('href')).toBe('/threads/th1');
    expect(screen.getByText('Links')).toBeTruthy();
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
    expect(await screen.findByText('We operate here, Shared')).toBeTruthy();
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
