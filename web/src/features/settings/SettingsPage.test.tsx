import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { Agents } from './Agents';
import { accessQuery, computersQuery, limitsQuery, subscriptionsQuery, usageQuery, type AccessView } from './queries';
import { SettingsPage } from './SettingsPage';
import { UsagePage } from './Usage';

vi.mock('@/search', () => ({ SearchDialog: () => null }));
vi.mock('@/tico/dot', () => ({ Dot: () => null, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

type Reply = { status: number; body: unknown };
let sent: { url: string; method: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = await reply(String(url), method);
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
}

const ACCESS: AccessView = {
  owner: { email: 'owner@example.test', person: 'p1' },
  people: [
    { id: 'p1', name: 'Ana Test', email: 'ana@example.test', left: false, owner: true, role: 'admin', sign_in: true },
    { id: 'p2', name: 'Ben Sample', email: 'ben@example.test', left: false, owner: false, role: 'member', sign_in: false },
  ],
  allowed: [],
  allowed_domains: [],
  revision: 1,
  rules: { bot_reports_direct: true },
  you: 'owner',
  invites: {},
  invite_email: true,
};

const FIG = { runs: 0, input_tokens: 0, cached_tokens: 0, cache_write_tokens: 0, output_tokens: 0, est_cost_usd: 0, subscription_equiv_usd: 0, low_estimate_runs: 0, unpriced_runs: 0 };

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  sent = [];
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:p1', email: 'ana@example.test', role: 'owner', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['config'], { company_name: 'Sample Co', owner_email: 'owner@example.test', version: '0.0.1', app_name: 'Tico', public_url: '' });
});
afterEach(() => vi.unstubAllGlobals());

describe('Settings', () => {
  it('lists the sections as links and marks the open one', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const nav = await screen.findByRole('navigation', { name: 'Sections' });
    expect(within(nav).getByRole('link', { name: 'People' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Billing' }).getAttribute('href')).toBe('/settings/billing');
  });

  it('a person row opens that person in the address, with their role to change', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" />, '/settings/people');
    const row = await screen.findByTitle('Ben Sample');
    expect(row.getAttribute('href')).toBe('/settings/people?open=p2');
  });

  it('an opened person shows their role and what can be done to them', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="people" open="p2" />, '/settings/people?open=p2');
    expect(await screen.findByRole('button', { name: 'Send invite again' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy();
    expect(screen.getByTitle('Ben Sample').getAttribute('href')).toBe('/settings/people');
  });

  it('a switch shows at once and is taken back when the server refuses', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'PUT' && url.endsWith('/api/v2/access/rules'))
        return new Promise<Reply>((r) => (refuse = () => r({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners only' } } })));
      if (url.startsWith('/api/v2/access')) return { status: 200, body: ACCESS };
      return { status: 200, body: { pages: {} } };
    });
    queryClient.setQueryData(accessQuery.queryKey, ACCESS);
    show(<SettingsPage section="general" />, '/settings/general');
    const sw = await screen.findByRole('switch', { name: 'Agents send reports to Tico support' });
    expect(sw.getAttribute('aria-checked')).toBe('true');
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Agents send reports to Tico support' }).getAttribute('aria-checked')).toBe('false'));
    expect(sent[0]).toMatchObject({ url: '/api/v2/access/rules', method: 'PUT', body: { bot_reports_direct: false } });
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => refuse());
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Agents send reports to Tico support' }).getAttribute('aria-checked')).toBe('true'));
    expect(await screen.findByText("Couldn't save")).toBeTruthy();
  });

  it('says when a section is for owners and admins only', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden', detail: 'Owners and admins' } } }));
    show(<SettingsPage section="billing" />, '/settings/billing');
    expect(await screen.findByRole('heading', { name: 'Owners and admins only' })).toBeTruthy();
  });

  it('names the page and offers Try again when a section does not load', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'down', detail: 'down' } } }));
    show(<SettingsPage section="history" />, '/settings/history');
    expect(await screen.findByRole('heading', { name: 'Settings did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('says so when there are no API tokens', async () => {
    serve((url) => (url.endsWith('/api/v2/me/tokens') ? { status: 200, body: { tokens: [], can_create: true } } : { status: 200, body: { people: [] } }));
    show(<SettingsPage section="account" />, '/settings/account');
    expect(await screen.findByRole('heading', { name: 'No tokens' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Make token' })).toBeTruthy();
  });
});

describe('Usage', () => {
  it('an agent row opens that agent in the address', async () => {
    serve(() => ({ status: 200, body: { from: '', to: '', totals: FIG, rows: [] } }));
    const one = { ...FIG, bot: 'builder', name: 'Builder', runs: 4, est_cost_usd: 2.5 };
    queryClient.setQueryData(usageQuery('7d', 'bot').queryKey, { from: '2026-11-13', to: '2026-11-19', totals: one, rows: [one] });
    queryClient.setQueryData(usageQuery('7d', 'day').queryKey, { from: '2026-11-13', to: '2026-11-19', totals: one, rows: [] });
    show(<UsagePage search={{}} />, '/company/usage');
    const row = await screen.findByTitle('Builder');
    expect(row.getAttribute('href')).toBe('/company/usage?open=builder');
    expect(screen.getByRole('heading', { level: 1, name: 'Usage' })).toBeTruthy();
  });

  it('a limit row says when an agent is paused', async () => {
    serve(() => ({ status: 200, body: {} }));
    const none = { daily_usd: null, hourly_usd: null, per_30m_usd: null, weekly_usd: null };
    const lim = {
      ...none,
      blocked: 'monthly' as const,
      blocked_by: null,
      monthly_usd: 10,
      month_spent: 10,
      day_spent: null,
      hour_spent: null,
      week_spent: null,
      percent: 100,
      may_edit: true,
      own_monthly_usd: 10,
      own_daily_usd: null,
      own_hourly_usd: null,
      own_per_30m_usd: null,
      own_weekly_usd: null,
      source: {},
    };
    queryClient.setQueryData(usageQuery('7d', 'bot').queryKey, { from: '', to: '', totals: FIG, rows: [] });
    queryClient.setQueryData(limitsQuery.queryKey, {
      bots: { builder: lim },
      default: { ...none, monthly_usd: 50, count_subscription: false },
      may_edit_default: true,
      pools: null,
    });
    show(<UsagePage search={{ view: 'limits' }} />, '/company/usage?view=limits');
    const row = await screen.findByTitle('builder');
    expect(within(row).getByText('Paused')).toBeTruthy();
    expect(row.getAttribute('href')).toBe('/company/usage?view=limits&open=builder');
  });
});

describe('A model sign-in', () => {
  const CODE = 'WDJB-MJHT';
  const PAGE = `https://auth.example/device?user_code=${CODE}`;

  it('keeps the provider’s code and page in the dialog only, never in the query cache', async () => {
    serve((url, method) =>
      method === 'POST' && url.endsWith('/api/v2/model-subscriptions/connect')
        ? { status: 200, body: { id: 'l1', runner_id: 'r1', kind: 'codex_login', state: 'starting' } }
        : url.endsWith('/api/v2/computers/r1/logins/l1')
          ? { status: 200, body: { id: 'l1', runner_id: 'r1', state: 'waiting', url: PAGE, code: CODE, lines: [`Enter ${CODE}`] } }
          : { status: 404, body: { detail: 'not_found' } },
    );
    const kind = { connect: false, help: '', kind: 'codex_login' as const, label: 'Codex', owned: false, runtime: 'codex' as const, secret_label: '' };
    queryClient.setQueryData(subscriptionsQuery.queryKey, { kinds: [kind], subscriptions: [] });
    queryClient.setQueryData(computersQuery.queryKey, []);
    show(<Agents />, '/settings/agents');
    fireEvent.click(await screen.findByRole('button', { name: 'Add' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Codex/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Codex' });
    expect((await within(dialog).findByRole('link', { name: 'Open page' })).getAttribute('href')).toBe(PAGE);
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => [q.queryKey, q.state.data]))).not.toContain(CODE);
  });
});
