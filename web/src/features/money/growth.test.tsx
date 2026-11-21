import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import { GrowthPage } from './Growth';
import { firstRead, ymOf } from './model';
import type { GrowthFig, GrowthRead } from './queries';
import { validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* The Growth board on the one growth read, and a channel beside it. Made-up figures, keys and names only. */
type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const missing: Reply = { status: 404, body: {} };
const broken: Reply = { status: 500, body: {} };

const posts: { path: string; body: Record<string, unknown> }[] = [];
/** What a message to an agent's DM answers: the message and the thread it went to, or a hold. */
let dmReply: Record<string, unknown> | Promise<Record<string, unknown>> = { id: 'm1', conversation_id: 'dm-gm1', thread_id: 'dm-gm1' };

/** Growth's reads, each replaceable (`path?scope=` for one scope); anything else is not found. Every POST is kept. */
function serve(over: Record<string, Reply> = {}, urls: URL[] = []) {
  const base: Record<string, Reply> = {
    '/growth/segments': ok({ segments: [] }),
    '/growth/read': ok(READ),
    '/page-owners': ok({ pages: {} }),
    ...over,
  };
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = new URL(String(url), 'http://x');
    const path = u.pathname.replace('/api/v2', '');
    if ((init?.method ?? 'GET') === 'POST') {
      posts.push({ path, body: JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown> });
      // a message to an agent's DM answers with the message and the thread it went to
      const sent = path === '/threads/dm/messages' ? await dmReply : {};
      return new Response(JSON.stringify(sent), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    urls.push(u);
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

const fig = (value: number | null, more: Partial<GrowthFig> = {}): GrowthFig => ({ value, ...more });
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

/** A read with nothing in it yet: zeros, no spend, nobody to count. */
const empty = (over: Partial<GrowthRead>): GrowthRead => ({
  scope: 'all',
  period: '2026-10',
  as_of: '2026-11-02',
  headline: {
    signups: fig(0),
    activated_rate: fig(null, { status: 'nobody' }),
    paid_rate: fig(null, { status: 'nobody' }),
    new_customers: fig(0),
    cost: fig(null, { status: 'no_spend' }),
    cac: fig(null, { status: 'no_spend' }),
    kept: fig(null, { status: 'nobody' }),
  },
  channels: [],
  health: { basis: 'customers', unknown_share: null, stale_sources: [], reconciled: 'not_reconciled' },
  metrics: [],
  more: [],
  ...over,
});
const kpi = (id: string, name: string, unit: string, value: number, estimate = false) =>
  ({ id, name, unit, owner: null, channel: null, value, period_end: '2026-10-31', estimate, vs_prior: null }) as GrowthRead['metrics'][number];
const heads = (table: HTMLElement) => within(table).getAllByRole('columnheader').map((h) => h.textContent);
const GM = () => agent('bot:gm1', 'growth-manager', 'Growth Manager');
const OWNED = { '/page-owners': ok({ pages: { growth: { bot: 'growth-manager', choice: 'default' } } }) };

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
  posts.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  directory();
});
afterEach(() => {
  delete document.documentElement.dataset.layout;
  vi.unstubAllGlobals();
});

describe('Growth on the one read @s400 @s444', () => {
  it('two revenue segments with paid channels: a big unknown share and a check that is off are said out loud', async () => {
    const read = empty({
      headline: {
        signups: fig(900, { verdict: 'up', vs_3mo: 800 }),
        activated_rate: fig(0.4, { cohort: '2026-10', n: 900 }),
        paid_rate: fig(0.2, { cohort: '2026-10', n: 900 }),
        new_customers: fig(160, { verdict: 'flat', vs_3mo: 155 }),
        cost: fig(31200),
        cac: fig(195, { verdict: 'down' }),
        kept: fig(0.62, { cohort: '2026-07', n: 140, window: 'd90' }),
        ltv: fig(900),
        ltv_cac: fig(4.6),
        payback_months: fig(3.1),
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
    directory(GM());
    serve({ '/growth/read': ok(read), '/growth/segments': ok({ segments }), ...OWNED });
    const router = show('/company/growth?month=2026-10');

    const loud = (await screen.findByText('31% of new customers have no known channel')).closest('[role="status"]') as HTMLElement;
    expect(within(loud).getByText('Cost is off from Finance: 31,200 here, 33,900 there')).toBeTruthy();
    expect(screen.getByText('Reconciled with Orders')).toBeTruthy();

    // Revenue tiles: CAC and LTV to CAC, payback under it, kept with its cohort.
    const tiles = screen.getByRole('group', { name: 'Headline' });
    expect(within(tiles).getByText('LTV to CAC')).toBeTruthy();
    expect(within(tiles).getByText('3.1 mo payback')).toBeTruthy();
    expect(within(tiles).getByText('Jul cohort of 140')).toBeTruthy();
    // the scope tabs are not awaited with the read
    expect(await screen.findByRole('link', { name: 'Alpha' })).toBeTruthy();

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

    // The ask line sends the words to the reader's DM with the Growth Manager, led by the scope and month, and the
    // thread they went to, as the server answered it, opens beside (#4882).
    fireEvent.change(screen.getByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-10', chat: 'thread:dm-gm1' }));
    expect(posts).toEqual([
      { path: '/threads/dm/messages', body: { text: 'All, October 2026: Why is search over?', to_dm: 'bot:gm1', refs: { from_page: { page: 'growth', label: 'Growth' } } } },
    ]);
  });

  it("leads with the segment as the board's read names it, before the segment list has loaded @4882-S17", async () => {
    directory(GM());
    const alpha = { ...READ, scope: 'segment:alpha', segment: { slug: 'alpha', name: 'Alpha', rule: null, value_kind: 'revenue' as const } };
    serve({ ...OWNED, '/growth/read?scope=segment:alpha': ok(alpha), '/growth/segments': ok({ segments: [] }) });
    show('/company/growth?month=2026-10&segment=alpha');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toMatchObject({ text: 'Alpha, October 2026: Why is search over?' });
  });

  it('says the channel open in its panel as a pointer, with the segment and month read before anything is awaited @4882-S14', async () => {
    directory(GM());
    serve(OWNED);
    show('/company/growth?month=2026-10&panel=channel:search');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toMatchObject({
      text: 'All, October 2026: Why is search over?',
      refs: { from_page: { page: 'growth', label: 'Growth', item: { kind: 'channel', id: 'search' } } },
    });
  });

  it('opens no chat when the person moved to another month before the answer came @4882-S8', async () => {
    let answer!: (r: Record<string, unknown>) => void;
    dmReply = new Promise((r) => (answer = r));
    try {
      directory(GM());
      serve(OWNED);
      const router = show('/company/growth?month=2026-10');
      fireEvent.change(await screen.findByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send' }));
      await waitFor(() => expect(posts).toHaveLength(1));
      act(() => void router.navigate({ to: '.', search: { month: '2026-09' } } as never));
      await waitFor(() => expect(router.state.location.search).toEqual({ month: '2026-09' }));
      await act(async () => answer({ id: 'm1', conversation_id: 'dm-gm1', thread_id: 'dm-gm1' }));
      await waitFor(() => expect((screen.getByRole('textbox', { name: 'Ask the Growth Manager' }) as HTMLInputElement).value).toBe(''));
      expect(router.state.location.search).toEqual({ month: '2026-09' });
    } finally {
      dmReply = { id: 'm1', conversation_id: 'dm-gm1', thread_id: 'dm-gm1' };
    }
  });

  it('says a held ask was held, clears the line and opens no chat @4882-S2', async () => {
    dmReply = { hold: { id: 'h1' } };
    try {
      directory(GM());
      serve(OWNED);
      const router = show('/company/growth?month=2026-10');
      fireEvent.change(await screen.findByRole('textbox', { name: 'Ask the Growth Manager' }), { target: { value: 'Why is search over?' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send' }));
      expect(await screen.findByText('Held for a check')).toBeTruthy();
      expect(router.state.location.search).toEqual({ month: '2026-10' });
      expect((screen.getByRole('textbox', { name: 'Ask the Growth Manager' }) as HTMLInputElement).value).toBe('');
    } finally {
      dmReply = { id: 'm1', conversation_id: 'dm-gm1', thread_id: 'dm-gm1' };
    }
  });

  it('no segments, no spend, all zeros: the KPIs lead and the funnel folds below', async () => {
    serve({ '/growth/read': ok(empty({ metrics: [kpi('k1', 'Waitlist', 'count', 340), kpi('k2', 'Demo calls', 'count', 12, true), kpi('k3', 'Pilot pipeline', 'usd', 45000)] })) });
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
    serve({
      '/growth/read': ok(empty({
        scope: 'segment:members',
        segment: { slug: 'members', name: 'Members', rule: null, value_kind: 'retention' },
        headline: {
          signups: fig(180),
          activated_rate: fig(0.55, { cohort: '2026-10', n: 180 }),
          paid_rate: fig(null, { status: 'not_supplied' }),
          new_customers: fig(null, { status: 'not_supplied' }),
          cost: fig(null, { status: 'no_spend' }),
          cac: fig(null, { status: 'no_spend' }),
          kept: fig(null, { status: 'not_yet' }),
          value: { kept_d30: fig(null, { status: 'not_yet' }), cost_per_retained: fig(null, { status: 'no_spend' }) },
        },
        retention: [
          // this month's cohort, whose first read is still to come; last year's was never pushed
          { cohort: ymOf(new Date()), entered: 180, kept_d30: { value: null, n: 0, status: 'not_yet' } },
          { cohort: '2025-01', entered: 180, kept_d30: { value: null, n: 0, status: 'not_yet' } },
          { cohort: '2026-09', entered: 180, kept_d30: { value: null, n: 12, status: 'too_few' } },
        ],
      })),
      '/growth/segments': ok({ segments: [{ slug: 'members', name: 'Members' }] }),
    });
    show('/company/growth?segment=members&month=2026-10');
    const tiles = await screen.findByRole('group', { name: 'Headline' });
    expect(within(tiles).getByText('Sign-ups')).toBeTruthy();
    expect(within(tiles).getByText('Activated')).toBeTruthy();
    expect(within(tiles).getByText('not yet')).toBeTruthy();
    expect(within(tiles).queryByText('Cost')).toBeNull();
    expect(screen.queryByText('CAC')).toBeNull();
    expect(screen.queryByText(/LTV/)).toBeNull();
    const retention = screen.getByRole('table', { name: 'Retention' });
    expect(within(retention).getByText(`first read ${firstRead(ymOf(new Date()), 30)}`)).toBeTruthy();
    expect(within(retention).getByText('not supplied')).toBeTruthy();
    expect(within(retention).getByText('12 of 180 old enough')).toBeTruthy();
  });

  it('a segment is its own tab and a channel opens at its address, keeping the segment', async () => {
    serve({ '/growth/segments': ok({ segments: [{ slug: 'alpha', name: 'Alpha', channels: ['search'] }] }), '/growth/read': ok({ ...READ, scope: 'segment:alpha', segment: { slug: 'alpha', name: 'Alpha', rule: null, value_kind: 'revenue' } }) });
    show('/company/growth?segment=alpha');
    const tab = await screen.findByRole('link', { name: 'Alpha' });
    expect(tab.getAttribute('aria-current')).toBe('page');
    expect(searchOf(screen.getByRole('link', { name: 'All' }).getAttribute('href'))).toEqual({});
    const channel = await screen.findByRole('link', { name: 'Search ads' });
    expect(searchOf(channel.getAttribute('href'))).toEqual({ segment: 'alpha', panel: 'channel:search' });
    expect(within(screen.getByRole('group', { name: 'Headline' })).getByText('CAC')).toBeTruthy();
  });

  it('with nothing read yet says there are no numbers yet', async () => {
    const headline = { ...empty({}).headline, activated_rate: fig(0.4), paid_rate: fig(0.1), kept: fig(null), ltv_cac: fig(null) };
    serve({ '/growth/read': ok(empty({ headline })) });
    show('/company/growth');
    expect(await screen.findByText('No numbers yet')).toBeTruthy();
    // Sources are set up through the Growth Manager (the Sources tab is gone): with none, nothing to press.
    expect(screen.queryByRole('button', { name: 'Set up' })).toBeNull();
  });
});

describe('Growth panels @s480 @s444', () => {
  const SOURCED: GrowthRead = {
    scope: 'channel:search',
    period: '2026-10',
    as_of: '2026-11-02',
    headline: {
      signups: fig(120),
      activated_rate: fig(0.5),
      paid_rate: fig(0.3),
      new_customers: fig(40),
      cost: fig(2000),
      cac: fig(50),
      kept: fig(0.75),
      ltv: fig(300, { window: 'd365' }),
      ltv_cac: fig(6),
    },
    channels: [{ slug: 'search', name: 'Search ads', signups: 120, new_customers: 40, new_share: 1, cost: 2000, cac: 50, cost_share: 1, budget: 2500 }],
    health: { basis: 'customers', unknown_share: 0, stale_sources: ['Ad sheet'], reconciled: 'not_reconciled' },
    metrics: [],
    series: [40, 45, 50].map((cac, i) => ({ period: `2026-0${8 + i}`.replace('2026-010', '2026-10'), signups: null, new_customers: 10, cost: cac * 10, cac, kept: null })),
    campaigns: {},
    more: [],
  };

  it('uses the month returned by the board for the picker and channel read', async () => {
    const urls: URL[] = [];
    const month = { period: '2025-01', as_of: '2025-02-01' };
    serve({
      '/growth/read': ok({ ...READ, ...month }),
      '/growth/read?scope=channel:search': ok({ ...CHANNEL, ...month }),
    }, urls);
    show('/company/growth');
    expect((await screen.findByRole('combobox', { name: 'Month' })).textContent).toContain('January 2025');
    fireEvent.click(screen.getByRole('link', { name: 'Search ads' }));
    expect(await screen.findByText('for a new customer, January 2025')).toBeTruthy();
    expect(urls.find((u) => u.searchParams.get('scope') === 'channel:search')?.searchParams.get('period')).toBe('2025-01');
  });

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

  it('shows the channel-wide acquisition cost alongside channel-wide history when opened from a segment', async () => {
    serve({
      '/growth/read?scope=segment:retail': ok({
        ...READ,
        scope: 'segment:retail',
        channels: [{ ...READ.channels[0]!, cac: 100 }],
      }),
      '/growth/read?scope=channel:search': ok(CHANNEL),
    });
    show('/company/growth?segment=retail&month=2026-10');
    fireEvent.click(await screen.findByRole('link', { name: 'Search ads' }));
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(await within(panel).findByText('$50', { selector: '.pfig b' })).toBeTruthy();
  });

  it('a channel that fails offers Try again', async () => {
    serve({ '/growth/read?scope=channel:search': broken });
    show('/company/growth?panel=channel:search');
    expect(await screen.findByRole('heading', { name: 'This channel did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('says where its numbers come from, from the read, and draws its cost per customer by month', async () => {
    serve({ '/growth/read': ok(SOURCED) });
    show('/company/growth?month=2026-10&panel=channel:search');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    await within(panel).findByText('Where this comes from');
    const trace = [...panel.querySelectorAll('.ptrace')].slice(0, 4).map((r) => r.textContent);
    expect(trace).toEqual([
      expect.stringMatching(/^SpendStale: Ad sheet\$2,000/),
      expect.stringMatching(/^New customersof 120 sign-ups40/),
      expect.stringMatching(/^LTVNet revenue per customer, 365 days\$300/),
      expect.stringMatching(/^LTV to cost\$300 over \$506\.0/),
    ]);
    expect(within(panel).getByRole('img', { name: 'Search ads, cost per customer by month' })).toBeTruthy();
  });
});

describe('Growth Manager entry @s479 @s483', () => {
  it('has no channel, month, customer or source editor: changes are asked of the Growth Manager @s481 @s482', async () => {
    directory(GM());
    serve(OWNED);
    show('/company/growth?month=2026-10&panel=channel:search');
    const panel = await screen.findByRole('complementary', { name: 'Search ads' });
    expect(within(panel).getByRole('button', { name: 'Close' })).toBeTruthy();
    expect(within(panel).queryByRole('button', { name: /^(Edit|\+ Month|Save|Add)/ })).toBeNull();
    const kept = await screen.findByRole('table', { name: 'Retention' });
    expect(within(kept).queryAllByRole('link')).toHaveLength(0);
    expect(within(kept).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /^(New channel|Edit|\+ Month|Upload|Connect)/ })).toBeNull();
    expect(screen.getByRole('textbox', { name: 'Ask the Growth Manager' })).toBeTruthy();
  });
});

describe('Growth on a phone', () => {
  beforeEach(() => {
    document.documentElement.dataset.layout = 'cards';
  });

  it('Retention drops Day 180, which would push the table sideways', async () => {
    const win = { value: 0.5, n: 10, status: 'ok' };
    serve({ '/growth/read': ok({ ...READ, retention: [{ cohort: '2026-05', entered: 20, kept_d30: win, kept_d90: win, kept_d180: win }] }) });
    show('/company/growth?month=2026-10');
    expect(heads(await screen.findByRole('table', { name: 'Retention' }))).toEqual(['First order', 'Customers', 'Day 30', 'Day 90']);
  });

  it('Channels shows Channel, New and CAC', async () => {
    serve();
    show('/company/growth?month=2026-10');
    const table = await screen.findByRole('table', { name: 'Channels' });
    expect(heads(table)).toEqual(['Channel', 'New', 'CAC']);
  });
});

describe('Growth panel addresses', () => {
  it('a channel opens on the board; an old Sources address lands on the board; anything else is dropped', () => {
    expect(validateGrowthSearch({ tab: 'sources', panel: 'source:src1' })).toEqual({});
    expect(validateGrowthSearch({ panel: 'source:src1' })).toEqual({});
    expect(validateGrowthSearch({ tab: 'sources', panel: 'channel:search' })).toEqual({ panel: 'channel:search' });
    expect(validateGrowthSearch({ panel: 'channel:search' })).toEqual({ panel: 'channel:search' });
    expect(validateGrowthSearch({ panel: 'month:search:2026-09' })).toEqual({});
    expect(validateGrowthSearch({ panel: 'customers:2026-06:90' })).toEqual({});
  });
});
