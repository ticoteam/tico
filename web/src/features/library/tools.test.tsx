import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import type { ToolIndexRow } from '@/api/schema';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { newAddressFor } from '@/oldAddress/map';
import { sendsMore } from './actions';
import { AddDialog, nameFromUrl, ReplaceLoginDialog } from './AddTool';
import {
  addressGrantsQuery,
  agentSendingQuery,
  apiToolQuery,
  googleQuery,
  inboxesQuery,
  inboxMessagesQuery,
  intakeQuery,
  legalMailQuery,
  lettersInQuery,
  lettersQuery,
  lettersSettingsQuery,
  mailboxMailQuery,
  mailboxQuery,
  mailboxSendsQuery,
  editApiTool,
  numberRequestsQuery,
  postalAddressesQuery,
  removeKey,
  replaceLogin,
  saveSetting,
  setKeyExpiry,
  sendingQuery,
  servicePageQuery,
  servicesQuery,
  teamsQuery,
  toolsQuery,
  vaultQuery,
  websiteToolQuery,
  type GoogleState,
  type SendPolicy,
} from './queries';
import { loadDocs, loadTools } from './routing';
import { toolsSearch } from './search';
import { NumberRequests, SharedInboxDialog } from './ToolSettings';
import { flowQuery, type FlowRequest } from '@/features/tasks/exports';
import { narrow, ToolsPage } from './ToolsPage';

/* Tools: the list, and the one page every kind of tool opens on (what it is, how it connects, who may use it, what it
   can do, recent use), each kind's changes, and the company's mail settings. By what a person sees and presses, what
   is sent, and what Tico may not run. Made-up tools, teammates, addresses and letters only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/features/team/exports', () => ({ BrowserDialog: () => null }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [] } as never);
});
afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.layout;
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
/** Each call answers `reply` (a body, or a whole Response for a refusal); a read it leaves out answers what the cache
    holds for it, else `{}`. */
function serve(reply: (url: string, method: string) => unknown = () => undefined) {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    const u = String(url);
    sent.push({ url: u, method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(u, method);
    if (r instanceof Response) return r.clone();
    const key = r === undefined && method === 'GET' ? READS.find(([p]) => u === p || u.startsWith(`${p}?`))?.[1]() : undefined;
    const body = r ?? (key && queryClient.getQueryData(key)) ?? {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const ok = (body: unknown) => body;
const refused = (status: number, code: string) =>
  new Response(JSON.stringify({ error: { code, detail: 'Made-up refusal' } }), { status, headers: { 'Content-Type': 'application/json' } });
const writes = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => ({ url, method, body }));
/** Writes as [method, url, body], a delete without its body. */
const list = (sent: Sent[]) => sent.filter((x) => x.method !== 'GET').map(({ url, method, body }) => (method === 'DELETE' ? [method, url] : [method, url, body]));
const reads = (sent: Sent[]) => () => sent.filter((x) => x.method === 'GET').map((x) => x.url);
const cached = () => JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data));
const tico = (id: string, input: unknown) => run(id, input, { via: 'tico' });

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
/** Tools with one tool open (`''`: none). */
const open = (tool: string) => at(tool ? `/library/tools?tool=${encodeURIComponent(tool)}` : '/library/tools', <ToolsPage search={tool ? { tool } : {}} />);
const page = (name: string) => screen.findByRole('region', { name });
const indexRow = (o: Record<string, unknown>) =>
  ({ kind: 'api', vendor: null, vendor_name: null, who: [], who_total: 0, detail: '', way: '', needs_you: false, status: { tone: 'ok', text: 'Working' }, ...o }) as unknown as ToolIndexRow;
const tools = (rows: ToolIndexRow[] = []) => queryClient.setQueryData(toolsQuery.queryKey, { rows } as never);
/** Opens a menu (in `inside`, else anywhere) and picks from it. */
async function pick(menu: string, item: string, inside?: HTMLElement) {
  fireEvent.click(await (inside ? within(inside) : screen).findByRole('button', { name: menu }));
  fireEvent.click(await screen.findByRole('menuitem', { name: item }));
}
/** Add tool › Find or connect › one of the kinds. */
async function addKind(kind: string) {
  fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
  const kinds = await screen.findByRole('group', { name: 'Add by kind' });
  fireEvent.click(within(kinds).getByRole('button', { name: kind }));
}
/** The open tool's own ⋯ (the list's head has one too). */
const toolMore = async (name: string) => fireEvent.click(await within(await page(name)).findByRole('button', { name: 'More' }));

// Made-up teammates, keys and tools only.
const people = [
  { id: 'human:p1', kind: 'person', name: 'Pat Sample' },
  { id: 'human:p2', kind: 'person', name: 'Robin Sample' },
  { id: 'bot:helper', kind: 'bot', name: 'Helper' },
];
const team = { id: 'g1', name: 'Support', order: 0, parent: '', people: ['p2'], bots: [], bot_slugs: [] };
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


const ME = { id: 'human:me', email: '', slug: null };
const as = (role: 'owner' | 'person') => queryClient.setQueryData(['me'], { ...ME, role });
const ACTORS = [
  { id: 'bot:b1', kind: 'agent', name: 'Clerk' },
  { id: 'human:p2', kind: 'person', name: 'Pat Sample' },
];

const may = { organize_mail: false, send_mail: true, change_calendar: false };
const google = (o: Partial<GoogleState> = {}): GoogleState => ({ can_admin: true, workspace: null, pending: null, me: null, permissions: may, ...o });
const policy: SendPolicy = {
  mode: 'ask_first',
  what: 'replies_only',
  to: 'known_addresses',
  daily_cap: 20,
  mailbox_daily_cap: 50,
  company_daily_cap: 200,
  undo_seconds: 30,
  max_outside_recipients: 1,
  blocked_recipients: ['@rival.test'],
  ask_new_address: true,
  ask_legal_money: true,
  ask_attachments: false,
  ask_many_recipients: true,
  ask_outside_cc: true,
};
const letters = { mode: 'ask_first', monthly_cap_micros: 50_000_000, return_address: null, availability: 'ready' };
const KEY = { id: 'k1', name: 'Main key', preview: 'sk-…abcd', access_level: 'read_write', can_remove: true, expires_on: '2026-12-31', expires_source: null };
const apiTool = (o: Record<string, unknown> = {}) => ({
  tool: { id: 't1', name: 'Sample API', service: 'sample', base_url: 'https://api.example.com', auth_scheme: 'bearer', delivery: 'proxy', check_path: '/v1/me' },
  keys: [KEY, { ...KEY, id: 'k2', name: 'Vendor key', expires_source: 'vendor' }],
  access: [{ subject: 'human:p1', name: 'Pat Sample', kind: 'person', level: 'read', initials: 'PS', last_used_at: null }],
  checks: [],
  recent: [{ id: 'r1', method: 'GET', path: '/v1/items', status_code: 200, result: 'sent', at: '2026-11-18T00:00:00Z' }],
  settings: [{ id: 'set1', name: 'REGION', value: 'us', updated_at: '2026-11-01T00:00:00Z' }],
  status: { tone: 'ok', text: 'Working' },
  can_manage: true,
  can_set_audience: false,
  mcp: null,
  ...o,
});
const openApi = (o: Record<string, unknown> = {}) => {
  tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
  queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool(o) as never);
  open('api:t1');
};

/** The head's controls a phone draws besides its ⋯: in the actions only those marked `data-phone` (pagekinds.css hides
    the rest). */
const controls = (head: HTMLElement) =>
  [...head.querySelectorAll<HTMLElement>('a, button')]
    .filter((el) => el.getAttribute('aria-label') !== 'More')
    .filter((el) => {
      const kept = [...head.querySelectorAll('.pk-acts > *')].find((c) => c.contains(el));
      return !kept || kept.hasAttribute('data-phone');
    })
    .map((el) => el.getAttribute('aria-label') ?? el.textContent);

/* ---------------------------------------------------------------- a website */

const website = (o: Record<string, unknown> = {}) => ({
  tool: { id: 'w1', name: 'Sample site', sites: ['app.example.com'], kind: 'website' },
  accounts: [
    {
      id: 'a1',
      username: 'ops@example.com',
      kind: 'shared',
      has_password: true,
      has_totp: true,
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

describe('kinds of tool', () => {
  beforeEach(() => {
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(['directory'], { actors: people, byId: Object.fromEntries(people.map((p) => [p.id, p])) });
    queryClient.setQueryData(teamsQuery.queryKey, [team]);
  });

/* ---------------------------------------------------------------- a stored key no tool holds */

describe('a stored key', () => {
  it('has its own row and page; Rotate sends the value only, never kept, and is closed to Tico @s788', async () => {
    const FAKE = 'fake-rotated-0000';
    const sent = serve();
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
    const sent = serve();
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
});

/* ---------------------------------------------------------------- an outgoing webhook */

const HOOK = {
  id: 'wh1',
  name: 'Order feed',
  url: 'https://hooks.example.test/orders',
  host: 'hooks.example.test',
  events: ['task.created'],
  enabled: true,
  paused_reason: '',
  disabled_reason: null,
  can_change: true,
  revision: 4,
  last_7_days: { delivered: 9, failed: 1, pending: 0 },
};
const hookTypes = { items: [{ name: 'task.created', label: 'Task created' }, { name: 'task.done', label: 'Task done' }] };
const hookServer = (url: string) =>
  url === '/api/v2/event-types'
    ? hookTypes
    : url === '/api/v2/webhooks/wh1'
    ? { webhook: HOOK }
    : url.startsWith('/api/v2/webhooks/wh1/sends')
      ? { items: [{ id: 'x1', type: 'task.created', subject: 'New task', state: 'failed', last_status: 500, last_error: 'Server error', created_at: '2026-11-18T00:00:00Z', delivered_at: null }] }
      : url === '/api/v2/webhooks/wh1/rotate'
        ? { webhook: { ...HOOK, secret: 'whsec_fake_0000', replayed: false } }
        : url === '/api/v2/webhooks/wh1/recover'
          ? { webhook: 'wh1', since: 'x', recovered: 2, more: false }
          : url === '/api/v2/webhooks/wh1/replay'
            ? { webhook: 'wh1', since: 'x', queued: 3, checked: 3, more: false }
            : undefined;
const openHook = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'webhook:wh1', kind: 'webhook', name: 'Order feed' })] } as never);
  return open('webhook:wh1');
};

/* ---------------------------------------------------------------- a mailing address */

const ACTIVE = { assignment_id: 'as1', address: '1 Fern Way, Dover DE', status: 'active', state: 'DE', kind: 'shared', agent: true };
const PENDING = { assignment_id: 'as2', address: null, status: 'pending_1583', state: 'TX', kind: 'private' };
const LETTER_IN = {
  id: 'm1',
  provider: 'gateway',
  address_id: 'as1',
  sender: 'County office',
  summary: 'Tax notice',
  kind: 'government_tax',
  status: 'failed',
  received_at: '2026-11-17T00:00:00Z',
  file_url: '/api/v2/files/f1',
  task_id: 't9',
};
const addresses = () => {
  queryClient.setQueryData(postalAddressesQuery.queryKey, { addresses: [ACTIVE, PENDING], orders: [] } as never);
  queryClient.setQueryData(lettersInQuery.queryKey, [LETTER_IN] as never);
  queryClient.setQueryData(addressGrantsQuery('as1').queryKey, [{ id: 'g1', address_id: 'as1', grantee_type: 'bot', grantee_id: 'b1', level: 'read' }] as never);
};

/* ---------------------------------------------------------------- letters out */

const SENT_LETTER = {
  id: 'L1',
  redacted: false,
  status: 'waiting_for_ok',
  can_cancel: true,
  created_at: '2026-11-18T00:00:00Z',
  recipient: { name: 'Sam Sample', line1: '2 Elm St', city: 'Dover', state: 'DE', postal_code: '19901' },
  return_address: { name: 'Sample Co', line1: '1 Fern Way', city: 'Dover', state: 'DE', postal_code: '19901' },
  page_count: 2,
  mail_class: 'first_class',
  color: false,
  duplex: true,
  total_micros: 1_500_000,
  billing: 'none',
  tracking: [{ barcode: '9400111', status: 'In transit' }],
  requested_by: 'human:me',
  acting_bot_id: null,
  proof: '/api/v2/files/p1',
  error: '',
  task_id: null,
};
/** The mailing address tool: one address and an order, a letter in, and `out` sent. */
const openAddress = (out: unknown[]) => {
  tools([indexRow({ id: 'address', kind: 'address', name: 'Mailing address', detail: '1 Fern Way, Dover DE', way: 'Tico', status: { tone: 'ok', text: 'Connected' } })]);
  queryClient.setQueryData(postalAddressesQuery.queryKey, { addresses: [ACTIVE], orders: [{ order_id: 'o1', state: 'TX', kind: 'private', status: 'open' }] } as never);
  queryClient.setQueryData(lettersInQuery.queryKey, [{ id: 'm1', kind: 'letter', summary: 'Tax notice', sender: 'County office', status: 'filed', received_at: '2026-11-17T00:00:00Z' }]);
  queryClient.setQueryData(lettersQuery.queryKey, out as never);
  queryClient.setQueryData(lettersSettingsQuery.queryKey, letters as never);
  queryClient.setQueryData(intakeQuery.queryKey, {});
  queryClient.setQueryData(legalMailQuery.queryKey, null as never);
  return open('address');
};

/* ---------------------------------------------------------------- Messages, and a message's code */

const INBOXES = [
  { inbox_id: 'i1', kind: 'email', name: 'Logins', address: 'logins@in.example.test', state: 'active', audience: 'company' },
  { inbox_id: 'i2', kind: 'sms', name: 'Mobile', address: '+15550100', state: 'active', audience: 'company' },
];
const MESSAGES = [
  { message_id: 'm1', inbox_id: 'i1', kind: 'email', from: 'bank@example.test', subject: 'Your code', received_at: 1763500000, has_code: true },
  { message_id: 'm2', inbox_id: 'i2', kind: 'sms', from: '+15550199', preview: 'See you at noon', received_at: 1763400000 },
];

describe('webhooks, letters and messages', () => {
  beforeEach(() => {
    as('person');
    queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
  });

describe('an outgoing webhook’s ⋯', () => {
  it('New secret asks first and shows the secret once, in its dialog only; Delete asks first and goes back to Tools; neither is Tico’s @s515', async () => {
    const sent = serve(hookServer);
    const router = openHook();
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New secret' }));
    const ask = await screen.findByRole('alertdialog', { name: 'Make a new signing secret?' });
    expect(writes(sent)).toEqual([]);
    fireEvent.click(within(ask).getByRole('button', { name: 'Make' }));
    const shown = await screen.findByRole('dialog', { name: 'Signing secret' });
    expect(within(shown).getByText('whsec_fake_0000')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/rotate', method: 'POST', body: {} }]);
    expect(cached()).not.toContain('whsec_fake_0000');
    fireEvent.click(within(shown).getByRole('button', { name: 'Done' }));
    expect(screen.queryByText('whsec_fake_0000')).toBeNull();

    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const sure = await screen.findByRole('alertdialog', { name: 'Delete Order feed?' });
    fireEvent.click(within(sure).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/delete', method: 'POST', body: {} }));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await tico('tools.newWebhookSecret', { webhook: 'wh1' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await tico('tools.deleteWebhook', { webhook: 'wh1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('shows where it sends and how its sends went; a failed send retries from its row; sends a test and pauses; Tico may pause, never restart @s515', async () => {
    const sent = serve(hookServer);
    openHook();
    expect(await screen.findByText('https://hooks.example.test/orders')).toBeTruthy();
    expect(screen.getByText('9 delivered, 1 failed, 0 waiting')).toBeTruthy();
    const failed = (await screen.findByText('Failed (500)')).closest('.row') as HTMLElement;
    fireEvent.click(within(failed).getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/sends/x1/retry', method: 'POST', body: {} }]));
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Send test' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/send', method: 'POST', body: { type: 'custom.test', subject: 'Test' } }));
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Pause' }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/webhooks/wh1/pause', method: 'POST', body: {} }));
    expect(await tico('tools.pauseWebhook', { webhook: 'wh1', on: true })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.pauseWebhook', { webhook: 'wh1', on: false })).ok).toBe(true);
  });
});

describe('one mailing address', () => {
  it('opens on its own page: who can read it (give, remove) and Check connection @s868', async () => {
    as('owner');
    const sent = serve((url) => (url === '/api/v2/postal/addresses' ? { addresses: [ACTIVE, PENDING], orders: [] } : undefined));
    addresses();
    open('address:as1');
    const page = await screen.findByRole('region', { name: '1 Fern Way, Dover DE' });
    expect(within(page).getByText('Yes')).toBeTruthy(); // registered agent
    const readers = within(page).getByRole('region', { name: 'Who may use it' });
    fireEvent.click(within(readers).getByRole('button', { name: 'Remove Clerk' }));
    await waitFor(() => expect(writes(sent)).toMatchObject([{ url: '/api/v2/postal/addresses/as1/grants/bot/b1', method: 'DELETE' }]));
    fireEvent.click(within(readers).getByRole('button', { name: 'Add someone' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Pat Sample/ }));
    await waitFor(() => expect(writes(sent).at(-1)).toEqual({ url: '/api/v2/postal/addresses/as1/grants/person/p2', method: 'PUT', body: { level: 'read' } }));
    const before = reads(sent)().filter((u) => u === '/api/v2/postal/addresses').length;
    fireEvent.click(within(page).getByRole('button', { name: 'Check connection' }));
    await waitFor(() => expect(reads(sent)().filter((u) => u === '/api/v2/postal/addresses').length).toBe(before + 1));
    expect(await tico('tools.setAddressAccess', { address: 'as1', type: 'person', who: 'p2', read: true })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.setAddressAccess', { address: 'as1', type: 'bot', who: 'b1', read: false })).ok).toBe(true);
  });

  it('its letters open their scan and their task, and an admin retries a failed one; an address waiting on Form 1583 shows its steps @s868', async () => {
    as('owner');
    const opened = vi.fn();
    vi.stubGlobal('open', opened);
    const sent = serve((url) =>
      url === '/api/v2/postal/addresses' ? { addresses: [ACTIVE, PENDING], orders: [] } : url === '/api/v2/postal/items?limit=100' ? { items: [LETTER_IN] } : undefined,
    );
    addresses();
    const router = open('address:as1');
    const page = await screen.findByRole('region', { name: '1 Fern Way, Dover DE' });
    expect(within(page).getByText('Government or tax, Failed')).toBeTruthy();
    const menu = () => fireEvent.click(within(page).getByRole('button', { name: 'County office options' }));
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open scan' }));
    expect(opened).toHaveBeenCalledWith('/api/v2/files/f1', '_blank', 'noopener');
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Retry' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/postal/items/m1/retry', method: 'POST', body: {} }]));
    menu();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Open task' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/task/t9'));
    cleanup();
    open('address:as2');
    const pending = await screen.findByRole('region', { name: 'TX, Private' });
    const steps = within(pending).getByRole('region', { name: 'How it connects' });
    expect(within(steps).getByText('Now').getAttribute('aria-current')).toBe('step');
    expect(within(steps).getByText('Now').closest('.fact')?.textContent).toContain('Form 1583');
  });
});

describe('letters out', () => {
  it('a letter’s ⋯ holds its details, its proof and Review; Load more reads the older ones @s869', async () => {
    const opened = vi.fn();
    vi.stubGlobal('open', opened);
    const sent = serve((url) =>
      url === '/api/v2/letters?limit=20&before=R18'
        ? { items: [{ id: 'O1', redacted: false, status: 'delivered', can_cancel: false, created_at: '2026-10-01T00:00:00Z', recipient: { name: 'Old Friend' }, page_count: 1, proof: null, tracking: [] }], next_before: null }
        : undefined,
    );
    const rest = Array.from({ length: 19 }, (_, n) => ({ id: `R${n}`, redacted: true, status: 'mailed', can_cancel: false, created_at: '2026-11-01T00:00:00Z', page_count: 1 }));
    const router = openAddress([SENT_LETTER, ...rest]);
    const options = async () => fireEvent.click(await screen.findByRole('button', { name: 'Sam Sample, Dover options' }));
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Details' }));
    const details = await screen.findByRole('dialog', { name: 'Sam Sample, Dover' });
    expect(within(details).getByText('Sam Sample, 2 Elm St, Dover DE 19901')).toBeTruthy();
    expect(within(details).getByText('Black and white, both sides')).toBeTruthy();
    expect(within(details).getByText('$1.50, not charged')).toBeTruthy();
    expect(within(details).getByText('9400111, In transit')).toBeTruthy();
    fireEvent.click(within(details).getByRole('button', { name: 'Close' }));
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Proof PDF' }));
    expect(opened).toHaveBeenCalledWith('/api/v2/files/p1', '_blank', 'noopener');
    // a private letter has no ⋯: its recipient and proof are not the reader's
    expect(screen.getAllByRole('button', { name: /options$/ }).filter((b) => b.getAttribute('aria-label')?.startsWith('A letter'))).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: 'Load more' }));
    expect(await screen.findByText('Old Friend')).toBeTruthy();
    expect(reads(sent)()).toContain('/api/v2/letters?limit=20&before=R18');
    await options();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Review' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/flow'));
  });
});

describe('Messages', () => {
  it('lists what came in to every address, narrowed by code, channel and words, a page at a time @s870', async () => {
    const sent = serve((url) => (url.startsWith('/api/v2/inbound/messages?') ? { messages: url.includes('before=') ? [] : MESSAGES, next: url.includes('before=') ? null : 77 } : undefined));
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    open('messages');
    const page = await screen.findByRole('region', { name: 'Messages' });
    expect(await within(page).findByText('Your code')).toBeTruthy();
    expect(within(page).getByText('See you at noon')).toBeTruthy();
    expect(reads(sent)()).toContain('/api/v2/inbound/messages?limit=50');
    fireEvent.click(within(page).getByRole('button', { name: 'Filter messages' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Has a code' }));
    await waitFor(() => expect(reads(sent)()).toContain('/api/v2/inbound/messages?limit=50&has_code=true'));
    fireEvent.click(within(page).getByRole('button', { name: 'Filter messages' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Channel' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Texts' }));
    await waitFor(() => expect(reads(sent)()).toContain('/api/v2/inbound/messages?limit=50&kind=sms&has_code=true'));
    fireEvent.change(within(page).getByRole('textbox', { name: 'Find messages' }), { target: { value: 'bank' } });
    await waitFor(() => expect(reads(sent)()).toContain('/api/v2/inbound/messages?limit=50&kind=sms&q=bank&has_code=true'));
    expect(within(page).getByRole('group', { name: 'Filters' }).textContent).toContain('Texts');
    fireEvent.click(await within(page).findByRole('button', { name: 'Older messages' }));
    await waitFor(() => expect(reads(sent)().some((u) => u.includes('before=77'))).toBe(true));
  });

  it('shows the fault with Try again when the inboxes fail, not "No messages yet" @s870', async () => {
    serve((url) => (url === '/api/v2/inbound/inboxes' ? refused(500, 'internal') : url.startsWith('/api/v2/inbound/messages?') ? { messages: [], next: null } : undefined));
    open('messages');
    const page = await screen.findByRole('region', { name: 'Messages' });
    expect(await within(page).findByText('Messages did not load')).toBeTruthy();
    expect(within(page).getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(within(page).queryByText('No messages yet')).toBeNull();
  });

  it('a message with a code reveals it in the reader only, on its own press; never Tico’s @s932', async () => {
    const CODE = '482913';
    const sent = serve((url) =>
      url === '/api/v2/inbound/messages/m1/code' ? { code: CODE } : url === '/api/v2/inbound/messages/m1' ? { text: 'Your sign-in code is inside.', next_offset: null } : undefined,
    );
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins' })] } as never);
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, [MESSAGES[0]] as never);
    open('tico:i1');
    fireEvent.click(await screen.findByRole('button', { name: /Your code/ }));
    const reader = await screen.findByRole('dialog', { name: 'Your code' });
    expect(await within(reader).findByText('Your sign-in code is inside.')).toBeTruthy();
    expect(reads(sent)()).not.toContain('/api/v2/inbound/messages/m1/code');
    fireEvent.click(within(reader).getByRole('button', { name: 'Reveal code' }));
    expect(await within(reader).findByText(CODE)).toBeTruthy();
    expect(cached()).not.toContain(CODE);
    expect(await tico('tools.revealCode', { message: 'm1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

/* ---------------------------------------------------------------- Show key, and what only an admin sees */

describe('who sees what', () => {
  it('Show key is offered to someone the key is given to, without the manager’s changes @s916', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    const given = { id: 'k1', name: 'Main key', preview: 'sk-…abcd', access_level: 'read', expires_on: null, expires_source: null };
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool({ keys: [given], access: [], recent: [], settings: [], can_manage: false }) as never);
    open('api:t1');
    expect(screen.queryByRole('button', { name: 'Replace key' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show key' }));
    expect(await screen.findByRole('dialog', { name: 'Main key' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Set end date' })).toBeNull();
    expect(screen.queryByRole('menuitem', { name: 'Remove key' })).toBeNull();
  });

  it('Add tool offers Email, Phone number and Mailing address only to an admin @s938 @s934 @s940', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    open('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    let kinds = await screen.findByRole('group', { name: 'Add by kind' });
    expect(within(kinds).getByRole('button', { name: 'Tico address' })).toBeTruthy();
    for (const name of ['Email', 'Phone number', 'Mailing address']) expect(within(kinds).queryByRole('button', { name })).toBeNull();
    cleanup();
    as('owner');
    open('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    kinds = await screen.findByRole('group', { name: 'Add by kind' });
    for (const name of ['Email', 'Phone number', 'Mailing address']) expect(await within(kinds).findByRole('button', { name })).toBeTruthy();
    expect((await tico('tools.requestNumber', { name: 'Mobile number' })).ok).toBe(true);
  });

  it('an address’s Stop using, and a number’s Edit, are an admin’s; anyone renames an address @s935 @s936', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, {
      rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins' }), indexRow({ id: 'number:i2', kind: 'number', name: 'Mobile' })],
    } as never);
    queryClient.setQueryData(inboxesQuery.queryKey, INBOXES);
    queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, []);
    queryClient.setQueryData(inboxMessagesQuery('i2').queryKey, []);
    open('tico:i1');
    await toolMore('Logins');
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Stop using' })).toBeNull();
    cleanup();
    open('number:i2');
    const number = await screen.findByRole('region', { name: 'Mobile' });
    expect(within(number).queryByRole('button', { name: 'More' })).toBeNull();
    cleanup();
    as('owner');
    open('number:i2');
    await toolMore('Mobile');
    expect(await screen.findByRole('menuitem', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Stop using' })).toBeTruthy();
  });
});
});

describe('mail settings', () => {
  beforeEach(() => queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null }));

describe('Google', () => {
  it('turns on what agents may do, and from Tico only turns things off', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    queryClient.setQueryData(googleQuery.queryKey, google());
    expect(await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setMailMay', { ...may, send_mail: false }, { via: 'tico' })).ok).toBe(true);
    expect((await run('tools.setMailMay', { ...may, organize_mail: true }, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/google/permissions', { ...may, send_mail: false }],
      ['PUT', '/api/v2/google/permissions', { ...may, organize_mail: true }],
    ]);
  });

  it('shows a setup command in its dialog only, and never makes one, takes a key or signs in for Tico', async () => {
    const CODE = 'tico google setup --code fake-0000-code';
    const sent = serve((url) => (url.endsWith('/setup-code') ? { command: CODE, expires_at: '2026-11-19T12:30:00Z' } : google()));
    queryClient.setQueryData(googleQuery.queryKey, google());
    at('/library/tools?tool=mailbox', <ToolsPage search={{ tool: 'mailbox' }} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Google settings' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Setup command' }));
    fireEvent.change(await screen.findByLabelText('Domain'), { target: { value: 'example.test' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make' }));
    expect(await screen.findByText(CODE)).toBeTruthy();
    expect(list(sent)).toContainEqual(['POST', '/api/v2/google/workspace/setup-code', { domain: 'example.test' }]);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('fake-0000');

    const before = sent.length;
    expect(await run('tools.googleSetup', { domain: 'example.test' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.uploadGoogleKey', { domain: 'example.test', key: '{"type":"service_account"}' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.connectMyGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.disconnectGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.disconnectMyGoogle', {}, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    // letters: Tico narrows, never widens
    expect(await run('tools.setIntake', { inbox: 'm1', senders: ['scans@example.test'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLetterAccess', { type: 'bot', who: 'fern', read: true }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLegalMail', { bot: 'fern' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent.length).toBe(before);
  });

  it('uploads a key as JSON, and refuses one that is not JSON', async () => {
    const sent = serve(() => ({ delegation_url: 'https://admin.example.test' }));
    at('/library/tools');
    expect((await run('tools.uploadGoogleKey', { domain: 'Example.test', key: '{"type":"service_account"}' }, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([['POST', '/api/v2/google/workspace/key', { domain: 'example.test', key: { type: 'service_account' } }]]);
    expect((await run('tools.uploadGoogleKey', { domain: 'example.test', key: 'not json' }, { via: 'click' })).ok).toBe(false);
  });

  it('checks and disconnects the Workspace', async () => {
    const sent = serve((url) => (url.endsWith('/check') ? { ok: false, message: 'nope' } : {}));
    at('/library/tools');
    expect(await run('tools.checkGoogle', {}, { via: 'tico' })).toMatchObject({ ok: true, did: 'Google did not accept it' });
    expect((await run('tools.disconnectGoogle', {}, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([
      ['POST', '/api/v2/google/workspace/check', {}],
      ['DELETE', '/api/v2/google/workspace'],
    ]);
  });
});

describe('what agents may send', () => {
  it('knows a change that lets more out', () => {
    expect(sendsMore(undefined, { mode: 'never' })).toBe(true);
    expect(sendsMore(policy, { mode: 'never', daily_cap: 5, undo_seconds: 120 })).toBe(false);
    expect(sendsMore(policy, { mode: 'on_their_own' })).toBe(true);
    expect(sendsMore(policy, { to: 'anyone' })).toBe(true);
    expect(sendsMore(policy, { daily_cap: 21 })).toBe(true);
    expect(sendsMore(policy, { undo_seconds: 0 })).toBe(true);
    expect(sendsMore(policy, { ask_new_address: false })).toBe(true);
    expect(sendsMore(policy, { blocked_recipients: [] })).toBe(true);
    expect(sendsMore(policy, { blocked_recipients: ['@rival.test', '@other.test'] })).toBe(false);
  });

  it('sends only the change, and from Tico only a tighter one', async () => {
    const sent = serve();
    queryClient.setQueryData(sendingQuery.queryKey, { effective: policy, company: policy });
    at('/library/tools');
    expect(await run('tools.setSending', { mode: 'on_their_own' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setSending', { daily_cap: 10 }, { via: 'tico' })).ok).toBe(true);
    expect((await run('tools.setSending', { mode: 'on_their_own', undo_seconds: 120 }, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/email/sending-policy', { daily_cap: 10 }],
      ['PUT', '/api/v2/email/sending-policy', { mode: 'on_their_own', undo_seconds: 120 }],
    ]);
    expect((await run('tools.setSending', { undo_seconds: 45 }, { via: 'click' })).ok).toBe(false);
  });

  it('sets one agent’s own mode or hands it back, and from Tico never loosens it', async () => {
    const sent = serve();
    queryClient.setQueryData(sendingQuery.queryKey, { effective: policy, company: policy });
    queryClient.setQueryData(agentSendingQuery('fern').queryKey, { mode: 'never' });
    at('/library/tools');
    expect(await run('tools.setAgentSending', { bot: 'fern', mode: null }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setAgentSending', { bot: 'fern', mode: 'ask_first' }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setAgentSending', { bot: 'fern', mode: null }, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/bots/fern/email-sending-policy', { mode: 'ask_first' }],
      ['PUT', '/api/v2/bots/fern/email-sending-policy', { inherit: true }],
    ]);
  });
});

describe('the shared Inbox', () => {
  const CODE = 'x'.repeat(43);

  it('names the company first, joins once agreed, and keeps the code out of the cache', async () => {
    const sent = serve(() => ({ connected: false, company: { company_id: 'c1', name: 'Sample Co' } }));
    at('/library/tools', <SharedInboxDialog onClose={() => {}} />);
    fireEvent.change(await screen.findByLabelText('Connection code'), { target: { value: CODE } });
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(await screen.findByText('Sample Co')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Join' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Share Inbox both ways with this company' }));
    fireEvent.click(screen.getByRole('button', { name: 'Join' }));
    await waitFor(() =>
      expect(list(sent)).toEqual([
        ['POST', '/api/v2/inbound/company-connection', { action: 'preview', code: CODE }],
        ['POST', '/api/v2/inbound/company-connection', { action: 'connect', code: CODE }],
      ]),
    );
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain(CODE);
  });

  it('is never Tico’s to join', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.joinSharedInbox', { code: CODE }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('number requests', () => {
  const later = Math.floor(Date.now() / 1000) + 3600;

  it('shows a fresh quote to accept, an old one as expired, and cancels', async () => {
    const sent = serve();
    queryClient.setQueryData(numberRequestsQuery.queryKey, [
      { request_id: 'r1', name: 'Front desk', state: 'quoted', price_micros: 2_500_000, quoted_until: later },
      { request_id: 'r2', name: 'Night line', state: 'quoted', price_micros: 2_500_000, quoted_until: 1 },
      { request_id: 'r3', name: 'Spare line', state: 'requested' },
      { request_id: 'r4', name: 'Old line', state: 'canceled' },
    ]);
    at('/library/tools', <NumberRequests />);
    expect(await screen.findByText('$2.50 for 30 days')).toBeTruthy();
    expect(screen.getByText('Quote expired')).toBeTruthy();
    expect(screen.getByText('Awaiting quote')).toBeTruthy();
    expect(screen.queryByText('Old line')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Accept' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(list(sent)).toEqual([['POST', '/api/v2/inbound/phone-requests/r1/accept', { price_micros: 2_500_000 }]]));
    expect((await run('tools.cancelNumberRequest', { request: 'r3' }, { via: 'tico' })).ok).toBe(true);
    expect(list(sent)[1]).toEqual(['POST', '/api/v2/inbound/phone-requests/r3/cancel', {}]);
  });

  it('never accepts a quote for Tico: it is paid', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.acceptQuote', { request: 'r1', price_micros: 2_500_000 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
  });
});

describe('letters', () => {
  it('chooses where emailed letters come in, and refuses an inbox with no senders', async () => {
    const sent = serve();
    at('/library/tools');
    expect(await run('tools.setIntake', { inbox: 'ib1', senders: [] }, { via: 'click' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await run('tools.setIntake', { inbox: 'ib1', senders: [' Scans@Provider.test', '@provider.test'] }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setIntake', { inbox: null, senders: ['@provider.test'] }, { via: 'click' })).ok).toBe(true);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/postal/intake', { inbox_id: 'ib1', senders: ['scans@provider.test', '@provider.test'] }],
      ['PUT', '/api/v2/postal/intake', { inbox_id: null, senders: [] }],
    ]);
  });

  it('lets someone read every letter and takes it back; nobody but a person or an agent', async () => {
    const sent = serve();
    at('/library/tools');
    expect((await run('tools.setLetterAccess', { type: 'bot', who: 'fern', read: true }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLetterAccess', { type: 'person', who: 'p1', read: false }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLetterAccess', { type: 'admins', who: 'x', read: true }, { via: 'click' })).ok).toBe(false);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/postal/addresses/all/grants/bot/fern', { level: 'read' }],
      ['DELETE', '/api/v2/postal/addresses/all/grants/person/p1'],
    ]);
  });

  it('sends legal letters to an agent or back to the owner', async () => {
    const sent = serve();
    at('/library/tools');
    expect((await run('tools.setLegalMail', { bot: 'fern' }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLegalMail', { bot: null }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLegalMail', { bot: '' }, { via: 'click' })).ok).toBe(false);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/postal/settings', { legal_bot_id: 'fern' }],
      ['PUT', '/api/v2/postal/settings', { legal_bot_id: null }],
    ]);
  });

  it('saves how letters go out, and from Tico never raises the cap or lets more go', async () => {
    const sent = serve(() => letters);
    queryClient.setQueryData(lettersSettingsQuery.queryKey, letters as never);
    at('/library/tools');
    expect(await run('tools.setLettersOut', { monthly_cap_micros: 80_000_000 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLettersOut', { monthly_cap_micros: null }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('tools.setLettersOut', { mode: 'on_their_own' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toEqual([]);
    expect((await run('tools.setLettersOut', { monthly_cap_micros: 20_000_000 }, { via: 'tico' })).ok).toBe(true);
    const address = { name: 'Sample Co', line1: '1 Main St', city: 'Springfield', state: 'IL', postal_code: '62701' };
    expect((await run('tools.setLettersOut', { mode: 'on_their_own', return_address: address }, { via: 'click' })).ok).toBe(true);
    expect((await run('tools.setLettersOut', { return_address: { ...address, state: 'Illinois' } }, { via: 'click' })).ok).toBe(false);
    expect(list(sent)).toEqual([
      ['PUT', '/api/v2/letters/settings', { monthly_cap_micros: 20_000_000 }],
      ['PUT', '/api/v2/letters/settings', { mode: 'on_their_own', return_address: address }],
    ]);
    expect((await run('tools.checkLetters', {}, { via: 'tico' })).ok).toBe(true);
    expect(sent.at(-1)).toMatchObject({ method: 'GET', url: '/api/v2/letters/settings?check=true' });
  });
});

describe('the settings pages', () => {
  function show(path: string) {
    const Tools = () => <ToolsPage search={toolsSearch(useSearch({ strict: false }))} />;
    const router = createRouter({ routeTree: createRootRoute({ component: Tools }), history: createMemoryHistory({ initialEntries: [path] }) });
    bind({ router });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }
  const reply = (url: string) =>
    url.includes('/tool-index')
      ? { rows: [] }
      : url === '/api/v2/google'
        ? google({ workspace: { status: 'connected', domain: 'example.test', client_id: '1', delegation_url: '', checked_at: null, last_error: null } })
        : url.startsWith('/api/v2/email/sending-policy')
          ? { effective: policy, company: policy, bot: {}, inbox: {} }
          : url.startsWith('/api/v2/inbound/phone-requests')
            ? { phone_requests: [], next: null }
            : url.startsWith('/api/v2/postal/settings')
              ? { settings: { legal_bot_id: null } }
              : url.startsWith('/api/v2/letters?') || url.startsWith('/api/v2/postal/items')
                ? { items: [] }
                : {};

  it('gives Email its Google connection, what agents may do and what they may send', async () => {
    serve(reply);
    show('/?tool=mailbox');
    expect(await screen.findByRole('heading', { name: 'Email' })).toBeTruthy();
    expect(await screen.findByText('example.test, connected')).toBeTruthy();
    expect(await screen.findByRole('switch', { name: 'Send mail' })).toBeTruthy();
    expect(await screen.findByText('Ask first')).toBeTruthy();
    expect(screen.getByText('20 an agent, 50 a mailbox, 200 in all')).toBeTruthy();
  });

  it('gives phone numbers their requests, and letters their rules', async () => {
    serve(reply);
    const router = show('/?tool=number');
    expect(await screen.findByText('No requests')).toBeTruthy();
    await act(async () => void (await run('tools.open', { tool: 'address' }, { via: 'click' })));
    expect(router.state.location.search).toMatchObject({ tool: 'address' });
    expect(await screen.findByText('Legal letters')).toBeTruthy();
  });
});
});

describe('the head, and sections whose read fails', () => {
  beforeEach(() => {
    queryClient.setQueryData(teamsQuery.queryKey, []);
    queryClient.setQueryData(vaultQuery.queryKey, { credentials: [], can_manage: false, people: [], bots: [] } as never);
  });

describe('Tools head', () => {
  it('keeps Filter, Add and the ⋯ on a phone; the settings pages are in the ⋯', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve();
    tools([indexRow({ id: 'api:a1', name: 'Sample API' })]);
    const router = at('/library/tools', <ToolsPage search={{}} />);
    const head = (await screen.findByRole('heading', { name: 'Tools' })).closest('header') as HTMLElement;
    expect(controls(head)).toEqual(['Filter tools', 'Add tool']);
    await addKind('API');
    expect(await screen.findByRole('dialog', { name: 'Add API' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(within(head).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Number requests' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tool: 'number' }));
  });

  it('keeps Add tool beside Filter on a wide screen, with the ⋯ last', async () => {
    serve();
    tools([indexRow({ id: 'api:a1', name: 'Sample API' })]);
    at('/library/tools', <ToolsPage search={{}} />);
    const head = (await screen.findByRole('heading', { name: 'Tools' })).closest('header') as HTMLElement;
    const labels = within(head)
      .getAllByRole('button')
      .map((b) => b.getAttribute('aria-label'));
    expect(labels).toEqual(['Filter tools', 'Add tool', 'More']);
  });
});

describe('a mailbox with no record of its own', () => {
  it('shows what it can do and how it sends, as a mailbox’s page does', async () => {
    serve();
    tools([indexRow({ id: 'mailbox:sales', kind: 'mailbox', name: 'sales@example.test', detail: 'sales@example.test' })]);
    queryClient.setQueryData(googleQuery.queryKey, { can_admin: false, workspace: null, pending: null, me: null, permissions: { organize_mail: false, send_mail: true, change_calendar: false } });
    queryClient.setQueryData(sendingQuery.queryKey, { effective: { mode: 'ask_first', daily_cap: 20, mailbox_daily_cap: 50, company_daily_cap: 200 }, company: {} } as never);
    open('mailbox:sales');
    const box = await screen.findByRole('region', { name: 'sales@example.test' });
    const may = await within(box).findByRole('region', { name: 'What it can do' });
    expect(within(may).getByText('Send mail')).toBeTruthy();
    expect(within(may).getByText('Ask first')).toBeTruthy();
  });
});

describe('a section whose read fails', () => {
  const requests = '/api/v2/inbound/phone-requests';

  it('on Phone numbers says the requests did not load, and Try again reads them again', async () => {
    let answer: unknown = refused(409, 'gateway');
    const asked = reads(serve((url) => (url.startsWith(requests) ? answer : undefined)));
    tools();
    open('number');
    expect(await screen.findByText('Requests did not load')).toBeTruthy();
    expect(screen.queryByText('No requests')).toBeNull();
    const before = asked().filter((u) => u.startsWith(requests)).length;
    answer = ok({ phone_requests: [], next: null });
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('No requests')).toBeTruthy();
    expect(asked().filter((u) => u.startsWith(requests)).length).toBe(before + 1);
  });

  it('says a 403 is not shared, with nothing to try again', async () => {
    serve((url) => (url.startsWith(requests) ? refused(403, 'forbidden') : undefined));
    tools();
    open('number');
    expect(await screen.findByText('Not shared with you')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(screen.queryByText('No requests')).toBeNull();
  });

  it('on the mailing address says the addresses did not load, beside the rest of the page', async () => {
    serve((url) =>
      url === '/api/v2/postal/addresses'
        ? refused(503, 'rehearsal')
        : url.startsWith('/api/v2/postal/settings')
          ? ok({ settings: { legal_bot_id: null } })
          : url.startsWith('/api/v2/letters?') || url.startsWith('/api/v2/postal/items')
            ? ok({ items: [] })
            : undefined,
    );
    tools([indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })]);
    queryClient.setQueryData(lettersQuery.queryKey, []);
    queryClient.setQueryData(lettersInQuery.queryKey, []);
    queryClient.setQueryData(lettersSettingsQuery.queryKey, { mode: 'ask_first', monthly_cap_micros: null, return_address: null, availability: 'ready' } as never);
    open('address');
    const connects = await screen.findByRole('region', { name: 'How it connects' });
    expect(await within(connects).findByText('Addresses did not load')).toBeTruthy();
    expect(within(connects).getByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'What it can do' })).toBeTruthy();
  });

  it('says how letters go out did not load, rather than leaving it out', async () => {
    serve((url) => (url.startsWith('/api/v2/letters/settings') ? refused(500, 'boom') : url.startsWith('/api/v2/letters?') ? ok({ items: [] }) : undefined));
    tools([indexRow({ id: 'address', kind: 'address', name: 'Mailing address' })]);
    queryClient.setQueryData(lettersQuery.queryKey, []);
    open('address');
    const out = await screen.findByRole('region', { name: 'What it can do' });
    expect(await within(out).findByText('Sending letters did not load')).toBeTruthy();
  });
});
});

describe('the list and the open tool’s states', () => {
  it('lists the tools in use and the unassigned ones, each opening beside the list, each group folding by its name; with no tools at all says so with Add tool @s862', async () => {
    serve();
    tools([indexRow({ id: 'api:t1', name: 'Sample API', who_total: 1 }), indexRow({ id: 'api:t2', name: 'Other API' })]);
    open('');
    expect((await screen.findByText('Sample API')).closest('a')?.getAttribute('href')).toBe('/library/tools?tool=api%3At1');
    expect(screen.getByText('Unassigned')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^In use/ }));
    expect(screen.queryByText('Sample API')).toBeNull();
    expect(screen.getByText('Other API')).toBeTruthy();
    cleanup();
    tools();
    open('');
    expect(await screen.findByText('No tools yet')).toBeTruthy();
    fireEvent.click(screen.getByText('Add tool', { selector: 'button' }));
    expect(within(await screen.findByRole('group', { name: 'Add by kind' })).getByRole('button', { name: 'API' })).toBeTruthy();
  });

  // A Tools list not shared with you is the router's no-access page (PageFault).
  it('says a tool not shared with you is no access, with nothing to try again', async () => {
    serve((url) => (url === '/api/v2/api-tools/t9' ? refused(403, 'forbidden') : undefined));
    tools([indexRow({ id: 'api:t9', name: 'Hidden API' })]);
    open('api:t9');
    const tool = await page('Hidden API');
    expect(await within(tool).findByText("You don't have access to this page")).toBeTruthy();
    expect(within(tool).queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('an open tool shows grey bars while it loads, and Try again when its read fails', async () => {
    let fail = true;
    const tool1 = { tool: { id: 't1', name: 'Sample API', base_url: 'https://api.example.com', auth_scheme: 'bearer', delivery: 'proxy' }, keys: [], access: [], checks: [], recent: [], status: { tone: 'ok', text: 'Working' }, can_manage: false, mcp: null };
    serve((url) => (url === '/api/v2/api-tools/t1' ? (fail ? refused(500, 'boom') : tool1) : undefined));
    tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
    open('api:t1');
    const tool = await page('Sample API');
    expect(within(tool).getByRole('status', { name: 'Loading' })).toBeTruthy();
    expect(await within(tool).findByText('Sample API did not load')).toBeTruthy();
    fail = false;
    fireEvent.click(within(tool).getByRole('button', { name: 'Try again' }));
    expect(await within(tool).findByRole('region', { name: 'What it is' })).toBeTruthy();
  });
});

describe('an outgoing webhook’s add and edit', () => {
  const SECRET = 'whsec_fake_1111';

  it('Add tool › Webhook sends its name, address and events, and shows the secret once in the dialog only @s515', async () => {
    const sent = serve((url, method) => (url === '/api/v2/event-types' ? hookTypes : method === 'POST' && url === '/api/v2/webhooks' ? { webhook: { ...HOOK, secret: SECRET } } : undefined));
    tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
    open('');
    await addKind('Webhook');
    const dialog = await screen.findByRole('dialog', { name: 'Add webhook' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Order feed' } });
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'https://hooks.example.test/orders' } });
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: 'Task created' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    expect(await screen.findByText(SECRET)).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks', method: 'POST', body: { name: 'Order feed', url: 'https://hooks.example.test/orders', events: ['task.created'], allow_http: false } }]);
    expect(cached()).not.toContain(SECRET);
    expect(await tico('tools.addWebhook', { name: 'X', url: 'https://x.example.test', events: ['task.created'] })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Edit sends only what changed; Tico may rename it, never send it somewhere new @s515', async () => {
    const sent = serve(hookServer);
    openHook();
    await toolMore('Order feed');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit webhook' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Orders' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/webhooks/wh1/update', method: 'POST', body: { name: 'Orders', expected_revision: 4 } }]));
    expect(await tico('tools.editWebhook', { webhook: 'wh1', url: 'https://elsewhere.example.test' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.editWebhook', { webhook: 'wh1', events: ['task.done'] })).ok).toBe(true);
  });
});

describe('Tools', () => {
  it('an API tool shows what it is, its key and its recent calls @s864 @s883', async () => {
    serve();
    openApi();
    expect(await screen.findByText('api.example.com')).toBeTruthy();
    expect(screen.getByText('Main key')).toBeTruthy();
    expect(screen.getByText('GET /v1/items → 200')).toBeTruthy();
  });

  it('keeps the Tools search and filters in the row and the phone back link', async () => {
    serve();
    tools([indexRow({ id: 'api:t1', name: 'Sample API', needs_you: true, who: [{ id: 'p1', kind: 'person', name: 'Pat Sample' }] })]);
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    const filters = { q: 'sample', kind: 'api', needs: 1, who: 'human:p1' } as const;
    at(
      '/library/tools?q=sample&kind=api&needs=1&who=human%3Ap1&tool=api%3At1',
      <ToolsPage search={{ ...filters, tool: 'api:t1' }} />,
    );
    const row = await screen.findByRole('link', { name: /Sample API/ });
    const back = screen.getByRole('link', { name: 'Back to Tools' });
    const params = (link: HTMLElement) => Object.fromEntries(new URL(link.getAttribute('href')!, 'http://example.test').searchParams);
    const expected = { ...filters, needs: '1' };
    expect(params(row)).toEqual({ ...expected, tool: 'api:t1' });
    expect(params(back)).toEqual(expected);
  });

  it('replaces a key: the value is only sent, never kept @s914 @s840', async () => {
    const FAKE = 'sk-new-fake-0000';
    const sent = serve();
    openApi({ keys: [KEY] });
    fireEvent.click(await screen.findByRole('button', { name: 'Replace key' }));
    const dialog = await screen.findByRole('dialog', { name: 'Replace the Sample API key' });
    fireEvent.change(within(dialog).getByLabelText('Key'), { target: { value: FAKE } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Replace' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/keys/k1', method: 'PATCH', body: { value: FAKE } }]));
    expect(cached()).not.toContain(FAKE);
    expect(await run('tools.setKey', { tool: 't1', key: 'k1', value: FAKE }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('adds someone who may use it at Read; Tico may not widen it @s926', async () => {
    const sent = serve();
    queryClient.setQueryData(['directory'], { actors: people, byId: Object.fromEntries(people.map((p) => [p.id, p])) });
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Add someone' }));
    expect(screen.queryByRole('menuitem', { name: 'Pat Sample' })).toBeNull();
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Robin Sample' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/access/human%3Ap2', method: 'PUT', body: { level: 'read' } }]));
    expect(await run('tools.setAccess', { tool: 't1', subject: 'human:p3', level: 'read' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('a website shows its site and logins, and adds an account @s865 @s924', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { id: 'a2' } : undefined));
    openSite();
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

describe('an API tool’s dialogs', () => {
  it('Edit sends only what changed; Tico may not move a tool that holds a key @s912', async () => {
    const sent = serve();
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit API' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Sample API v2' } });
    fireEvent.change(within(dialog).getByLabelText('Base URL'), { target: { value: 'https://api2.example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1', method: 'PATCH', body: { name: 'Sample API v2', base_url: 'https://api2.example.com' } }]),
    );
    queryClient.setQueryData(apiToolQuery('t1').queryKey, apiTool() as never);
    expect(await tico('tools.editApi', { tool: 't1', base_url: 'https://elsewhere.example' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.editApi', { tool: 't1', name: 'Renamed' })).ok).toBe(true);
  });

  it('adds a run setting by its name in capitals, and removes one @s913', async () => {
    const sent = serve((url, method) => (method === 'GET' && url === '/api/v2/api-tools/t1' ? apiTool() : undefined));
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add run setting' }));
    const add = await screen.findByRole('dialog', { name: 'Add run setting' });
    fireEvent.change(within(add).getByLabelText('Name'), { target: { value: 'account_id' } });
    fireEvent.change(within(add).getByLabelText('Value'), { target: { value: '42' } });
    fireEvent.click(within(add).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/settings', method: 'POST', body: { name: 'ACCOUNT_ID', value: '42' } }]));
    fireEvent.click(await screen.findByRole('button', { name: /^REGION/ }));
    const edit = await screen.findByRole('dialog', { name: 'REGION' });
    fireEvent.click(within(edit).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(writes(sent).at(-1)).toMatchObject({ url: '/api/v2/api-tools/t1/settings/set1', method: 'DELETE' }));
  });

  it('a key’s end date is set from its ⋯ and cleared with Never ends; a vendor’s date is not ours to set @s915', async () => {
    const sent = serve();
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Vendor key options' }));
    expect(await screen.findByRole('menuitem', { name: 'Show key' })).toBeTruthy();
    expect(screen.queryByRole('menuitem', { name: 'Set end date' })).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    cleanup();
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Set end date' }));
    const dialog = await screen.findByRole('dialog', { name: 'When Main key ends' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Never ends' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools/t1/keys/k1', method: 'PATCH', body: { expires_on: '' } }]));
  });

  it('Show key reads the value once into the dialog only, and is never Tico’s @s916', async () => {
    const FAKE = 'sk-shown-fake-0000';
    const sent = serve((url) => (url === '/api/v2/credentials/k1/reveal' ? { id: 'k1', value: FAKE } : undefined));
    openApi();
    fireEvent.click(await screen.findByRole('button', { name: 'Main key options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show key' }));
    const dialog = await screen.findByRole('dialog', { name: 'Main key' });
    expect(within(dialog).getByText('Seeing it is logged.')).toBeTruthy();
    expect(writes(sent)).toEqual([]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show' }));
    expect(await within(dialog).findByText(FAKE)).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/credentials/k1/reveal', method: 'POST', body: {} }]);
    expect(cached()).not.toContain(FAKE);
    expect(JSON.stringify(queryClient.getMutationCache().getAll().map((m) => m.state.data))).not.toContain(FAKE);
    expect(await tico('tools.reveal', { credential: 'k1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('Add many keys shows what a paste holds by name first, then stores it in one step @s917', async () => {
    const PASTE = 'ACME_KEY=sk-fake-1234\nREGION=us';
    const sent = serve((url) =>
      url === '/api/v2/tool-templates/classify'
        ? { template: null, items: [{ role: 'secret', name: 'ACME_KEY', preview: 'sk-…1234' }, { role: 'setting', name: 'REGION', preview: 'us' }] }
        : url.endsWith('/paste')
          ? { keys: 1, settings: 1 }
          : undefined,
    );
    openApi();
    await toolMore('Sample API');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add many keys' }));
    const dialog = await screen.findByRole('dialog', { name: 'Add many keys' });
    fireEvent.change(within(dialog).getByLabelText('Paste'), { target: { value: PASTE } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Next' }));
    expect(await within(dialog).findByText('ACME_KEY')).toBeTruthy();
    expect(within(dialog).queryByText('sk-fake-1234')).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([
        { url: '/api/v2/tool-templates/classify', method: 'POST', body: { text: PASTE } },
        { url: '/api/v2/api-tools/t1/paste', method: 'POST', body: { text: PASTE } },
      ]),
    );
    expect(cached()).not.toContain('sk-fake-1234');
    expect(await tico('tools.pasteKeys', { tool: 't1', text: PASTE })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

const openSite = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'website:w1', kind: 'website', name: 'Sample site' })] } as never);
  queryClient.setQueryData(websiteToolQuery('w1').queryKey, website() as never);
  at('/library/tools?tool=website%3Aw1', <ToolsPage search={{ tool: 'website:w1' }} />);
};

describe('a website’s dialogs', () => {
  it('renames the website from its name in the head @s920', async () => {
    const sent = serve();
    openSite();
    const tool = await screen.findByRole('region', { name: 'Sample site' });
    fireEvent.click(within(tool).getByRole('heading', { name: 'Sample site' }));
    const name = within(tool).getByRole('textbox', { name: 'Name' });
    fireEvent.change(name, { target: { value: 'Shop admin' } });
    fireEvent.keyDown(name, { key: 'Enter' });
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/website-tools/w1', method: 'PATCH', body: { name: 'Shop admin' } }]));
  });

  it('changes the sites one login covers, one to a line; Tico may not add a site to one with logins @s921', async () => {
    const sent = serve();
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: /^Site/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Sites' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Sites' }), { target: { value: 'app.example.com\nshop.example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/website-tools/w1', method: 'PATCH', body: { sites: ['app.example.com', 'shop.example.com'] } }]),
    );
    queryClient.setQueryData(websiteToolQuery('w1').queryKey, website() as never);
    expect(await tico('tools.editWebsite', { tool: 'w1', sites: ['app.example.com', 'other.example.com'] })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect((await tico('tools.editWebsite', { tool: 'w1', sites: [] })).ok).toBe(true);
  });

  it('shows the sign-in code the authenticator has now, spaced, in the dialog only @s922', async () => {
    const sent = serve((url) => (url === '/api/v2/website-tools/w1/accounts/a1/code' ? { code: '482913', expires_in: 30 } : undefined));
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: 'ops@example.com options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show sign-in code' }));
    const dialog = await screen.findByRole('dialog', { name: 'Sign-in code' });
    expect(await within(dialog).findByText('482 913')).toBeTruthy();
    expect(sent.some((x) => x.url === '/api/v2/website-tools/w1/accounts/a1/code')).toBe(true);
    expect(cached()).not.toContain('482913');
    expect(await tico('tools.signInCode', { tool: 'w1', account: 'a1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('shows the stored password once, on Show, in the dialog only @s923', async () => {
    const FAKE = 'fake-pass-0000';
    const sent = serve((url) => (url === '/api/v2/credentials/a1/reveal' ? { password: FAKE } : undefined));
    openSite();
    fireEvent.click(await screen.findByRole('button', { name: 'ops@example.com options' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Show password' }));
    const dialog = await screen.findByRole('dialog', { name: 'Password' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Show' }));
    expect(await within(dialog).findByText(FAKE)).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/credentials/a1/reveal', method: 'POST', body: {} }]);
    expect(cached()).not.toContain(FAKE);
  });
});

const box = { inbox_id: 'i1', kind: 'email', name: 'Logins', address: 'logins@in.example.test', state: 'active', audience: 'company', used_for: 'Bank sign-in codes' };
const openInbox = () => {
  queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'tico:i1', kind: 'tico', name: 'Logins', detail: box.address })] } as never);
  queryClient.setQueryData(inboxesQuery.queryKey, [box]);
  queryClient.setQueryData(inboxMessagesQuery('i1').queryKey, [{ message_id: 'm1', seq: 1, from: 'bank@example.test', subject: 'Your code', received_at: 1763500000 }] as never);
  return at('/library/tools?tool=tico%3Ai1', <ToolsPage search={{ tool: 'tico:i1' }} />);
};

describe('a Tico address’s dialogs', () => {
  it('a message that came in opens as plain text, a page at a time @s932', async () => {
    const sent = serve((url) =>
      url === '/api/v2/inbound/messages/m1'
        ? { text: 'Your code is <b>482913</b>.', next_offset: 30 }
        : url === '/api/v2/inbound/messages/m1?offset=30'
          ? { text: ' It ends in ten minutes.', next_offset: null }
          : undefined,
    );
    openInbox();
    fireEvent.click(await screen.findByRole('button', { name: /Your code/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Your code' });
    expect(await within(dialog).findByText('Your code is <b>482913</b>.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Read more' }));
    expect(await within(dialog).findByText('Your code is <b>482913</b>. It ends in ten minutes.')).toBeTruthy();
    expect(within(dialog).queryByRole('button', { name: 'Read more' })).toBeNull();
    expect(sent.filter((x) => x.url.startsWith('/api/v2/inbound/messages/m1')).map((x) => x.url)).toEqual(['/api/v2/inbound/messages/m1', '/api/v2/inbound/messages/m1?offset=30']);
    expect(cached()).not.toContain('482913');
  });

  it('Edit renames it and says what depends on it @s935', async () => {
    const sent = serve();
    openInbox();
    await toolMore('Logins');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Bank logins' } });
    fireEvent.change(within(dialog).getByLabelText('Used for'), { target: { value: 'Bank and payroll codes' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes/i1', method: 'PATCH', body: { name: 'Bank logins', used_for: 'Bank and payroll codes' } }]),
    );
  });

  it('Stop using asks for the address typed back when something depends on it, and is never Tico’s @s936', async () => {
    const sent = serve();
    // Stopping is an owner's or admin's (as the old page); see 'who sees what'.
    as('owner');
    const router = openInbox();
    await toolMore('Logins');
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Stop using' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Stop using Logins?' });
    const stop = within(dialog).getByRole('button', { name: 'Stop using' });
    expect(stop).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText(`Type ${box.address}`), { target: { value: box.address } });
    fireEvent.click(stop);
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes/i1/release', method: 'POST', body: { confirm: box.address } }]));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(await tico('tools.releaseInbox', { inbox: 'i1' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Add tool’s mail dialogs', () => {
  const add = (adding: 'tico' | 'number' | 'mailbox' | 'address') => at('/library/tools', <AddDialog adding={adding} onClose={() => {}} />);

  it('a new Tico address takes a name, and opens @s933', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { inbox_id: 'i9' } : undefined));
    const router = add('tico');
    const dialog = await screen.findByRole('dialog', { name: 'New Tico address' });
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Account logins' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Make' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'tico:i9' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/inboxes', method: 'POST', body: { kind: 'email', name: 'Account logins' } }]);
  });

  it('asks for a US mobile number, which buys nothing, and opens the requests; Tico may ask too @s934', async () => {
    const sent = serve();
    const router = add('number');
    const dialog = await screen.findByRole('dialog', { name: 'Ask for a US mobile number' });
    expect(within(dialog).getByText(/nothing is bought now/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Ask' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'number' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/inbound/phone-requests', method: 'POST', body: { name: 'Mobile number' } }]);
    // Asking buys nothing; accepting the quote stays the person's own.
    expect((await tico('tools.requestNumber', { name: 'Mobile number' })).ok).toBe(true);
    expect(await tico('tools.acceptQuote', { request: 'r1', price_micros: 1 })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('connects a mailbox by its address, shared or not, and opens it; never Tico’s @s938', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { mailbox: { id: 'mb1' } } : undefined));
    const router = add('mailbox');
    const dialog = await screen.findByRole('dialog', { name: 'Connect mailbox' });
    const go = within(dialog).getByRole('button', { name: 'Connect' });
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'sales' } });
    expect(go).toHaveProperty('disabled', true);
    fireEvent.change(within(dialog).getByLabelText('Address'), { target: { value: 'Sales@Example.test' } });
    fireEvent.click(within(dialog).getByRole('switch', { name: 'Shared inbox' }));
    fireEvent.click(go);
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'mailbox:mb1' }));
    expect(writes(sent)).toEqual([{ url: '/api/v2/mailboxes', method: 'POST', body: { address: 'sales@example.test', shared: true } }]);
    expect(await tico('tools.connectMailbox', { address: 'ops@example.test', shared: false })).toMatchObject({ ok: false, error: 'not_allowed' });
  });

  it('orders a mailing address by kind, state and company name; paid, so never Tico’s @s940', async () => {
    const sent = serve();
    add('address');
    const dialog = await screen.findByRole('dialog', { name: 'Get a mailing address' });
    expect(within(dialog).getByText('$10 a month.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Private' }));
    expect(await within(dialog).findByText('From $25 a month.')).toBeTruthy();
    fireEvent.change(within(dialog).getByLabelText('Company name'), { target: { value: 'Sample Co' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Get address' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/postal/orders', method: 'POST', body: { kind: 'private', state: 'DE', company_name: 'Sample Co' } }]));
    expect(await tico('tools.orderAddress', { kind: 'shared', state: 'DE', company_name: 'Sample Co' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('the mailing address and letters', () => {
  it('shows its addresses, each a link to its page, and orders, and the letters that came in @s868', async () => {
    serve();
    openAddress([]);
    const tool = await screen.findByRole('region', { name: 'Mailing address' });
    expect(within(tool).getAllByText('1 Fern Way, Dover DE').length).toBeGreaterThan(0);
    const connects = within(tool).getByRole('region', { name: 'How it connects' });
    expect(within(connects).getByRole('link', { name: /1 Fern Way/ }).getAttribute('href')).toBe('/library/tools?tool=address%3Aas1');
    expect(within(tool).getAllByText('Connected').length).toBeGreaterThan(0);
    expect(within(tool).getByText('TX, Private')).toBeTruthy();
    expect(within(tool).getByText('Ordered')).toBeTruthy();
    expect(within(tool).getByText('County office')).toBeTruthy();
    expect(within(tool).getByText('Filed')).toBeTruthy();
  });

  it('lists the letters sent, a private one by its pages, and stops one before it is mailed @s869', async () => {
    const sent = serve();
    openAddress([
      { id: 'L1', status: 'mailed', can_cancel: false, created_at: '2026-11-16T00:00:00Z', redacted: false, recipient: { name: 'Sam Sample', city: 'Dover' }, page_count: 2 },
      { id: 'L2', status: 'waiting_approval', can_cancel: true, created_at: '2026-11-18T00:00:00Z', redacted: true, recipient: {}, page_count: 1 },
    ]);
    expect(await screen.findByText('Sam Sample, Dover')).toBeTruthy();
    expect(screen.getByText('Mailed')).toBeTruthy();
    const waiting = screen.getByText('A letter, 1 page').closest('.row') as HTMLElement;
    expect(within(waiting).getByText('Waits on approval')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Stop this letter' })).toHaveLength(1);
    fireEvent.click(within(waiting).getByRole('button', { name: 'Stop this letter' }));
    expect(await within(waiting).findByText('Stopped')).toBeTruthy();
    expect(writes(sent)).toEqual([{ url: '/api/v2/letters/L2/cancel', method: 'POST', body: {} }]);
  });
});

describe('the tools’ settings', () => {
  it('Email, number and letter settings are in the Tools head’s ⋯, each opening its tool @s871', async () => {
    serve();
    queryClient.setQueryData(toolsQuery.queryKey, { rows: [indexRow({ id: 'api:t1', name: 'Sample API' })] } as never);
    const router = at('/library/tools', <ToolsPage search={{}} />);
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Email settings' })).toBeTruthy();
    expect(screen.getByRole('menuitem', { name: 'Number requests' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Letter settings' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tool: 'address' }));
    await act(async () => void (await run('tools.open', { tool: 'mailbox' }, { via: 'click' })));
    expect(router.state.location.search).toEqual({ tool: 'mailbox' });
  });
});

/* ---------------------------------------------------------------- the filter, the changes sent, and loading the page */

describe('the Tools filter', () => {
  const row = (id: string, kind: string, needs = false) =>
    ({ id, kind, name: id, needs_you: needs, status: { tone: needs ? 'fail' : 'ok', text: needs ? 'Key expired' : 'Working' } }) as unknown as ToolIndexRow;
  const rows = [row('api:1', 'api'), row('api:2', 'api', true), row('website:1', 'website')];

  it('keeps one kind, or only the tools that need someone', () => {
    expect(narrow(rows, {}).map((r) => r.id)).toEqual(['api:1', 'api:2', 'website:1']);
    expect(narrow(rows, { kind: 'website' }).map((r) => r.id)).toEqual(['website:1']);
    expect(narrow(rows, { needs: 1 }).map((r) => r.id)).toEqual(['api:2']);
    expect(narrow(rows, { kind: 'website', needs: 1 })).toEqual([]);
  });

  it('puts the filter in the address @s908', async () => {
    serve();
    const router = at('/library/tools');
    await act(async () => void (await run('tools.filter', { kind: 'api', needs: true }, { via: 'click' })));
    expect(router.state.location.search).toMatchObject({ kind: 'api', needs: 1 });
  });
});

describe('tool changes', () => {
  it('sends a run setting, a key end date and an edit to the tool', async () => {
    const sent = serve();
    queryClient.setQueryData(apiToolQuery('t1').queryKey, { tool: { id: 't1', name: 'Sample API' }, keys: [{ id: 'k1', expires_on: null }], settings: [] } as never);
    const settled = [
      saveSetting('t1', { name: 'ACCOUNT_ID', value: '1234' }),
      saveSetting('t1', { id: 's1', name: 'REGION', value: 'us' }),
      setKeyExpiry('t1', 'k1', '2027-01-31'),
      editApiTool('t1', { name: 'Sample API 2' }),
    ];
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: Array<{ expires_on: string }> }).keys[0]?.expires_on).toBe('2027-01-31');
    await Promise.all(settled);
    expect(list(sent)).toEqual([
      ['POST', '/api/v2/api-tools/t1/settings', { name: 'ACCOUNT_ID', value: '1234' }],
      ['PATCH', '/api/v2/api-tools/t1/settings/s1', { name: 'REGION', value: 'us' }],
      ['PATCH', '/api/v2/api-tools/t1/keys/k1', { expires_on: '2027-01-31' }],
      ['PATCH', '/api/v2/api-tools/t1', { name: 'Sample API 2' }],
    ]);
  });

  it('keeps a key being removed until the server takes it, and keeps it on a refusal', async () => {
    serve(() => refused(422, 'invalid'));
    const key = { id: 'k1', name: 'Main', preview: '••••abcd' };
    queryClient.setQueryData(apiToolQuery('t1').queryKey, { id: 't1', keys: [key] } as never);
    const done = removeKey('t1', 'k1');
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: unknown[] }).keys).toEqual([key]);
    await expect(done).rejects.toBeTruthy();
    expect((queryClient.getQueryData(apiToolQuery('t1').queryKey) as { keys: unknown[] }).keys).toEqual([key]);
  });

  it('names an MCP server from its address', () => {
    expect(nameFromUrl('https://mcp.acme.com/sse')).toBe('Acme');
    expect(nameFromUrl('not an address')).toBe('');
  });

  it('sends only the login fields that changed, and null to take the authenticator off @s925', async () => {
    const sent = serve();
    await replaceLogin('w1', 'a1', { totp: null });
    expect(writes(sent).map((x) => x.body)).toEqual([{ totp: null }]);
    render(
      <QueryClientProvider client={queryClient}>
        <ReplaceLoginDialog tool="w1" account={{ id: 'a1', username: 'ops@example.com', has_password: true, has_totp: true } as never} onClose={() => {}} />
      </QueryClientProvider>,
    );
    // A stored password is never read back: the field starts empty.
    expect((screen.getByLabelText('Password') as HTMLInputElement).value).toBe('');
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true);
  });
});

describe('Loading a Library page', () => {
  it('opens the Tools list while the selected API tool is still loading', async () => {
    let respond = (_r: Response) => {};
    const toolResponse = new Promise<Response>((resolve) => {
      respond = resolve;
    });
    vi.stubGlobal('fetch', (url: string) =>
      url === '/api/v2/api-tools/t1' ? toolResponse : Promise.resolve(new Response(JSON.stringify({ rows: [] }), { headers: { 'Content-Type': 'application/json' } })),
    );
    let opened = false;
    const loading = loadTools(queryClient, { tool: 'api:t1' }).then(() => {
      opened = true;
    });
    try {
      await waitFor(() => expect(opened).toBe(true));
      expect(queryClient.getQueryData(toolsQuery.queryKey)).toEqual({ rows: [] });
      expect(queryClient.getQueryState(apiToolQuery('t1').queryKey)?.fetchStatus).toBe('fetching');
    } finally {
      respond(new Response(null, { status: 403 }));
      await loading;
    }
  });

  it('lets a failed main read through to the router, and leaves an open tool’s failure in its place', async () => {
    queryClient.clear();
    serve((url) => (url.startsWith('/api/v2/tool-index') ? { rows: [] } : refused(403, 'forbidden')));
    await expect(loadTools(queryClient, { tool: 'api:t1' })).resolves.toBeTruthy();
    await expect(loadDocs(queryClient, {})).rejects.toMatchObject({ status: 403 });
  });
});

describe('the Tools page: requests, connections by vendor, find or connect', () => {
  const accessFor = (id: string, type: 'tool' | 'repository', name: string): FlowRequest => ({
    kind: 'access',
    id: `access:${id}`,
    line: `Clerk asks for ${name}`,
    since: '2026-11-20T09:00:00Z',
    from_id: 'bot:b1',
    access: { id, state: 'open', subject: 'bot:b1', subject_name: 'Clerk', resource_type: type, resource_name: name, level: 'read', why: '' },
  });
  const waiting = (requests: FlowRequest[]) => queryClient.setQueryData(flowQuery.queryKey, { count: requests.length, items: [], requests, task_ids: [] });
  const raygun = [
    indexRow({ id: 'api:rg', name: 'Raygun API', vendor: 'raygun', vendor_name: 'Raygun', delivery: 'proxy', who_total: 1 }),
    indexRow({ id: 'api:rg-dev', name: 'Raygun dev', vendor: 'raygun', vendor_name: 'Raygun', delivery: 'computer' }),
    indexRow({ id: 'website:rg-site', kind: 'website', name: 'raygun.com', vendor: 'raygun', vendor_name: 'Raygun' }),
  ];

  it('answers the tool requests that wait on you beside the list, and leaves a repository request to Flow', async () => {
    serve();
    tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
    waiting([accessFor('r1', 'tool', 'Meta Ads'), accessFor('r2', 'repository', 'acme/site')]);
    open('');
    const requests = await screen.findByRole('region', { name: /^Requests/ });
    expect(within(requests).getByText('Clerk asks for Meta Ads')).toBeTruthy();
    expect(within(requests).queryByText(/acme\/site/)).toBeNull();
    cleanup();
    // A phone shows the list first: the requests lead it.
    document.documentElement.dataset.layout = 'cards';
    waiting([accessFor('r1', 'tool', 'Meta Ads')]);
    open('');
    const list = await screen.findByRole('region', { name: /^Requests/ });
    expect(list.closest('.tools-list')).toBeTruthy();
  });

  it('lists a vendor’s tools as one row whose page shows each connection with its delivery and access', async () => {
    const sent = serve((url) =>
      url === '/api/v2/tool-vendors/raygun'
        ? {
            vendor: 'raygun',
            name: 'Raygun',
            rows: [
              { ...raygun[0], access: [{ subject: 'bot:b1', kind: 'bot', name: 'Clerk', level: 'read', initials: 'C', last_used_at: null }] },
              { ...raygun[1], access: [] },
              { ...raygun[2], who: [{ kind: 'bot', id: 'b1', name: 'Clerk', initials: 'C' }] },
            ],
          }
        : undefined,
    );
    tools([...raygun, indexRow({ id: 'api:solo', name: 'Solo API', vendor: 'solo', vendor_name: 'Solo' })]);
    open('');
    expect((await screen.findByText('Raygun')).closest('a')?.getAttribute('href')).toBe('/library/tools?tool=vendor%3Araygun');
    expect(screen.queryByText('Raygun dev')).toBeNull();
    expect(screen.getByText('Solo API')).toBeTruthy(); // a lone tool of its vendor stays itself
    cleanup();
    open('vendor:raygun');
    const vendor = await page('Raygun');
    const api = await within(vendor).findByRole('region', { name: /^Raygun API/ });
    expect(within(api).getByText('Proxy-protected')).toBeTruthy();
    expect(within(api).getByText('Clerk')).toBeTruthy();
    expect(within(within(vendor).getByRole('region', { name: /^Raygun dev/ })).getByText('Direct key')).toBeTruthy();
    expect(within(within(vendor).getByRole('region', { name: /^raygun\.com/ })).getByText('Browser session')).toBeTruthy();
    expect(reads(sent)()).not.toContain('/api/v2/api-tools/rg-dev');
  });

  it('finds a service in the catalog and offers its ways in; Paste a key adds it from the catalog’s entry', async () => {
    const linear = { id: 'linear', name: 'Linear', auth_scheme: 'header:Authorization', site: 'linear.app', mcp_url: 'https://mcp.linear.app/mcp' };
    const stripe = { id: 'stripe', name: 'Stripe', auth_scheme: 'bearer', site: 'stripe.com', mcp_url: '' };
    const sent = serve((url, method) =>
      url === '/api/v2/tool-templates' ? { services: [linear, stripe], templates: [] } : method === 'POST' && url === '/api/v2/api-tools' ? { id: 'linear' } : undefined,
    );
    as('owner');
    tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
    open('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    const find = await screen.findByRole('dialog', { name: 'Find or connect' });
    fireEvent.change(within(find).getByRole('textbox', { name: 'Find a service' }), { target: { value: 'lin' } });
    expect(within(find).queryByRole('button', { name: 'Stripe' })).toBeNull();
    fireEvent.click(within(find).getByRole('button', { name: 'Linear' }));
    const ways = await screen.findByRole('dialog', { name: 'Linear' });
    expect(within(ways).getByRole('button', { name: 'Sign in with Linear' })).toBeTruthy();
    expect(within(ways).getByRole('button', { name: 'Sign in on the website' })).toBeTruthy();
    fireEvent.click(within(ways).getByRole('button', { name: 'Paste a key' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/api-tools', method: 'POST', body: { name: 'Linear', service: 'linear', auth_scheme: 'header:Authorization' } }]));
    cleanup();
    // The website way opens Add website on the catalog's site; the MCP way Add MCP server on the vendor's address.
    serve((url) => (url === '/api/v2/tool-templates' ? { services: [linear], templates: [] } : undefined));
    tools([indexRow({ id: 'api:t1', name: 'Sample API' })]);
    open('');
    fireEvent.click(await screen.findByRole('button', { name: 'Add tool' }));
    fireEvent.click(await within(await screen.findByRole('dialog', { name: 'Find or connect' })).findByRole('button', { name: 'Linear' }));
    fireEvent.click(await within(await screen.findByRole('dialog', { name: 'Linear' })).findByRole('button', { name: 'Sign in on the website' }));
    expect((within(await screen.findByRole('dialog', { name: 'Add website' })).getByLabelText('Site') as HTMLInputElement).value).toBe('linear.app');
  });

  it('lists an Everyone tool a person holds nothing on under Everyone, whose page asks for an agent’s access', async () => {
    const sent = serve((url, method) => (method === 'POST' && url === '/api/v2/access-requests' ? { state: 'open' } : undefined));
    queryClient.setQueryData(['directory'], { actors: ACTORS, byId: Object.fromEntries(ACTORS.map((a) => [a.id, a])) });
    tools([indexRow({ id: 'api:crm', name: 'Acme CRM', requestable: true, delivery: 'proxy', status: { tone: 'idle', text: '' } })]);
    open('');
    expect(await screen.findByRole('button', { name: /^Everyone/ })).toBeTruthy();
    cleanup();
    open('api:crm');
    const tool = await page('Acme CRM');
    expect(within(tool).getByText('Proxy-protected')).toBeTruthy();
    fireEvent.click(within(tool).getByRole('button', { name: 'Request access' }));
    const dialog = await screen.findByRole('dialog', { name: 'Request access to Acme CRM' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Agent' }));
    const clerk = await screen.findByRole('option', { name: 'Clerk' });
    fireEvent.pointerDown(clerk, { pointerType: 'mouse' });
    fireEvent.click(clerk);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Request' }));
    await waitFor(() => expect(writes(sent)).toEqual([{ url: '/api/v2/access-requests', method: 'POST', body: { resource: 'tool:crm', level: 'read', for_bot: 'b1' } }]));
    expect(reads(sent)()).not.toContain('/api/v2/api-tools/crm');
  });
});
