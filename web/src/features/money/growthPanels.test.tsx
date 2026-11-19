import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import { GrowthPage } from './Growth';
import type { GrowthRead } from './queries';
import { validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// Made-up figures, keys and names only.
type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const missing: Reply = { status: 404, body: {} };
const broken: Reply = { status: 500, body: {} };

/** Growth's reads, each replaceable; anything else is not found. */
function serve(over: Record<string, Reply> = {}) {
  const base: Record<string, Reply> = {
    '/growth/segments': ok({ segments: [] }),
    '/growth/sources': ok({ sources: [{ id: 'src1', name: 'Ledger', system: 'ledger', health: 'fresh', fills: ['spend'], truth_for: ['spend'] }] }),
    '/growth/read': ok(READ),
    '/page-owners': ok({ pages: {} }),
    ...over,
  };
  vi.stubGlobal('fetch', async (url: string) => {
    const u = new URL(String(url), 'http://x');
    const path = u.pathname.replace('/api/v2', '');
    const scope = u.searchParams.get('scope');
    const r = (scope && base[`${path}?scope=${scope}`]) || base[path] || missing;
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(at: string) {
  const Growth = () => <GrowthPage search={validateGrowthSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Growth }), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const searchOf = (href: string | null) => Object.fromEntries(new URL(href ?? '', 'http://x').searchParams);

const fig = (value: number | null, more: Partial<GrowthRead['headline']['cac']> = {}) => ({ value, ...more });
const READ: GrowthRead = {
  scope: 'all',
  period: '2026-10',
  as_of: '2026-11-02',
  headline: {
    signups: fig(120),
    activated_rate: fig(0.5),
    paid_rate: fig(0.3),
    new_customers: fig(40),
    cost: fig(2000),
    cac: fig(50),
    kept: fig(0.75, { cohort: '2026-06', n: 40 }),
    ltv: fig(300),
    ltv_cac: fig(6),
    payback_months: fig(2),
  },
  channels: [{ slug: 'search', name: 'Search ads', signups: 120, new_customers: 40, new_share: 1, cost: 2000, cac: 50, cost_share: 1, budget: 2500 }],
  health: { basis: 'customers', unknown_share: 0, stale_sources: [], reconciled: 'not_reconciled' },
  metrics: [],
  retention: [{ cohort: '2026-06', entered: 40, kept_d30: { value: 0.75, n: 40, status: 'ok' }, kept_d90: { value: 0.5, n: 40, status: 'ok' } }],
  more: [],
};
const point = (period: string, n: number | null) => ({ period, signups: null, new_customers: n, cost: n && n * 25, cac: n && 25, kept: null });
const CHANNEL: GrowthRead = {
  ...READ,
  scope: 'channel:search',
  series: [point('2026-08', 9), point('2026-09', 12), point('2026-10', null)],
  campaigns: {
    search: [
      { campaign: 'Spring beds', new_customers: 30, cost: 1500, cac: 50 },
      { campaign: 'none', new_customers: 10, cost: 500, cac: 50 },
    ],
  },
};

const agent = (id: string, slug: string, name: string): Actor => ({ id, kind: 'agent', name, slug, hue: 200 });
function directory(...actors: Actor[]) {
  const byId: Record<string, Actor> = {};
  for (const a of actors) Object.assign(byId, { [a.id]: a, [a.id.slice(4)]: a, [a.slug!]: a, [`bot:${a.slug}`]: a });
  queryClient.setQueryData(['directory'], { actors, byId });
}

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  directory();
});
afterEach(() => vi.unstubAllGlobals());

describe('Growth panels @s480 @s484 @s486', () => {
  it('a channel opens beside: its budget and spend, its months newest first, its campaigns by name', async () => {
    serve({ '/growth/read?scope=channel:search': ok(CHANNEL) });
    const router = show('/company/growth?month=2026-10');
    const link = await screen.findByRole('link', { name: 'Search ads' });
    expect(searchOf(link.getAttribute('href'))).toEqual({ month: '2026-10', panel: 'channel:search' });
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-10', panel: 'channel:search' }));
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(await within(panel).findByText('$2,500')).toBeTruthy();
    const rows = [...panel.querySelectorAll('.prow .row-title')].map((r) => r.textContent);
    expect(rows).toEqual(['September 2026', 'August 2026', 'Spring beds', 'No campaign']);
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-10' }));
  });

  it('a channel with no months says so', async () => {
    serve({ '/growth/read?scope=channel:search': ok({ ...CHANNEL, series: [], campaigns: {} }) });
    show('/company/growth?panel=channel:search');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(await within(panel).findByText('No months yet')).toBeTruthy();
  });

  it('a channel that fails offers Try again', async () => {
    serve({ '/growth/read?scope=channel:search': broken });
    show('/company/growth?panel=channel:search');
    expect(await screen.findByRole('heading', { name: 'This channel did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a source opens beside Sources: what it is the truth for, how agents reach it, its last sync', async () => {
    serve({
      '/growth/sources': ok({
        sources: [
          {
            id: 'src1',
            name: 'Ledger',
            health: 'failing',
            health_since: '2026-10-01',
            last_run_at: '2026-10-03T10:00:00Z',
            last_error: 'Sign-in expired',
            fills: ['spend'],
            truth_for: ['spend', 'new_customers'],
            reach_kind: 'url',
            reach_ref: 'https://ledger.example.com/api',
            may: 'read,write',
          },
          { id: 'src2', name: 'Sheet', health: 'not_synced', truth_for: 'revenue' },
        ],
      }),
    });
    const router = show('/company/growth?tab=sources');
    const link = await screen.findByRole('link', { name: 'Ledger' });
    expect(searchOf(link.getAttribute('href'))).toEqual({ tab: 'sources', panel: 'source:src1' });
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: 'sources', panel: 'source:src1' }));
    const panel = await screen.findByRole('complementary', { name: 'Ledger' });
    expect(within(panel).getByText('Spend, new customers')).toBeTruthy();
    expect(within(panel).getByText('Address, ledger.example.com')).toBeTruthy();
    expect(within(panel).getByText('Read, Write notes')).toBeTruthy();
    expect(within(panel).getByText('Failing')).toBeTruthy();
    expect(within(panel).getByText('Sign-in expired')).toBeTruthy();
  });

  it('a source says which other source claims the same number, and only then', async () => {
    serve({
      '/growth/sources': ok({
        sources: [
          { id: 'src1', name: 'Ledger', health: 'fresh', truth_for: ['spend'] },
          { id: 'src2', name: 'Books', health: 'fresh', truth_for: 'Spend' },
          { id: 'src3', name: 'Sheet', health: 'fresh', truth_for: 'revenue' },
        ],
        conflicts: ['spend'],
      }),
    });
    show('/company/growth?tab=sources&panel=source:src1');
    const panel = await screen.findByRole('complementary', { name: 'Ledger' });
    expect(within(panel).getByText('Also claimed by')).toBeTruthy();
    expect(within(panel).getByText('Books')).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Sheet' }));
    const sheet = await screen.findByRole('complementary', { name: 'Sheet' });
    expect(within(sheet).queryByText('Also claimed by')).toBeNull();
  });

  it('a source never synced has no last sync', async () => {
    serve({ '/growth/sources': ok({ sources: [{ id: 'src2', name: 'Sheet', health: 'not_synced', truth_for: 'revenue' }] }) });
    show('/company/growth?tab=sources&panel=source:src2');
    const panel = await screen.findByRole('complementary', { name: 'Sheet' });
    expect(within(panel).getByText('Revenue')).toBeTruthy();
    expect(within(panel).queryByText('Last sync')).toBeNull();
  });
});

describe('Growth Manager entry @s479 @s483 @s485', () => {
  it('its face is in the head of the board and of Sources, and opens its chat beside the page', async () => {
    directory(agent('bot:gm1', 'growth-manager', 'Growth Manager'), agent('bot:gm0', 'general-manager', 'General Manager'));
    serve({ '/page-owners': ok({ pages: { growth: { bot: 'growth-manager', choice: 'default' } } }) });
    const router = show('/company/growth?month=2026-10');
    fireEvent.click(await screen.findByRole('button', { name: 'Chat with Growth Manager' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-10', chat: 'agent:gm1' }));
    expect(screen.getByRole('button', { name: 'Chat with Growth Manager' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('is on Sources too', async () => {
    directory(agent('bot:gm1', 'growth-manager', 'Growth Manager'));
    serve({ '/page-owners': ok({ pages: { growth: { bot: 'bot:gm1', choice: 'bot:gm1' } } }) });
    show('/company/growth?tab=sources');
    expect(await screen.findByRole('button', { name: 'Chat with Growth Manager' })).toBeTruthy();
  });

  it('is absent when only the General Manager stands in', async () => {
    directory(agent('bot:gm0', 'general-manager', 'General Manager'));
    serve({ '/page-owners': ok({ pages: { growth: { bot: 'general-manager', choice: 'default' } } }) });
    show('/company/growth');
    await screen.findByRole('link', { name: 'Search ads' });
    await waitFor(() => expect(queryClient.getQueryData(['settings', 'page-owners'])).toBeTruthy());
    expect(screen.queryByRole('button', { name: /^Chat with/ })).toBeNull();
  });

  it('is absent when the Growth page has no owner', async () => {
    serve({ '/page-owners': ok({ pages: { growth: { bot: null, choice: 'none' } } }) });
    show('/company/growth?tab=sources');
    await screen.findByRole('link', { name: 'Ledger' });
    await waitFor(() => expect(queryClient.getQueryData(['settings', 'page-owners'])).toBeTruthy());
    expect(screen.queryByRole('button', { name: /^Chat with/ })).toBeNull();
  });
});

describe('Growth panel addresses', () => {
  it('a source opens on Sources only, a channel on the board; anything else is dropped', () => {
    expect(validateGrowthSearch({ tab: 'sources', panel: 'source:src1' })).toEqual({ tab: 'sources', panel: 'source:src1' });
    expect(validateGrowthSearch({ panel: 'source:src1' })).toEqual({});
    expect(validateGrowthSearch({ tab: 'sources', panel: 'channel:search' })).toEqual({ tab: 'sources' });
    expect(validateGrowthSearch({ panel: 'channel:search' })).toEqual({ panel: 'channel:search' });
    expect(validateGrowthSearch({ panel: 'month:search:2026-09' })).toEqual({});
    expect(validateGrowthSearch({ panel: 'customers:2026-06:90' })).toEqual({});
  });
});
