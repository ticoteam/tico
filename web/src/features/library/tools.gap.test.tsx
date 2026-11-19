import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { newAddressFor } from '@/oldAddress/map';
import { apiToolQuery, inboxesQuery, mailboxQuery, toolsQuery, websiteToolQuery } from './queries';
import { mailboxMailQuery, mailboxSendsQuery, servicePageQuery, servicesQuery, teamsQuery, vaultQuery } from './toolKeys';
import { ToolsPage } from './ToolsPage';

/* The Tools gap rows: a stored key's own page, a declared tool, Slack's links, a mailbox, who reads an address, a
   website's accounts, who sees a tool, and Who has access. By what a person sees and presses, and what is sent. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: people, byId: Object.fromEntries(people.map((p) => [p.id, p])) });
  queryClient.setQueryData(teamsQuery.queryKey, [team]);
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
/** A read again answers what the page was given, so a write's read-back draws the same page. */
const READS: [string, () => readonly unknown[]][] = [
  ['/api/v2/tool-index', () => toolsQuery.queryKey],
  ['/api/v2/credentials', () => vaultQuery.queryKey],
  ['/api/v2/api-tools/t1', () => apiToolQuery('t1').queryKey],
  ['/api/v2/website-tools/w1', () => websiteToolQuery('w1').queryKey],
  ['/api/v2/mailboxes/m1', () => mailboxQuery('m1').queryKey],
];
/** Each write answers `reply` (or `{}`); each read what the cache holds for it, else `reply`, else `{}`. */
function serve(reply: (url: string, method: string) => unknown = () => undefined) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const u = String(url);
    sent.push({ url: u, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const key = method === 'GET' ? READS.find(([p]) => u === p || u.startsWith(`${p}?`) || u === `${p}/`)?.[1]() : undefined;
    const body = (key && queryClient.getQueryData(key)) ?? reply(u, method) ?? {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));

function at(path: string, node: ReactNode) {
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
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data));
const page = (name: string) => screen.findByRole('region', { name });

// Made-up teammates, keys and tools only.
const people = [
  { id: 'human:p1', kind: 'person', name: 'Pat Sample' },
  { id: 'human:p2', kind: 'person', name: 'Robin Sample' },
  { id: 'bot:helper', kind: 'bot', name: 'Helper' },
];
const team = { id: 'g1', name: 'Support', order: 0, parent: '', people: ['p2'], bots: [], bot_slugs: [] };
const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who: [], who_total: 0, detail: '', way: '', needs_you: false, status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
const storedKey = (o: Record<string, unknown> = {}) => ({
  id: 'c1',
  name: 'Sample token',
  username: '',
  kind: 'token',
  env: 'SAMPLE_TOKEN',
  source: 'manual',
  revision: 3,
  stored: true,
  preview: '…abcd',
  can_reveal: true,
  can_remove: true,
  tool_id: null,
  grants: [{ id: 'g1', subject: 'human:p1', can_revoke: true }],
  ...o,
});
const vault = (credentials: unknown[] = [storedKey()]) => ({
  credentials,
  can_manage: true,
  people: [
    { id: 'p1', name: 'Pat Sample' },
    { id: 'p2', name: 'Robin Sample' },
  ],
  bots: [],
});
/** Reads answer the vault, so it stays the same after a write reads it again. */
const vaultServe = () => serve();

/** Opens a menu (in `inside`, else anywhere) and picks from it. */
async function pick(menu: string, item: string, inside?: HTMLElement) {
  fireEvent.click(await (inside ? within(inside) : screen).findByRole('button', { name: menu }));
  fireEvent.click(await screen.findByRole('menuitem', { name: item }));
}

/* ---------------------------------------------------------------- a stored key no tool holds */

describe('a stored key', () => {
  it('has its own row and page; Rotate sends the value only, never kept, and is closed to Tico @s788', async () => {
    const FAKE = 'fake-rotated-0000';
    const sent = vaultServe();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [] } as never);
    queryClient.setQueryData(vaultQuery.queryKey, vault() as never);
    at('/library/tools?tool=key%3Ac1', <ToolsPage search={{ tool: 'key:c1' }} />);
    const key = await page('Sample token');
    expect(within(key).getByText('SAMPLE_TOKEN')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Sample token/ }).getAttribute('href')).toBe('/library/tools?tool=key%3Ac1');
    await pick('More', 'Rotate', key);
    const dialog = await screen.findByRole('dialog', { name: 'Rotate Sample token' });
    fireEvent.change(within(dialog).getByLabelText('New value'), { target: { value: FAKE } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Rotate' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        {
          url: '/api/v2/credentials/c1',
          method: 'POST',
          body: { name: 'Sample token', username: '', kind: 'token', env: 'SAMPLE_TOKEN', source: 'manual', secret: FAKE, expected_revision: 3 },
        },
      ]),
    );
    expect(cached()).not.toContain(FAKE);
    expect(await run('tools.rotateKey', { credential: 'c1', value: FAKE }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(newAddressFor('#/tools/key/k_1')).toBe('/library/tools?tool=key:k_1');
  });

  it('gives and takes access, and deletes for good; Tico may only take access away @s909', async () => {
    const sent = vaultServe();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [] } as never);
    queryClient.setQueryData(vaultQuery.queryKey, vault() as never);
    const router = at('/library/tools?tool=key%3Ac1', <ToolsPage search={{ tool: 'key:c1' }} />);
    const key = await page('Sample token');
    await pick('Add someone', 'Robin Sample');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Pat Sample' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        { url: '/api/v2/credentials/c1/grants', method: 'POST', body: { subject: 'human:p2' } },
        { url: '/api/v2/credentials/c1/grants/g1/revoke', method: 'POST', body: {} },
      ]),
    );
    expect(await run('tools.grantKey', { credential: 'c1', subject: 'human:p2' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.deleteKey', { credential: 'c1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.revokeKey', { credential: 'c1', grant: 'g1' }, { via: 'tico' })).toMatchObject({ ok: true });
    await pick('More', 'Delete key', key);
    const sure = await screen.findByRole('alertdialog', { name: 'Delete Sample token?' });
    fireEvent.click(within(sure).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/credentials/c1', method: 'DELETE', body: null }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

/* ---------------------------------------------------------------- a declared tool, and Slack */

describe('a declared tool', () => {
  it('shows its keys, who has it at what level, and its page @s787', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'declared:samplecrm', kind: 'declared', name: 'Sample CRM', service: 'samplecrm' })] } as never);
    queryClient.setQueryData(servicesQuery.queryKey, [
      {
        service: 'samplecrm',
        title: 'Sample CRM',
        kind: 'api',
        summary: 'Customer records',
        credentials: ['SAMPLECRM_API_KEY', 'SAMPLECRM_SECRET'],
        writes: 'approval',
        bots: ['helper'],
        connections: [{ bot: 'helper', status: 'ready', write: 'allowed', env: 'SAMPLECRM_API_KEY' }],
      },
    ]);
    queryClient.setQueryData(servicePageQuery('samplecrm').queryKey, { body: 'Finds a customer by email.' });
    queryClient.setQueryData(vaultQuery.queryKey, vault([storedKey({ id: 'c2', name: 'Sample CRM key', env: 'SAMPLECRM_API_KEY', tool_id: 'samplecrm' })]) as never);
    at('/library/tools?tool=declared%3Asamplecrm', <ToolsPage search={{ tool: 'declared:samplecrm' }} />);
    const tool = await page('Sample CRM');
    expect(within(tool).getByRole('link', { name: /Sample CRM key/ }).getAttribute('href')).toBe('/library/tools?tool=key%3Ac2');
    expect(within(tool).getByText('SAMPLECRM_SECRET')).toBeTruthy();
    expect(within(tool).getByRole('button', { name: 'Add SAMPLECRM_SECRET' })).toBeTruthy();
    expect(within(tool).getByText('Helper')).toBeTruthy();
    expect(within(tool).getByText('Read and write')).toBeTruthy();
    expect(within(tool).getByText('Finds a customer by email.')).toBeTruthy();
    expect(await run('tools.storeKey', { name: 'Sample CRM', env: 'SAMPLECRM_SECRET', source: 'samplecrm', kind: 'api_key', value: 'x' }, { via: 'tico' })).toMatchObject({
      ok: false,
      error: 'not_allowed',
    });
  });
});

/* ---------------------------------------------------------------- a mailbox */

const mailbox = (o: Record<string, unknown> = {}) => ({
  mailbox: {
    id: 'm1',
    address: 'desk@example.com',
    connection: 'personal',
    kind: 'personal',
    level: 'read_write',
    person_id: null,
    person_name: 'Pat Sample',
    provider: 'Gmail',
    state: 'connected',
    status: 'active',
    sync: '',
    sync_error: '',
    synced_at: null,
    newest_epoch: null,
    routines: [],
    used_by: { bots: 1, people: 0, names: [] },
  },
  accesses: [{ id: 'a1', grantee_id: 'helper', grantee_type: 'bot', implicit: false, level: 'read_write', name: 'Helper' }],
  candidates: [{ id: 'p2', name: 'Robin Sample', type: 'person' }],
  can_manage: true,
  can_stop: true,
  may: {},
  routines: [],
  ...o,
});

describe('a mailbox', () => {
  it('lowers and removes access, checks now, shows its mail and activity; Tico may only lower @s866', async () => {
    const sent = serve((url) => (url.startsWith('/api/v2/mailboxes/m1') ? mailbox() : url.startsWith('/api/v2/email/sends') ? { sends: [], next_cursor: null } : undefined));
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'mailbox:m1', kind: 'mailbox', name: 'desk@example.com', mailbox_id: 'm1' })] } as never);
    queryClient.setQueryData(mailboxQuery('m1').queryKey, mailbox() as never);
    queryClient.setQueryData(mailboxMailQuery('desk@example.com').queryKey, [{ id: 'x1', thread_id: 't1', subject: 'Sample order', from_addr: 'buyer@example.com', epoch: 1_790_000_000 }]);
    queryClient.setQueryData(mailboxSendsQuery('desk@example.com').queryKey, {
      pages: [{ next_cursor: null, sends: [{ id: 's1', subject: 'Sample reply', recipients: ['buyer@example.com'], state: 'waiting_approval', bot: 'helper', actor: '', created_at: '2026-11-18T00:00:00Z' }] }],
      pageParams: [''],
    } as never);
    at('/library/tools?tool=mailbox%3Am1', <ToolsPage search={{ tool: 'mailbox:m1' }} />);
    const box = await page('desk@example.com');
    expect(within(box).getByText('Sample order')).toBeTruthy();
    expect(within(box).getByText('Waiting for OK')).toBeTruthy();
    await pick('Helper: Read and write', 'Read');
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/mailboxes/m1/accesses/a1', method: 'PATCH', body: { level: 'read' } }]));
    fireEvent.click(within(box).getByRole('button', { name: 'Check now' }));
    expect(await screen.findByText('Connected')).toBeTruthy();
    expect(await run('tools.setMailboxAccess', { mailbox: 'm1', access: 'a1', level: 'none' }, { via: 'tico' })).toMatchObject({ ok: true });
    queryClient.setQueryData(mailboxQuery('m1').queryKey, mailbox({ accesses: [{ id: 'a1', grantee_id: 'helper', grantee_type: 'bot', implicit: false, level: 'read', name: 'Helper' }] }) as never);
    expect(await run('tools.setMailboxAccess', { mailbox: 'm1', access: 'a1', level: 'read_write' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.addMailboxAccess', { mailbox: 'm1', type: 'person', who: 'p2', level: 'read' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.shareMailbox', { mailbox: 'm1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('makes a shared inbox and stops using it, after asking @s866', async () => {
    const sent = serve((url) => (url.startsWith('/api/v2/mailboxes/m1') ? mailbox() : url.startsWith('/api/v2/email/sends') ? { sends: [], next_cursor: null } : undefined));
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'mailbox:m1', kind: 'mailbox', name: 'desk@example.com', mailbox_id: 'm1' })] } as never);
    queryClient.setQueryData(mailboxQuery('m1').queryKey, mailbox() as never);
    const router = at('/library/tools?tool=mailbox%3Am1', <ToolsPage search={{ tool: 'mailbox:m1' }} />);
    const box = await page('desk@example.com');
    await pick('More', 'Make shared inbox', box);
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/mailboxes/m1/share', method: 'POST', body: {} }]));
    await pick('More', 'Stop using', box);
    const sure = await screen.findByRole('alertdialog', { name: 'Stop using desk@example.com?' });
    fireEvent.click(within(sure).getByRole('button', { name: 'Stop using' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/mailboxes/m1', method: 'DELETE', body: null }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

/* ---------------------------------------------------------------- a Tico address */

describe('a Tico address', () => {
  it('chooses who reads it and checks the connection; Tico may only narrow it @s867', async () => {
    const box = { inbox_id: 'i1', kind: 'email', name: 'Front desk', address: 'desk@example.test', state: 'ready', audience: 'company' };
    const sent = serve((url) => (url === '/api/v2/inbound/inboxes' ? { configured: true, error: null, inboxes: [box] } : undefined));
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'tico:i1', kind: 'mailbox', name: 'Front desk' })] } as never);
    queryClient.setQueryData(inboxesQuery.queryKey, [box]);
    at('/library/tools?tool=tico%3Ai1', <ToolsPage search={{ tool: 'tico:i1' }} />);
    await page('Front desk');
    await pick('Reads it: Everyone', 'Support');
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes/i1/audience', method: 'PUT', body: { audience: 'team', group_id: 'g1' } }]));
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByText('Connected')).toBeTruthy();
    expect(await run('tools.setInboxReaders', { inbox: 'i1', audience: 'team', group: 'g1' }, { via: 'tico' })).toMatchObject({ ok: true });
    queryClient.setQueryData(inboxesQuery.queryKey, [{ ...box, audience: 'team' }]);
    expect(await run('tools.setInboxReaders', { inbox: 'i1', audience: 'company' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.checkInbox', {}, { via: 'tico' })).toMatchObject({ ok: true });
  });
});

/* ---------------------------------------------------------------- a website */

const website = (o: Record<string, unknown> = {}) => ({
  tool: { id: 'w1', name: 'Sample site', sites: ['app.example.com'], kind: 'website' },
  accounts: [
    {
      id: 'a1',
      username: 'ops@example.com',
      kind: 'shared',
      has_password: true,
      has_totp: false,
      can_open: false,
      can_use: true,
      can_manage: true,
      can_remove: true,
      all_bots: false,
      holder: null,
      access: [{ id: 'p1', subject: 'human:p1', name: 'Pat Sample', kind: 'person', initials: 'PS' }],
    },
  ],
  recent: [],
  status: { tone: 'ok', label: 'Working' },
  can_manage: true,
  can_set_audience: false,
  private: false,
  host: null,
  ...o,
});

describe('a website', () => {
  it('gives and takes an account, removes it and deletes the website @s865', async () => {
    const sent = serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'website:w1', kind: 'website', name: 'Sample site' })] } as never);
    queryClient.setQueryData(websiteToolQuery('w1').queryKey, website() as never);
    const router = at('/library/tools?tool=website%3Aw1', <ToolsPage search={{ tool: 'website:w1' }} />);
    const site = await page('Sample site');
    await pick('Add someone', 'Support');
    fireEvent.click(screen.getByRole('button', { name: 'Remove Pat Sample' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        { url: '/api/v2/website-tools/w1/accounts/a1/access/group%3Ag1', method: 'PUT', body: { on: true } },
        { url: '/api/v2/website-tools/w1/accounts/a1/access/human%3Ap1', method: 'PUT', body: { on: false } },
      ]),
    );
    expect(await run('tools.setAccountAccess', { tool: 'w1', account: 'a1', subject: 'bots', on: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setAccountAccess', { tool: 'w1', account: 'a1', subject: 'human:p1', on: false }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('tools.removeAccount', { tool: 'w1', account: 'a1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.deleteWebsite', { tool: 'w1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    await pick('ops@example.com options', 'Remove account');
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Remove ops@example.com?' })).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/website-tools/w1/accounts/a1', method: 'DELETE', body: null }));
    await pick('More', 'Delete website', site);
    fireEvent.click(within(await screen.findByRole('alertdialog', { name: 'Delete Sample site?' })).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/website-tools/w1', method: 'DELETE', body: null }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

/* ---------------------------------------------------------------- Who has access */

describe('Tools by who has access', () => {
  it('keeps the tools and keys a team or a teammate has, in the address @s908', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, {
      rows: [
        indexRow({ id: 'api:t1', name: 'Team API', who_total: 1, who: [{ id: 'g1', kind: 'group', name: 'Support', initials: 'S' }] }),
        indexRow({ id: 'api:t2', name: 'Other API', who_total: 1, who: [{ id: 'human:p1', kind: 'person', name: 'Pat Sample', initials: 'PS' }] }),
      ],
    } as never);
    queryClient.setQueryData(vaultQuery.queryKey, vault() as never);
    const router = at('/library/tools', <ToolsPage search={{}} />);
    expect(await screen.findByText('Team API')).toBeTruthy();
    expect(await run('tools.filterWho', { who: 'group:g1' }, { via: 'click' })).toMatchObject({ ok: true });
    expect(router.state.location.search).toEqual({ who: 'group:g1' });
    cleanup();
    at('/library/tools?who=group%3Ag1', <ToolsPage search={{ who: 'group:g1' }} />);
    expect(await screen.findByText('Team API')).toBeTruthy();
    expect(screen.queryByText('Other API')).toBeNull();
    expect(screen.queryByText('Sample token')).toBeNull();
    cleanup();
    // Pat has Other API and the key; Robin has Team API through Support.
    at('/library/tools?who=human%3Ap1', <ToolsPage search={{ who: 'human:p1' }} />);
    expect(await screen.findByText('Other API')).toBeTruthy();
    expect(screen.getByText('Sample token')).toBeTruthy();
    expect(screen.queryByText('Team API')).toBeNull();
    expect(screen.getByRole('button', { name: 'Clear Pat Sample' })).toBeTruthy();
  });
});
