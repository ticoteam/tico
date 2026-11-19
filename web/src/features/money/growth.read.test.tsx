import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import { GrowthPage } from './Growth';
import type { GrowthFig, GrowthRead } from './queries';
import { validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* The Growth board on the one growth read, one test per company shape. Made-up figures and names only. */

type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const posts: { path: string; body: Record<string, unknown> }[] = [];

function serve(read: GrowthRead, segments: { slug: string; name: string }[] = [], owners: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const path = new URL(String(url), 'http://x').pathname.replace('/api/v2', '');
    if ((init?.method ?? 'GET') === 'POST') {
      posts.push({ path, body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown> });
      return json({});
    }
    const reads: Record<string, Reply> = { '/growth/read': ok(read), '/growth/segments': ok({ segments }), '/page-owners': ok({ pages: owners }) };
    const r = reads[path] ?? { status: 404, body: {} };
    return json(r.body, r.status);
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

const f = (value: number | null, more: Partial<GrowthFig> = {}): GrowthFig => ({ value, ...more });
const base = (over: Partial<GrowthRead>): GrowthRead => ({
  scope: 'all',
  period: '2026-10',
  as_of: '2026-11-02',
  headline: {
    signups: f(0),
    activated_rate: f(null, { status: 'nobody' }),
    paid_rate: f(null, { status: 'nobody' }),
    new_customers: f(0),
    cost: f(null, { status: 'no_spend' }),
    cac: f(null, { status: 'no_spend' }),
    kept: f(null, { status: 'nobody' }),
  },
  channels: [],
  health: { basis: 'customers', unknown_share: null, stale_sources: [], reconciled: 'not_reconciled' },
  metrics: [],
  more: [],
  ...over,
});
const kpi = (id: string, name: string, unit: string, value: number, estimate = false) =>
  ({ id, name, unit, owner: null, channel: null, value, period_end: '2026-10-31', estimate, vs_prior: null }) as GrowthRead['metrics'][number];

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  posts.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  const gm: Actor = { id: 'bot:gm1', kind: 'agent', name: 'Growth Manager', slug: 'growth-manager', hue: 200 };
  queryClient.setQueryData(['directory'], { actors: [gm], byId: { 'bot:gm1': gm, gm1: gm, 'growth-manager': gm, 'bot:growth-manager': gm } });
});
afterEach(() => vi.unstubAllGlobals());

describe('Growth on the one read @s400', () => {
  it('two revenue segments with paid channels: a big unknown share and a check that is off are said out loud', async () => {
    const read = base({
      headline: {
        signups: f(900, { verdict: 'up', vs_3mo: 800 }),
        activated_rate: f(0.4, { cohort: '2026-10', n: 900 }),
        paid_rate: f(0.2, { cohort: '2026-10', n: 900 }),
        new_customers: f(160, { verdict: 'flat', vs_3mo: 155 }),
        cost: f(31200),
        cac: f(195, { verdict: 'down' }),
        kept: f(0.62, { cohort: '2026-07', n: 140, window: 'd90' }),
        ltv: f(900),
        ltv_cac: f(4.6),
        payback_months: f(3.1),
      },
      channels: [
        {
          slug: 'search',
          name: 'Search ads',
          owner: 'bot:gm1',
          signups: 500,
          new_customers: 80,
          new_share: 0.5,
          cost: 20000,
          cac: 250,
          cost_share: 0.64,
          budget: 18000,
          over_budget: 2000,
        },
        { slug: 'social', name: 'Social ads', signups: 300, new_customers: 30, new_share: 0.19, cost: 11200, cac: 373.33, cost_share: 0.36, budget: 12000 },
        { slug: 'friends', name: 'Friends', signups: 0, new_customers: 0, new_share: 0, cost: null, cac: null, status: 'no_spend' },
        { slug: 'unknown', signups: 100, new_customers: 50, new_share: 0.31 },
      ],
      health: {
        basis: 'customers',
        unknown_share: 0.31,
        stale_sources: [],
        reconciled: 'off',
        checks: [
          { figure: 'cost', growth: 31200, outside: 33900, kpi: 'Finance', agrees: false },
          { figure: 'new_customers', growth: 160, outside: 160, kpi: 'Orders', agrees: true },
        ],
      },
    });
    const segments = [
      { slug: 'alpha', name: 'Alpha' },
      { slug: 'beta', name: 'Beta' },
    ];
    serve(read, segments, { growth: { bot: 'growth-manager', choice: 'default' } });
    const router = show('/company/growth?month=2026-10');

    const loud = (await screen.findByText('31% of new customers have no known channel')).closest('[role="status"]') as HTMLElement;
    expect(within(loud).getByText('Cost is off from Finance: 31,200 here, 33,900 there')).toBeTruthy();
    expect(screen.getByText('Reconciled with Orders')).toBeTruthy();

    // Revenue tiles: CAC and LTV to CAC, payback under it, kept with its cohort.
    const tiles = screen.getByRole('group', { name: 'Headline' });
    expect(within(tiles).getByText('LTV to CAC')).toBeTruthy();
    expect(within(tiles).getByText('payback 3.1 months')).toBeTruthy();
    expect(within(tiles).getByText('Jul cohort of 140')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Alpha' })).toBeTruthy();

    // Where to bet: the read's ranking, Unknown last and not a link, no spend in a word, what is over budget.
    const table = screen.getByRole('table', { name: 'Channels' });
    const names = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((r) => r.querySelector('td')?.textContent);
    expect(names).toEqual(['Search ads', 'Social ads', 'Friends', 'Unknown']);
    expect(within(table).getByText('$2,000 over')).toBeTruthy();
    expect(within(table).getByText('no spend')).toBeTruthy();
    expect(within(table).queryByRole('link', { name: 'Unknown' })).toBeNull();

    // The ask line sends the words to the Growth Manager, led by the scope and month, and opens its chat.
    fireEvent.change(screen.getByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-10', chat: 'agent:gm1' }));
    expect(posts).toEqual([{ path: '/chat/gm1', body: expect.objectContaining({ text: 'All, October 2026: Why is search over?' }) as unknown }]);
  });

  it('no segments, no spend, all zeros: the KPIs lead and the funnel folds below', async () => {
    serve(base({ metrics: [kpi('k1', 'Waitlist', 'count', 340), kpi('k2', 'Demo calls', 'count', 12, true), kpi('k3', 'Pilot pipeline', 'usd', 45000)] }));
    show('/company/growth?month=2026-10');
    const kpis = await screen.findByRole('group', { name: 'KPIs' });
    expect(within(kpis).getByText('Waitlist')).toBeTruthy();
    expect(within(kpis).getByText('340')).toBeTruthy();
    expect(within(kpis).getByText('estimate')).toBeTruthy();
    expect(within(kpis).getByText('$45,000')).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Headline' })).toBeNull();
    // The funnel is folded, and nothing says $0 for a cost per customer with no spend.
    expect(screen.getByRole('button', { name: /Funnel/ }).getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByText('$0')).toBeNull();
    // No segments, no scope tabs.
    expect(screen.queryByRole('link', { name: 'All' })).toBeNull();
  });

  it('one retention segment with no spend: no CAC or LTV, and kept says when it can first be read', async () => {
    serve(
      base({
        scope: 'segment:members',
        segment: { slug: 'members', name: 'Members', rule: null, value_kind: 'retention' },
        headline: {
          signups: f(180),
          activated_rate: f(0.55, { cohort: '2026-10', n: 180 }),
          paid_rate: f(null, { status: 'not_supplied' }),
          new_customers: f(null, { status: 'not_supplied' }),
          cost: f(null, { status: 'no_spend' }),
          cac: f(null, { status: 'no_spend' }),
          kept: f(null, { status: 'not_yet' }),
          value: { kept_d30: f(null, { status: 'not_yet' }), cost_per_retained: f(null, { status: 'no_spend' }) },
        },
        retention: [
          { cohort: '2026-10', entered: 180, kept_d30: { value: null, n: 0, status: 'not_yet' } },
          { cohort: '2026-09', entered: 180, kept_d30: { value: null, n: 12, status: 'too_few' } },
        ],
      }),
      [{ slug: 'members', name: 'Members' }],
    );
    show('/company/growth?segment=members&month=2026-10');
    const tiles = await screen.findByRole('group', { name: 'Headline' });
    expect(within(tiles).getByText('Sign-ups')).toBeTruthy();
    expect(within(tiles).getByText('Activated')).toBeTruthy();
    expect(within(tiles).getByText('not yet')).toBeTruthy();
    expect(within(tiles).queryByText('Cost')).toBeNull();
    expect(screen.queryByText('CAC')).toBeNull();
    expect(screen.queryByText(/LTV/)).toBeNull();
    const retention = screen.getByRole('table', { name: 'Retention' });
    expect(within(retention).getByText('first read Nov 30')).toBeTruthy();
    expect(within(retention).getByText('12 of 180 old enough')).toBeTruthy();
  });
});
