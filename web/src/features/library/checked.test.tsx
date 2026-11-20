import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { ContactsPage } from './ContactsPage';
import { DocPage } from './DocPage';
import { FilePreview } from './FileViewer';
import { FilesPage } from './FilesPage';
import {
  attributesQuery,
  cardQuery,
  contactsQuery,
  countsQuery,
  docQuery,
  docVersionQuery,
  docVersionsQuery,
  fileQuery,
  filesQuery,
  fileVersionsQuery,
  typesQuery,
  type Card,
} from './queries';
import { contactsSearch } from './search';
import { viewing } from './viewing';

/* One test per Library page the old app had, by what a person sees and presses there, and what is sent. */

// jsdom lays nothing out, so the virtual list would draw no rows: here it draws them all (its own tests cover the rest).
vi.mock('@/kit/VirtualList', () => ({
  VirtualList: <T,>({ items, getKey, renderItem, label }: { items: readonly T[]; getKey: (t: T) => string; renderItem: (t: T, i: number) => ReactNode; label?: string }) => (
    <div role="list" aria-label={label}>
      {items.map((t, i) => (
        <div key={getKey(t)}>{renderItem(t, i)}</div>
      ))}
    </div>
  ),
}));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  viewing.set(null);
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
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
    const r = reply(String(url), method);
    if (r instanceof Response) return r;
    return new Response(JSON.stringify(r), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));

function holdResponse() {
  let send: (response: Response) => void = () => {};
  const response = new Promise<Response>((resolve) => {
    send = resolve;
  });
  return { response, send };
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
    </QueryClientProvider>,
  );
  return router;
}
const linkOf = (name: string) => screen.getByRole('link', { name }).getAttribute('href');

// Made-up files, docs, contacts and tools only.
const file = (o: Record<string, unknown> = {}) => ({
  id: 'f1',
  title: 'Sample notes',
  name: 'notes.txt',
  version: 2,
  kind: 'file',
  library_kind: 'document',
  mime: 'text/plain',
  bot: 'helper',
  last_activity_at: '2026-11-01T00:00:00Z',
  locator: 'blob',
  open: null,
  ...o,
});
const version = (n: number, current = false) => ({ version: n, current, name: 'notes.txt', size: 12, mime: 'text/plain', actor: null, created_at: '2026-11-01T00:00:00Z', url: '' });

describe('a file', () => {
  it('shows its text, Download and its versions, each a link @s52', async () => {
    serve(() => 'plain words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    expect(await screen.findByText('plain words')).toBeTruthy();
    expect(linkOf('Download')).toBe('/api/v2/files/f1/versions/2');
    expect(screen.getByRole('link', { name: /v1/ }).getAttribute('href')).toBe('/library/files?file=f1&v=1');
  });

  it('opens a linked file where it lives, with no Download @s52', async () => {
    const url = 'https://docs.google.com/document/d/abcdefghijk123/edit';
    serve();
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ locator: 'remote_link', open: { type: 'external', url } }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, []);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    expect((await screen.findByRole('link', { name: 'Open in Google' })).getAttribute('href')).toBe(url);
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(document.querySelector('iframe')?.getAttribute('src')).toBe('https://docs.google.com/document/d/abcdefghijk123/preview');
  });

  it('draws the version asked for, as a task shows it @s76', async () => {
    const sent = serve((url) => (url.endsWith('/versions/1') ? 'first words' : 'later words'));
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/tasks/7', <FilePreview file="f1" version={1} />);
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(sent.map((x) => x.url)).toEqual(['/api/v2/files/f1/versions/1']);
  });

  it('uses the historical version’s file type instead of the latest one', async () => {
    serve(() => 'first words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ mime: 'image/png', library_kind: 'image', name: 'notes.png' }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [
      { ...version(2, true), mime: 'image/png', name: 'notes.png' },
      version(1),
    ]);
    at('/library/files?file=f1&v=1', <FilesPage search={{ file: 'f1', v: 1 }} />);
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(screen.queryByRole('img', { name: 'Sample notes' })).toBeNull();
  });

  it('keeps quoted CSV data in its original cells', async () => {
    const csv = '\uFEFFName,Notes\r\n"Acme, Inc.","First line\r\nSecond ""quoted"" line"';
    serve(() => new Response(csv, { headers: { 'Content-Type': 'text/csv' } }));
    queryClient.setQueryData(fileQuery('f1').queryKey, file({ mime: 'text/csv', name: 'notes.csv' }) as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [{ ...version(2, true), mime: 'text/csv', name: 'notes.csv' }]);
    at('/library/files?file=f1', <FilesPage search={{ file: 'f1' }} />);
    const table = await screen.findByRole('table');
    expect(within(table).getAllByRole('row')).toHaveLength(2);
    expect(within(table).getAllByRole('cell').map((cell) => cell.textContent)).toEqual(['Acme, Inc.', 'First line\r\nSecond "quoted" line']);
  });

  it('keeps the desktop Files filters when opening an older version', async () => {
    serve(() => 'plain words');
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/library/files?file=f1&kind=document&q=notes', <FilesPage search={{ file: 'f1', kind: 'document', q: 'notes' }} />);
    const link = await screen.findByRole('link', { name: /v1/ });
    expect(link.getAttribute('href')).toBe('/library/files?kind=document&q=notes&file=f1&v=1');
  });

  it('waits for the selected file version before previewing or offering a download', async () => {
    const ready = holdResponse();
    const requests: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      requests.push(url);
      if (url.endsWith('/versions')) return ready.response;
      return Response.json(url.endsWith('/versions/1') ? 'first words' : 'later words');
    });
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    at('/library/files?file=f1&v=1', <FilesPage search={{ file: 'f1', v: 1 }} />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByRole('link', { name: 'Download' })).toBeNull();
    expect(requests).not.toContain('/api/v2/files/f1/versions/2');

    ready.send(Response.json({ versions: [version(2, true), version(1)] }));
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(linkOf('Download')).toBe('/api/v2/files/f1/versions/1');
    expect(requests).not.toContain('/api/v2/files/f1/versions/2');
  });

  it('offers download and lets a phone open an older file version without clearing the filters', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => 'plain words');
      queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
      queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
      const router = at('/library/files?file=f1&kind=document', <FilesPage search={{ file: 'f1', kind: 'document' }} />);
      expect((await screen.findByRole('link', { name: 'Download' })).hasAttribute('data-phone')).toBe(true);
      fireEvent.click(screen.getByRole('button', { name: 'Versions' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: /^v1/ }));
      await waitFor(() => expect(router.state.location.search).toMatchObject({ file: 'f1', kind: 'document', v: 1 }));
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });
});

describe('Files', () => {
  it('searches a filtered list without fetching its unfiltered rows and lets a failed search retry', async () => {
    let searches = 0;
    const sent = serve((url) => {
      if (url === '/api/v2/files/search?q=notes&limit=50&kind=document') {
        if (++searches === 1) return new Response(null, { status: 500 });
        return { results: [{ id: 'f1', title: 'Sample notes' }] };
      }
      return {};
    });
    at('/library/files?kind=document&q=notes', <FilesPage search={{ kind: 'document', q: 'notes' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'Sample notes' })).toBeTruthy();
    expect(searches).toBe(2);
    expect(sent.some(({ url }) => url.startsWith('/api/v2/files?'))).toBe(false);
  });

  it('lists files newest first by day, each opening in the page, and filters by kind in the address @s779', async () => {
    serve();
    const today = new Date().toISOString();
    queryClient.setQueryData(filesQuery('').queryKey, {
      pages: [
        {
          files: [
            { ...file({ id: 'f1', title: 'Fresh plan' }), last_activity_at: today },
            { ...file({ id: 'f2', title: 'Old price list' }), last_activity_at: '2026-01-02T00:00:00Z' },
          ],
          has_more: false,
          next_cursor: null,
        },
      ],
      pageParams: [''],
    } as never);
    const router = at('/library/files', <FilesPage search={{}} />);
    expect(await screen.findByText('Today')).toBeTruthy();
    expect(screen.getByText('Earlier')).toBeTruthy();
    expect(screen.getByText('Fresh plan').closest('a')?.getAttribute('href')).toBe('/library/files?file=f1');
    fireEvent.click(screen.getByRole('button', { name: 'Filter by kind' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Images' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ kind: 'image' }));
  });

  it('takes files dropped on the page sheet itself, with no wrapper around the sheet', async () => {
    serve();
    queryClient.setQueryData(filesQuery('').queryKey, { pages: [{ files: [file()], has_more: false, next_cursor: null }], pageParams: [''] } as never);
    at('/library/files', <FilesPage search={{}} />);
    const page = await screen.findByRole('main', { name: 'Files' });
    expect(page.parentElement?.classList.contains('lib-drop')).toBe(false);
    fireEvent.drop(page, { dataTransfer: { files: [new File(['x'], 'a.txt')], types: ['Files'] } });
    expect(await screen.findByRole('dialog', { name: 'Add a.txt' })).toBeTruthy();
  });

  it('searches within the selected file kind', async () => {
    const sent = serve(() => ({ results: [{ id: 'f1', title: 'Seed photo' }] }));
    queryClient.setQueryData(filesQuery('image').queryKey, {
      pages: [{ files: [], has_more: false, next_cursor: null }],
      pageParams: [''],
    });
    at('/library/files?kind=image&q=seed', <FilesPage search={{ kind: 'image', q: 'seed' }} />);
    expect(await screen.findByText('Seed photo')).toBeTruthy();
    expect(sent).toContainEqual(expect.objectContaining({ url: '/api/v2/files/search?q=seed&limit=50&kind=image' }));
  });
});

const doc = { id: 'd1', title: 'Plan', locked: false, path: 'company/plan.md', updated_at: '2026-11-01T00:00:00Z', updated_by_id: '', updated_by_name: 'Sam', version: 3 };
const fullDoc = (o: Record<string, unknown> = {}) => ({ ...doc, body: '# Goals\n\nGrow slowly.\n\n## Next\n\nHire.', archived: false, created_at: '', created_by_id: '', created_by_name: '', ...o });

describe('a doc', () => {
  it('reads with its outline; a locked one is typed in only by an owner, who may unlock it, and Tico may only lock @s774', async () => {
    const sent = serve();
    queryClient.setQueryData(docQuery('d1').queryKey, fullDoc({ locked: true }));
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByText('Grow slowly.')).toBeTruthy();
    expect(within(screen.getByRole('navigation', { name: 'Outline' })).getByText('Next')).toBeTruthy();
    expect(screen.queryByLabelText('Text')).toBeNull();
    cleanup();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByLabelText('Text')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Unlock' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1', method: 'PATCH', body: { version: 3, locked: false } }]));
    expect(await run('docs.lock', { doc: 'd1', version: 3, locked: false }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await run('docs.lock', { doc: 'd1', version: 3, locked: true }, { via: 'tico' })).ok).toBe(true);
  });

  it('shows an old version from the head, and Restore brings it back @s816', async () => {
    const sent = serve(() => ({ doc: fullDoc({ version: 4 }) }));
    queryClient.setQueryData(docQuery('d1').queryKey, fullDoc());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, [
      { version: 3, current: true, actor: null, actor_name: 'Sam', created_at: '2026-11-01T00:00:00Z' },
      { version: 2, current: false, actor: null, actor_name: 'Sam', created_at: '2026-10-01T00:00:00Z' },
    ] as never);
    queryClient.setQueryData(docVersionQuery('d1', 2).queryKey, { version: 2, title: 'Plan', body: 'Older words', actor: null, actor_name: 'Sam', created_at: '2026-10-01T00:00:00Z' } as never);
    at('/library/d1?v=2', <DocPage doc="d1" search={{ v: 2 }} />);
    expect(await screen.findByText('Older words')).toBeTruthy();
    expect(screen.getByRole('button', { name: /v2/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/docs/d1/restore', method: 'POST', body: { version: 2 } }]));
  });

  it('never shows or edits the current doc when a historical version is loading or fails', async () => {
    const ready = holdResponse();
    vi.stubGlobal('fetch', async () => ready.response);
    queryClient.setQueryData(docQuery('d1').queryKey, fullDoc());
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1?v=2', <DocPage doc="d1" search={{ v: 2 }} />);
    await screen.findByRole('status', { name: 'Loading' });
    expect(screen.queryByLabelText('Text')).toBeNull();
    expect(screen.queryByText('Grow slowly.')).toBeNull();

    ready.send(Response.json({ error: { code: 'not_found' } }, { status: 404 }));
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.queryByLabelText('Text')).toBeNull();
    expect(screen.queryByText('Grow slowly.')).toBeNull();
  });

  it('a new doc is the same page, empty: naming it in the head makes it with what was typed, and opens it @s775', async () => {
    const sent = serve(() => ({ doc: { ...fullDoc(), id: 'd9', version: 1 } }));
    const router = at('/library/new', <DocPage doc="new" search={{}} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Text' }), { target: { value: 'Two roles.' } });
    fireEvent.click(screen.getByRole('heading', { name: 'Title' }));
    const title = await screen.findByRole('textbox', { name: 'Title' });
    fireEvent.change(title, { target: { value: 'Hiring plan' } });
    fireEvent.keyDown(title, { key: 'Enter' });
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/d9'));
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs', method: 'POST', body: { title: 'Hiring plan', body: 'Two roles.', path: null } }]);
  });
});

describe('Contacts', () => {
  const row = (o: Record<string, unknown>) => ({ type: 'person', status: 'active', primary_email: null, primary_domain: null, last_activity_at: null, ...o });

  it('Add makes a contact of the tab’s kind by its name and opens it beside the list', async () => {
    const sent = serve((url, method) => (method === 'POST' && url === '/api/v2/entities' ? { id: 'c9', created: true, changed: ['name'] } : { rows: [], next: null }));
    queryClient.setQueryData(typesQuery.queryKey, [{ key: 'person', name: 'Person', plural_name: 'People' }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'person', count: 1 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [row({ id: 'c1', name: 'Ada Sample' })], next: null }], pageParams: [''] } as never);
    const router = at('/library/contacts', <ContactsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add person' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Robin Sample' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/entities', method: 'POST', body: { type: 'person', match: { name: 'Robin Sample' }, name: 'Robin Sample' } }]));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ panel: 'c9' }));
  });

  it('keeps the relationship filter when searching contacts', async () => {
    serve(() => ({ rows: [], next: null, types: [], counts: [] }));
    queryClient.setQueryData(contactsQuery('person', '', 'customer_of').queryKey, {
      pages: [{ rows: [], next: null }],
      pageParams: [''],
    });
    const router = at('/library/contacts?link=customer_of', <ContactsPage search={{ link: 'customer_of' }} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Search contacts' }), { target: { value: 'seed' } });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: 'seed', link: 'customer_of' }));
  });

  it('retains the relationship filter when opening a list row', async () => {
    serve(() => ({ rows: [], next: null, types: [], counts: [] }));
    queryClient.setQueryData(contactsQuery('person', '', 'customer_of').queryKey, {
      pages: [{ rows: [row({ id: 'c1', name: 'Ada Sample' })], next: null }],
      pageParams: [''],
    } as never);
    at('/library/contacts?link=customer_of', <ContactsPage search={{ link: 'customer_of' }} />);
    const link = (await screen.findByText('Ada Sample')).closest('a');
    expect(link?.getAttribute('href')).toBe('/library/contacts?link=customer_of&panel=c1');

  });

  it('does not carry an unsent note to another contact', async () => {
    serve(() => ({ rows: [], next: null, types: [], counts: [] }));
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    queryClient.setQueryData(attributesQuery('person').queryKey, []);
    for (const id of ['c1', 'c2']) {
      queryClient.setQueryData(cardQuery(id).queryKey, {
        ...row({}),
        id,
        name: id === 'c1' ? 'Ada Sample' : 'Bo Sample',
        summary: null,
        values: {},
        links: [],
        notes: [],
      });
    }
    function CurrentContact() {
      return <ContactsPage search={contactsSearch(useSearch({ strict: false }))} />;
    }
    const router = at('/library/contacts?panel=c1', <CurrentContact />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Add a note' }), { target: { value: 'Unsaved note' } });
    await act(() => router.navigate({ to: '/library/contacts', search: { panel: 'c2' } }));
    const panel = await screen.findByRole('complementary', { name: 'Bo Sample' });
    expect((within(panel).getByRole('textbox', { name: 'Add a note' }) as HTMLInputElement).value).toBe('');
  });

  it('shows what each contact is to us, and filters by relationship @s785', async () => {
    const sent = serve(() => ({ rows: [], next: null }));
    queryClient.setQueryData(typesQuery.queryKey, [{ key: 'person', name: 'Person', plural_name: 'People' }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'person', count: 2 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, {
      pages: [
        {
          rows: [
            row({ id: 'c1', name: 'Ada Sample', links_to_us: ['customer_of'], sources: ['mailbox', 'meeting'] }),
            // no link of her own: her organization's
            row({ id: 'c2', name: 'Bo Sample', links_to_us: [], organization: { id: 'o1', name: 'Sample Seeds', links_to_us: ['vendor_to'] }, sources: ['finance_book'] }),
          ],
          next: null,
        },
      ],
      pageParams: [''],
    } as never);
    const router = at('/library/contacts', <ContactsPage search={{}} />);
    const ada = (await screen.findByText('Ada Sample')).closest('a') as HTMLElement;
    expect(ada.getAttribute('href')).toBe('/library/contacts?panel=c1');
    expect(within(ada).getByText('Customer')).toBeTruthy();
    const bo = screen.getByText('Bo Sample').closest('a') as HTMLElement;
    expect(within(bo).getByText('Vendor')).toBeTruthy();
    expect(within(bo).getByText('Sample Seeds')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Relationship' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Customer' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ link: 'customer_of' }));
    cleanup();
    at('/library/contacts?link=customer_of', <ContactsPage search={{ link: 'customer_of' }} />);
    expect(await screen.findByRole('button', { name: 'Clear Customer' })).toBeTruthy();
    await waitFor(() => expect(sent.some((x) => x.url.startsWith('/api/v2/entities/find/person?link=customer_of'))).toBe(true));
  });

  it('a contact beside the list takes a note for the company, or for the person alone @s786', async () => {
    const card: Card = {
      id: 'c1',
      type: 'person',
      name: 'Ada Sample',
      status: 'active',
      primary_email: null,
      primary_domain: null,
      last_activity_at: null,
      owner: 'human:me',
      summary: null,
      values: {},
      links: [],
      notes: [],
    };
    const sent = serve(() => ({ id: 'n1', rows: [], next: null }));
    queryClient.setQueryData(typesQuery.queryKey, [{ key: 'person', name: 'Person', plural_name: 'People' }]);
    queryClient.setQueryData(countsQuery.queryKey, [{ type: 'person', count: 1 }]);
    queryClient.setQueryData(contactsQuery('person', '').queryKey, { pages: [{ rows: [], next: null }], pageParams: [''] });
    queryClient.setQueryData(cardQuery('c1').queryKey, card);
    queryClient.setQueryData(attributesQuery('person').queryKey, []);
    at('/library/contacts?panel=c1', <ContactsPage search={{ panel: 'c1' }} />);
    const box = await screen.findByRole('textbox', { name: 'Add a note' });
    fireEvent.change(box, { target: { value: 'Call before noon' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add the note' }));
    fireEvent.change(box, { target: { value: 'On leave in May' } });
    fireEvent.click(screen.getByRole('button', { name: 'Only me' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add the note' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(2));
    expect(writes(sent).map((x) => x.body)).toEqual([
      { ref: 'c1', body: 'Call before noon', audience: 'company' },
      { ref: 'c1', body: 'On leave in May', audience: 'me' },
    ]);
  });
});
