import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import { AddDialog } from './AddTool';
import { ContactsPage } from './ContactsPage';
import { DocPage } from './DocPage';
import { FilePreview, FileViewerHost } from './FileViewer';
import { FilesPage } from './FilesPage';
import {
  apiToolQuery,
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
  toolsQuery,
  typesQuery,
  vendorQuery,
  websiteToolQuery,
  type Card,
} from './queries';
import { ToolsPage } from './ToolsPage';
import { viewing } from './viewing';

/* One test per Library page the old app had, by what a person sees and presses there, and what is sent. */

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
    cleanup();
    at('/tasks', <FileViewerHost />);
    act(() => viewing.set({ file: 'f1' }));
    const dialog = await screen.findByRole('dialog', { name: 'Sample notes' });
    expect(within(dialog).getByRole('link', { name: 'Open in Google' }).getAttribute('href')).toBe(url);
  });

  it('draws the version asked for, as a task shows it @s76', async () => {
    const sent = serve((url) => (url.endsWith('/versions/1') ? 'first words' : 'later words'));
    queryClient.setQueryData(fileQuery('f1').queryKey, file() as never);
    queryClient.setQueryData(fileVersionsQuery('f1').queryKey, [version(2, true), version(1)]);
    at('/tasks/7', <FilePreview file="f1" version={1} />);
    expect(await screen.findByText('first words')).toBeTruthy();
    expect(sent.map((x) => x.url)).toEqual(['/api/v2/files/f1/versions/1']);
  });
});

describe('Files', () => {
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
});

const doc = { id: 'd1', title: 'Plan', locked: false, path: 'company/plan.md', updated_at: '2026-11-01T00:00:00Z', updated_by_id: '', updated_by_name: 'Sam', version: 3 };
const fullDoc = (o: Record<string, unknown> = {}) => ({ ...doc, body: '# Goals\n\nGrow slowly.\n\n## Next\n\nHire.', archived: false, created_at: '', created_by_id: '', created_by_name: '', ...o });

describe('a doc', () => {
  it('reads with its outline and Edit; a locked one changes only by an owner, who may unlock it, and Tico may only lock @s774', async () => {
    const sent = serve();
    queryClient.setQueryData(docQuery('d1').queryKey, fullDoc({ locked: true }));
    queryClient.setQueryData(docVersionsQuery('d1').queryKey, []);
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByText('Grow slowly.')).toBeTruthy();
    expect(within(screen.getByRole('navigation', { name: 'Outline' })).getByText('Next')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    cleanup();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    at('/library/d1', <DocPage doc="d1" search={{}} />);
    expect(await screen.findByRole('button', { name: 'Edit' })).toBeTruthy();
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

  it('a new doc takes a title, a folder and its text, and opens once made @s775', async () => {
    const sent = serve(() => ({ doc: { ...fullDoc(), id: 'd9', version: 1 } }));
    const router = at('/library/new', <DocPage doc="new" search={{}} />);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Title' }), { target: { value: 'Hiring plan' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Folder' }), { target: { value: 'Company' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Text' }), { target: { value: 'Two roles.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make doc' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/library/d9'));
    expect(writes(sent)).toEqual([{ url: '/api/v2/docs', method: 'POST', body: { title: 'Hiring plan', body: 'Two roles.', path: 'Company/hiring-plan.md' } }]);
  });
});

describe('Contacts', () => {
  const row = (o: Record<string, unknown>) => ({ type: 'person', status: 'active', primary_email: null, primary_domain: null, last_activity_at: null, ...o });

  it('shows what each contact is to us and where it is known from, and filters by relationship @s785', async () => {
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
    const ada = (await screen.findByText('Ada Sample')).closest('tr') as HTMLElement;
    expect(screen.getByRole('columnheader', { name: 'Relationship' })).toBeTruthy();
    expect(within(ada).getByText('Customer')).toBeTruthy();
    expect(within(ada).getByText('Mail · Meetings')).toBeTruthy();
    const bo = screen.getByText('Bo Sample').closest('tr') as HTMLElement;
    expect(within(bo).getByText('Vendor')).toBeTruthy();
    expect(within(bo).getByText('Sample Seeds')).toBeTruthy();
    expect(within(bo).getByText('Finance')).toBeTruthy();
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

/* ---------------------------------------------------------------- tools */

const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who_total: 0, needs_you: false, status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
const KEY = { id: 'k1', name: 'Main key', preview: 'sk-…abcd', access_level: 'read_write', can_remove: true, expires_on: null, expires_source: null };
const apiTool = (o: Record<string, unknown> = {}) => ({
  tool: { id: 't1', name: 'Sample API', base_url: 'https://api.example.com', auth_scheme: 'bearer', delivery: 'proxy', check_path: '/v1/me' },
  keys: [KEY],
  access: [{ subject: 'human:p1', name: 'Pat Sample', kind: 'person', level: 'read', initials: 'PS', last_used_at: null }],
  checks: [],
  recent: [{ id: 'r1', method: 'GET', path: '/v1/items', status_code: 200, result: 'sent', at: '2026-11-18T00:00:00Z' }],
  settings: [],
  status: { tone: 'ok', text: 'Working' },
  can_manage: true,
  can_set_audience: false,
  mcp: null,
  ...o,
});
const people = [
  { id: 'human:p1', kind: 'person', name: 'Pat Sample' },
  { id: 'human:p2', kind: 'person', name: 'Robin Sample' },
];

describe('Tools', () => {
  it('lists the tools in use and the ones available, each opening beside the list @s862', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, {
      rows: [indexRow({ id: 'api:t1', name: 'Sample API', who_total: 2 }), indexRow({ id: 'website:w1', kind: 'website', name: 'Sample site' })],
    } as never);
    at('/library/tools', <ToolsPage search={{}} />);
    expect(await screen.findByText('In use')).toBeTruthy();
    expect(screen.getByText('Available')).toBeTruthy();
    expect(screen.getByText('Sample API').closest('a')?.getAttribute('href')).toBe('/library/tools?tool=api%3At1');
  });

  it('a vendor with several tools opens to its tools @s863', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [] } as never);
    queryClient.setQueryData(vendorQuery('acme').queryKey, {
      vendor: 'acme',
      name: 'Acme',
      rows: [indexRow({ id: 'api:t1', name: 'Acme mail' }), indexRow({ id: 'website:w1', kind: 'website', name: 'Acme files' })],
    } as never);
    at('/library/tools?tool=vendor%3Aacme', <ToolsPage search={{ tool: 'vendor:acme' }} />);
    const tools = await screen.findByRole('region', { name: 'Acme' });
    expect(within(tools).getByText('Acme mail').closest('a')?.getAttribute('href')).toContain('api%3At1');
    expect(within(tools).getByText('Acme files')).toBeTruthy();
  });

  it('an API tool shows what it is, its key and its recent calls @s864 @s883', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    at('/library/tools?tool=api%3At1', <ToolsPage search={{ tool: 'api:t1' }} />);
    expect(await screen.findByText('api.example.com')).toBeTruthy();
    expect(screen.getByText('Main key')).toBeTruthy();
    expect(screen.getByText('GET /v1/items → 200')).toBeTruthy();
  });

  it('replaces a key: the value is only sent, never kept @s914 @s840', async () => {
    const FAKE = 'sk-new-fake-0000';
    const sent = serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    at('/library/tools?tool=api%3At1', <ToolsPage search={{ tool: 'api:t1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Replace key' }));
    const dialog = await screen.findByRole('dialog', { name: 'Replace the Sample API key' });
    fireEvent.change(within(dialog).getByLabelText('Key'), { target: { value: FAKE } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/keys/k1', method: 'PATCH', body: { value: FAKE } }]));
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(FAKE);
    expect(await run('tools.setKey', { tool: 't1', key: 'k1', value: FAKE }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('adds someone who may use it at Read; Tico may not widen it @s926', async () => {
    const sent = serve();
    queryClient.setQueryData(['directory'], { actors: people, byId: Object.fromEntries(people.map((p) => [p.id, p])) });
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    at('/library/tools?tool=api%3At1', <ToolsPage search={{ tool: 'api:t1' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Add someone' }));
    expect(screen.queryByRole('menuitem', { name: 'Pat Sample' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Robin Sample' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/access/human%3Ap2', method: 'PUT', body: { level: 'read' } }]));
    expect(await run('tools.setAccess', { tool: 't1', subject: 'human:p3', level: 'read' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('a website shows its site and logins, and adds an account @s865 @s924', async () => {
    const sent = serve(() => ({ id: 'a2' }));
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'website:w1', kind: 'website', name: 'Sample site' })] } as never);
    queryClient.setQueryData(websiteToolQuery('w1').queryKey, {
      tool: { id: 'w1', name: 'Sample site', sites: ['app.example.com'] },
      accounts: [
        { id: 'a1', username: 'ops@example.com', kind: 'shared', has_password: true, has_totp: false, can_open: false, can_use: true, holder: null, access: [{ subject: 'human:p1', name: 'Pat Sample' }] },
      ],
      recent: [],
      status: { tone: 'ok', label: 'Working' },
      can_manage: true,
      host: null,
    } as never);
    at('/library/tools?tool=website%3Aw1', <ToolsPage search={{ tool: 'website:w1' }} />);
    expect(await screen.findByText('app.example.com')).toBeTruthy();
    expect(screen.getByText('ops@example.com', { exact: false })).toBeTruthy();
    expect(screen.getByText('Pat Sample')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Add account' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add account' });
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'me@example.com' } });
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: 'fake-pass-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/website-tools/w1/accounts', method: 'POST', body: { username: 'me@example.com', password: 'fake-pass-1', holder: 'human:me' } }]),
    );
  });
});

describe('Add tool', () => {
  it('adds an API by its address and where the key goes @s911', async () => {
    const sent = serve(() => ({ id: 't9' }));
    const router = at('/library/tools', <AddDialog adding="api" onClose={() => {}} />);
    const dialog = await screen.findByRole('dialog', { name: 'Add API' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Sample CRM' } });
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: 'https://api.example.com' } });
    fireEvent.change(within(dialog).getByLabelText('Check path'), { target: { value: '/v1/me' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tool: 'api:t9' }));
    expect(writes(sent)[0]).toEqual({
      url: '/api/v2/api-tools',
      method: 'POST',
      body: { name: 'Sample CRM', base_url: 'https://api.example.com', auth_scheme: 'bearer', check_path: '/v1/me' },
    });
  });

  it('adds an MCP server: its address is read first, then the token is sent @s910', async () => {
    const sent = serve((url) => (url.endsWith('/probe') ? { oauth: false, dcr: false, server: 'Sample' } : { id: 't8' }));
    const router = at('/library/tools', <AddDialog adding="mcp" onClose={() => {}} />);
    const dialog = await screen.findByRole('dialog', { name: 'Add MCP server' });
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'https://mcp.example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    fireEvent.change(await within(dialog).findByLabelText('Token'), { target: { value: 'fake-token-1' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tool: 'api:t8' }));
    expect(writes(sent)).toEqual([
      { url: '/api/v2/mcp-tools/probe', method: 'POST', body: { url: 'https://mcp.example.com' } },
      { url: '/api/v2/mcp-tools', method: 'POST', body: { name: 'Sample', url: 'https://mcp.example.com', auth: 'token', token: 'fake-token-1' } },
    ]);
  });

  it('adds a website with a login, which is the person’s own press @s919', async () => {
    const sent = serve((url) => (url.endsWith('/accounts') ? { id: 'a1' } : { id: 'w9' }));
    const router = at('/library/tools', <AddDialog adding="website" onClose={() => {}} />);
    const dialog = await screen.findByRole('dialog', { name: 'Add website' });
    fireEvent.change(within(dialog).getByLabelText('Site'), { target: { value: 'app.example.com' } });
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'me@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(writes(sent)).toHaveLength(2));
    expect(router.state.location.search).toMatchObject({ tool: 'website:w9' });
    expect(writes(sent)).toEqual([
      { url: '/api/v2/website-tools', method: 'POST', body: { sites: ['app.example.com'] } },
      { url: '/api/v2/website-tools/w9/accounts', method: 'POST', body: { username: 'me@example.com', holder: 'human:me' } },
    ]);
    expect(await run('tools.addWebsiteWithLogin', { site: 'b.example.com', password: 'x' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});
