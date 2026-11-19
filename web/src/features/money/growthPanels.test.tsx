import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import { GrowthPage } from './Growth';
import type { GrowthCohortMonth, GrowthComputed, GrowthRead } from './queries';
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
    const path = new URL(String(url), 'http://x').pathname.replace('/api/v2', '');
    const r = base[path] ?? missing;
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

const READ: GrowthRead = {
  scope: 'all',
  period: '2026-10',
  headline: { new_customers: { value: 40 }, cost: { value: 2000 }, cac: { value: 50 } },
  channels: [{ slug: 'search', name: 'Search ads', new_customers: 40, cost: 2000, cac: 50 }],
  metrics: [],
  retention: [{ cohort: '2026-06', entered: 40, kept_d30: { value: 0.75, n: 40, status: 'ok' }, kept_d90: { value: 0.5, n: 40, status: 'ok' } }],
};
const MONTH: GrowthCohortMonth = {
  id: 'c1',
  channel: 'search',
  month: '2026-09',
  spend: 1800,
  bot_cost: null,
  new_customers: 36,
  kept_d30: 80,
  kept_d90: null,
  kept_d180: null,
  revenue_month: null,
  margin_pct: null,
  quality: 'actual',
  status: { kept_d90: 'not_yet' },
  origins: {
    spend: { origin: 'synced', source: 'ledger', at: null, conflict: { value: 1700, source: 'books' } },
    new_customers: { origin: 'computed', computed_from: 36, sources: [] },
    kept_d30: { origin: 'typed', by: 'human:p1', quality: 'estimate' },
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

describe('Growth panels @s480 @s481 @s482 @s484 @s486', () => {
  it('a channel lists its months, each opening at its own address', async () => {
    serve({ '/growth/cohorts': ok({ cohorts: [{ ...MONTH, month: '2026-08', new_customers: 30 }, MONTH] }) });
    show('/company/growth?panel=channel:search');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    const sep = await within(panel).findByRole('link', { name: 'September 2026' });
    expect(searchOf(sep.getAttribute('href'))).toEqual({ panel: 'month:search:2026-09' });
    // Newest first.
    const months = within(panel)
      .getAllByRole('link')
      .map((a) => a.textContent);
    expect(months).toEqual(['September 2026', 'August 2026']);
  });

  it('a channel with no months says so', async () => {
    serve({ '/growth/cohorts': ok({ cohorts: [] }) });
    show('/company/growth?panel=channel:search');
    expect(await screen.findByText('No months yet')).toBeTruthy();
  });

  it('months that fail offer Try again', async () => {
    serve({ '/growth/cohorts': broken });
    show('/company/growth?panel=channel:search');
    expect(await screen.findByRole('heading', { name: 'Months did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a month shows each number and where it came from, and closes back to its channel', async () => {
    directory(agent('bot:gm1', 'growth-manager', 'Growth Manager'));
    queryClient.setQueryData(['directory'], (d: { actors: Actor[]; byId: Record<string, Actor> }) => ({
      ...d,
      byId: { ...d.byId, 'human:p1': { id: 'human:p1', kind: 'person', name: 'Sam Rivera' } },
    }));
    serve({ '/growth/cohorts': ok({ cohorts: [MONTH] }) });
    const router = show('/company/growth?panel=month:search:2026-09');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(await within(panel).findByText('new customers, September 2026')).toBeTruthy();
    expect(within(panel).getByText('ledger; books says $1,700')).toBeTruthy();
    expect(within(panel).getByText('From 36 customers')).toBeTruthy();
    expect(within(panel).getByText('Typed by Sam Rivera, estimate')).toBeTruthy();
    expect(within(panel).getByText('not yet')).toBeTruthy();
    expect(within(panel).queryByText('Agent cost')).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ panel: 'channel:search' }));
  });

  it('a month with nothing in it says so', async () => {
    serve({ '/growth/cohorts': ok({ cohorts: [] }) });
    show('/company/growth?panel=month:search:2026-09');
    expect(await screen.findByText('Nothing for this month')).toBeTruthy();
  });

  it('a month that fails offers Try again', async () => {
    serve({ '/growth/cohorts': broken });
    show('/company/growth?panel=month:search:2026-09');
    expect(await screen.findByRole('heading', { name: 'This month did not load' })).toBeTruthy();
  });

  it('a retention figure opens the customers behind it: still and churned', async () => {
    const computed: GrowthComputed = {
      keys_visible: true,
      cohorts: [
        {
          period: '2026-06',
          kept_d90: {
            value: 50,
            status: 'ok',
            count: 2,
            denominator: 4,
            customers: {
              still: ['cus_a', 'cus_b'],
              churned: [
                { key: 'cus_c', churned: '2026-08-02' },
                { key: 'cus_d', churned: null },
              ],
            },
          },
        },
      ],
    };
    serve({ '/growth/computed': ok(computed) });
    const router = show('/company/growth');
    const cell = await screen.findByRole('link', { name: '50%' });
    expect(searchOf(cell.getAttribute('href'))).toEqual({ panel: 'customers:2026-06:90' });
    fireEvent.click(cell);
    await waitFor(() => expect(router.state.location.search).toEqual({ panel: 'customers:2026-06:90' }));
    const panel = await screen.findByRole('complementary', { name: 'June 2026, day 90' });
    expect(await within(panel).findByText('2 of 4 kept')).toBeTruthy();
    expect(within(panel).getByText('Still customers')).toBeTruthy();
    expect(within(panel).getByText('cus_b')).toBeTruthy();
    expect(within(panel).getByText('cus_c')).toBeTruthy();
  });

  it('customers with no list say so, and a failed read offers Try again', async () => {
    serve({ '/growth/computed': ok({ keys_visible: false, cohorts: [] }) });
    show('/company/growth?panel=customers:2026-06:90');
    expect(await screen.findByText('No customer list')).toBeTruthy();
  });

  it('customers that fail to load offer Try again', async () => {
    serve({ '/growth/computed': broken });
    show('/company/growth?panel=customers:2026-06:90');
    expect(await screen.findByRole('heading', { name: 'Customers did not load' })).toBeTruthy();
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
  it('a source opens on Sources only; months and customers on the board; anything else is dropped', () => {
    expect(validateGrowthSearch({ tab: 'sources', panel: 'source:src1' })).toEqual({ tab: 'sources', panel: 'source:src1' });
    expect(validateGrowthSearch({ panel: 'source:src1' })).toEqual({});
    expect(validateGrowthSearch({ tab: 'sources', panel: 'channel:search' })).toEqual({ tab: 'sources' });
    expect(validateGrowthSearch({ panel: 'month:search:2026-09' })).toEqual({ panel: 'month:search:2026-09' });
    expect(validateGrowthSearch({ panel: 'month:search:2026-13' })).toEqual({});
    expect(validateGrowthSearch({ panel: 'customers:2026-06:90' })).toEqual({ panel: 'customers:2026-06:90' });
    expect(validateGrowthSearch({ panel: 'customers:2026-06:45' })).toEqual({});
  });
});
