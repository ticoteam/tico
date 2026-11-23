import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { run } from '@/actions/run';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { accessQuery, billingQuery, changelogQuery, directorySyncQuery, healthQuery, humansQuery, type AccessView } from './queries';
import { SettingsPage } from './SettingsPage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
let read: string[] = [];
/** Anything `reply` leaves (undefined) answers 200 with {}. */
function serve(reply: (url: string, method: string) => Reply | undefined = () => undefined) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'GET') read.push(String(url));
    else sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = reply(String(url), method) ?? ok({});
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => {}} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const ok = (body: unknown): Reply => ({ status: 200, body });

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const LIST = { can_add: false, can_review: false, current_version: '0.0.1', drafts: [], next_offset: null, unread_count: 0 };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  read = [];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings › Changelog @s1068', () => {
  const entry = (id: string, version: string, more: Record<string, unknown> = {}) => ({
    id,
    version,
    title: `Change ${id}`,
    area: 'tasks',
    bullets: [],
    kind: 'product',
    shipped_at: '2026-03-04T00:00:00Z',
    unread: false,
    ...more,
  });

  it('groups the product updates by version, newest first; a version with unread updates is New and they are read', async () => {
    serve();
    queryClient.setQueryData(changelogQuery.queryKey, {
      ...LIST,
      unread_count: 1,
      entries: [entry('e1', '0.0.2', { unread: true }), entry('e2', '0.0.2', { area: 'fix' }), entry('e3', '0.0.1')],
    } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    const newest = await screen.findByRole('region', { name: '0.0.2' });
    expect(within(newest).getByText('Change e1')).toBeTruthy();
    expect(within(newest).getByText('Fixed')).toBeTruthy();
    expect(within(newest).getByText('New')).toBeTruthy();
    expect(within(newest).getByText('Mar 4')).toBeTruthy();
    const older = screen.getByRole('region', { name: '0.0.1' });
    expect(within(older).getByText('Change e3')).toBeTruthy();
    expect(within(older).queryByText('New')).toBeNull();
    expect(newest.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/changelog/read', method: 'POST', body: { ids: ['e1'] } }));
  });

  it('Load older reads the next page and adds its versions', async () => {
    serve((url) => (url.includes('offset=60') ? ok({ ...LIST, entries: [entry('e9', '0.0.0')] }) : undefined));
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, next_offset: 60, entries: [entry('e1', '0.0.2')] } as never);
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    fireEvent.click(await screen.findByRole('button', { name: 'Load older' }));
    expect(await screen.findByRole('region', { name: '0.0.0' })).toBeTruthy();
    expect(read.some((u) => u.includes('/api/v2/changelog?kind=product&limit=60&offset=60'))).toBe(true);
    expect(screen.queryByRole('button', { name: 'Load older' })).toBeNull();
  });

  it('empty, it is one quiet line; Add update in its head publishes a title and its bullets @s1120', async () => {
    serve();
    queryClient.setQueryData(changelogQuery.queryKey, { ...LIST, can_add: true, entries: [] });
    show(<SettingsPage section="changelog" />, '/settings/changelog');
    const log = await screen.findByRole('region', { name: 'Changelog' });
    expect(log.textContent).toContain('No changes yet');
    expect(document.querySelector('.empty')).toBeNull();
    const add = log.querySelector('.stage-head')?.querySelector('[aria-label="Add update"]') as HTMLElement;
    fireEvent.click(add);
    const dlg = await screen.findByRole('dialog', { name: 'Product update' });
    const publish = within(dlg).getByRole('button', { name: 'Publish' });
    expect(publish.hasAttribute('disabled')).toBe(true);
    fireEvent.change(within(dlg).getByLabelText('Title'), { target: { value: 'Calls on the phone' } });
    fireEvent.change(within(dlg).getByLabelText('Bullets, one a line'), { target: { value: '- Ring on the phone\n- Answer from the lock screen' } });
    fireEvent.click(publish);
    await waitFor(() =>
      expect(sent.at(-1)).toMatchObject({
        url: '/api/changelog',
        method: 'POST',
        body: { title: 'Calls on the phone', bullets: ['Ring on the phone', 'Answer from the lock screen'] },
      }),
    );
    // It cannot be taken back, so only the person publishes one.
    sent = [];
    expect(await run('settings.productUpdate', { title: 'New', bullets: ['A line'] }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(sent).toHaveLength(0);
  });
});

describe('Settings › Health @s1082', () => {
  it('links both credential sources when model sign-ins are rejected', async () => {
    const health = {
      checks: [{
        id: 'models', label: 'Models', status: 'bad', summary: 'Sign-in rejected',
        fixes: [
          { label: 'Open Keys', href: '#/tools/keys?show=credentials' },
          { label: 'Open Computer on Studio', href: '#/bot/alpha/computer' },
        ],
      }],
    };
    serve((url) => url === '/api/v2/health' ? ok(health) : undefined);
    show(<SettingsPage section="health" />, '/settings/health');
    const models = within(await screen.findByTitle('Models'));
    expect(models.getByRole('link', { name: 'Open Keys' }).getAttribute('href')).toBe('/library/tools');
    expect(models.getByRole('link', { name: 'Open Computer on Studio' }).getAttribute('href')).toBe('/team/alpha?tab=computer');
  });

  it('splits Needs a look from Fine with the checked time, links a fix and a stuck agent, and Check now reads again', async () => {
    const HEALTH = {
      checked: new Date().toISOString(),
      attention: 1,
      checks: [
        { id: 'db', label: 'Database', status: 'ok', summary: 'Answers in 12 ms' },
        { id: 'mail', label: 'Mail', status: 'bad', summary: 'Nothing received today', fixes: [{ label: 'Read how', href: 'https://example.test/mail' }] },
      ],
      stuck: [{ bot: 'alpha', name: 'Alpha Agent', reason: 'no_computer', why: 'No computer is online' }],
    };
    serve((url) => (url === '/api/v2/health' ? ok(HEALTH) : undefined));
    queryClient.setQueryData(healthQuery.queryKey, HEALTH as never);
    show(<SettingsPage section="health" />, '/settings/health');
    const bad = await screen.findByRole('region', { name: 'Needs a look' });
    const mail = within(bad).getByTitle('Mail');
    expect(mail.className).toContain('is-bad');
    expect(within(mail).getByText('Broken')).toBeTruthy();
    expect(within(mail).getByRole('link', { name: 'Read how' }).getAttribute('href')).toBe('https://example.test/mail');
    expect(within(bad).getByText(/^Checked /)).toBeTruthy();
    expect(within(within(screen.getByRole('region', { name: 'Fine' })).getByTitle('Database')).getByText('Fine')).toBeTruthy();
    const stuck = screen.getByRole('region', { name: 'Stuck agents' });
    expect(within(stuck).getByRole('link', { name: /Alpha Agent/ }).getAttribute('href')).toBe('/team/alpha');
    read = [];
    fireEvent.click(screen.getByRole('button', { name: 'Check now' }));
    await waitFor(() => expect(read).toContain('/api/v2/health'));
  });
});

describe('Settings › Account', () => {
  it('reads the people list where the server keeps it', async () => {
    const asked: string[] = [];
    vi.stubGlobal('fetch', async (url: string) => {
      asked.push(String(url));
      return new Response(JSON.stringify({ people: [{ id: 'p1', email_digest: true }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    });
    expect(await queryClient.fetchQuery(humansQuery)).toEqual([{ id: 'p1', email_digest: true }]);
    expect(asked).toEqual(['/api/humans']);
  });
});

const ACCESS: AccessView = {
  owner: { email: 'owner@example.test', person: 'p1' },
  people: [{ id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true }],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: {},
  you: 'owner',
  invites: {},
  invite_email: true,
};

const SYNC = {
  source: 'scim' as const,
  filter: { groups: [], org_units: [], domains: [] },
  interval_minutes: 360,
  mass_leave_limit: 10,
  revision: 4,
  last: {},
  confirmed: true,
  credentials: { google: { configured: false, hint: '' }, entra: { configured: false, hint: '' } },
  scim: { enabled: true, created_at: '2026-01-01T00:00:00Z', url: 'https://tico.example.test/scim/v2' },
};

describe('Settings › People › Directory sync', () => {
  it('a new SCIM token shows once, in its dialog, and is kept nowhere else @s812', async () => {
    serve((url, method) =>
      method === 'POST' && url === '/api/v2/directory/scim-token' ? { status: 200, body: { token: 'scim-sample-0000' } } : { status: 200, body: SYNC },
    );
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(directorySyncQuery.queryKey, SYNC);
    show(<SettingsPage section="people" />, '/settings/people');
    fireEvent.click(await screen.findByRole('button', { name: 'Replace token' }));
    expect(await screen.findByText('scim-sample-0000')).toBeTruthy();
    // Not in the query cache, not in a toast, not in the address.
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('scim-sample-0000');
    expect(document.querySelector('.toast')?.textContent ?? '').not.toContain('scim-sample-0000');
    expect(window.location.href).not.toContain('scim-sample-0000');
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByText('scim-sample-0000')).toBeNull());
  });

  it('Sync now previews first, and a first sync applies only once the list is checked @s812', async () => {
    const plan = { hash: 'h1', adds: [{ email: 'new@example.test' }], updates: [], restores: [], leaves: [], protected: [], skipped: [] };
    serve((url) => {
      if (url === '/api/v2/directory/preview') return { status: 200, body: { plan, needs_confirmation: { first: true }, mass_leave_limit: 10 } };
      if (url === '/api/v2/directory/sync') return { status: 200, body: { plan, needs_confirmation: {}, mass_leave_limit: 10, applied: true } };
      return { status: 200, body: SYNC };
    });
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    queryClient.setQueryData(directorySyncQuery.queryKey, SYNC);
    show(<SettingsPage section="people" />, '/settings/people');
    fireEvent.click(await screen.findByRole('button', { name: 'Sync now' }));
    const apply = await screen.findByRole('button', { name: 'Apply' });
    expect(apply.hasAttribute('disabled') || apply.getAttribute('aria-disabled') === 'true').toBe(true);
    fireEvent.click(screen.getByRole('checkbox', { name: 'I checked this list' }));
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(sent.at(-1)).toMatchObject({ url: '/api/v2/directory/sync', body: { confirm: true, plan_hash: 'h1' } }));
  });
});

describe('Settings › Billing @s1081', () => {
  const BILL = {
    state: 'ok',
    month: '2026-11',
    months: ['2026-11', '2026-10'],
    balance_micros: 0,
    min_balance_micros: 0,
    cloud: { plan: 'Team', access: 'frozen', billing_url: 'https://billing.example.test/pay' },
    credits: [],
    usage: [
      { period: '2026-11-03', total_micros: 5_000_000 },
      { period: '2026-11-04', total_micros: 2_000_000 },
    ],
    statement: { opening_micros: 10_000_000, credits_by_source: { grant: 5_000_000 }, debits_by_product: { gateway: 4_000_000 }, expired_micros: 0, closing_micros: 11_000_000 },
  };

  it('sums the month, and links the statement and credit', async () => {
    serve();
    queryClient.setQueryData(billingQuery.queryKey, BILL);
    show(<SettingsPage section="billing" />, '/settings/billing');
    expect(await screen.findByText('$7.00')).toBeTruthy();
    expect(screen.getByText('Out of credit')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href')).toBe('/api/v2/billing/statements/2026-11.csv');
    expect(screen.getByRole('link', { name: 'Add credit' }).getAttribute('href')).toBe('https://billing.example.test/pay');
    expect(screen.getByText('Credit, Grant')).toBeTruthy();
  });

  it('another month reads that month, its total and its statement', async () => {
    serve(() => ok({ ...BILL, month: '2026-10', usage: [], statement: { ...BILL.statement, closing_micros: 1 } }));
    queryClient.setQueryData(billingQuery.queryKey, BILL);
    show(<SettingsPage section="billing" />, '/settings/billing');
    await pick(await screen.findByRole('combobox', { name: 'Month' }), /October 2026/);
    await waitFor(() => expect(read).toContain('/api/v2/billing?month=2026-10'));
    const figures = await screen.findByRole('group', { name: 'Billing' });
    expect(await within(figures).findByText('October 2026', { selector: '.widget-title' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href')).toBe('/api/v2/billing/statements/2026-10.csv');
  });

  it('a company on its own servers adds credit by talking to us', async () => {
    serve();
    queryClient.setQueryData(billingQuery.queryKey, { ...BILL, cloud: null, booking_url: 'https://book.example.test' } as never);
    show(<SettingsPage section="billing" />, '/settings/billing');
    const link = await screen.findByRole('link', { name: 'Add credit' });
    expect(link.getAttribute('href')).toBe('https://book.example.test');
    expect(screen.queryByRole('link', { name: 'Change plan' })).toBeNull();
  });

  it('Billing shows the balance, the credit left and where the plan changes', async () => {
    serve();
    queryClient.setQueryData(billingQuery.queryKey, {
      state: 'ok',
      month: '2026-11',
      balance_micros: 12_000_000,
      min_balance_micros: 0,
      cloud: { plan: 'Team', billing_url: 'https://billing.example.test' },
      credits: [{ label: 'Welcome credit', amount_micros: 20_000_000, left_micros: 12_000_000, expires: null, current: true }],
      usage: [{ period: '2026-11', total_micros: 8_000_000 }],
      statement: { opening_micros: 0, closing_micros: 0, debits_by_product: {} },
      months: ['2026-11'],
    });
    show(<SettingsPage section="billing" />, '/settings/billing');
    expect(await screen.findByTitle('Welcome credit')).toBeTruthy();
    expect(screen.getByText('Team')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Change plan' }).getAttribute('href')).toBe('https://billing.example.test');
  });
});
