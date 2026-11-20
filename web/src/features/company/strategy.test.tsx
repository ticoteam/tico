import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the router's error component, as the app wires it
import { PageFault } from '@/shell/PageFault';
import type { GoalTree, Kpi, KpiDetail } from '@/api/schema';
import { goalLink } from './exports';
import {
  brandProposalsQuery,
  brandQuery,
  brandVersionQuery,
  goalNeedsQuery,
  goalsTreeQuery,
  kpiQuery,
  marketSpendQuery,
  placedKpisQuery,
  strategyQuery,
  strategyVersionQuery,
  strategyVersionsQuery,
  type Brand,
  type GoalNeed,
  type MarketSpend,
  type PlacedKpi,
  type Strategy,
} from './queries';
import { StrategyPage, type StrategyTab } from './StrategyPage';

/* Strategy's own tabs: the strategy and its versions, Goals and their KPIs, and the Brand book. Sample data only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
const toasts = vi.hoisted(() => vi.fn());
vi.mock('@/kit/Toast', async (real) => ({ ...(await real<object>()), toast: toasts }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  toasts.mockClear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
/** Answers every read with `reply`, and keeps each write. */
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });
const ok = (body: unknown = {}): Reply => ({ status: 200, body });
const refused = (status: number, code: string): Reply => ({ status, body: { error: { code } } });

/** A Strategy tab at its own address; with no `search` it reads the address's search, as the route does. */
function show(tab: StrategyTab, at: string, search?: Record<string, unknown>) {
  function Routed() {
    const live = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <StrategyPage tab={tab} search={search ?? live} />;
  }
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: Routed });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [at] }), defaultErrorComponent: PageFault });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');
async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

// ---------------------------------------------------------------- Strategy

const strategy: Strategy = {
  current: { n: 3, summary: 'Sell kits to new gardeners.', sections: { serve: 'First gardeners.\n\nRenters with a balcony.' }, published_by: 'bot:strat', published_at: '2026-11-18T10:00:00Z' },
  bets: [{ id: 'b1', statement: 'Balcony kits', status: 'active' }],
};

describe('Strategy versions @s355', () => {
  it('shows the version in the head and the strategy as written, with no change marks; a past one reads at its own address', async () => {
    serve(nothing);
    queryClient.setQueryData(strategyQuery.queryKey, strategy);
    queryClient.setQueryData(strategyVersionsQuery.queryKey, [{ n: 3 }, { n: 2, published_at: '2026-11-12T10:00:00Z' }]);
    queryClient.setQueryData(strategyVersionQuery(2).queryKey, { n: 2, summary: 'Sell kits to gardeners.', sections: {}, bets: [], published_by: 'human:me' });
    const router = show('strategy', '/company/strategy');
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    expect(screen.getByText('Renters with a balcony.').tagName).toBe('P');
    expect(screen.getByText('Balcony kits').tagName).toBe('SPAN');
    expect(document.querySelector('ins, del')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'v3' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v2/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ v: 2 }));
    expect(await screen.findByText('Sell kits to gardeners.')).toBeTruthy();
    expect(screen.queryByText('Sell kits to new gardeners.')).toBeNull();
  });
});

describe('Strategy: where we play @s304', () => {
  const spend = {
    segments: [
      { id: 'sg1', name: 'Balcony growers', self: true, size: { spend_usd: 2_400_000 }, players: [{ entity_id: 'e1', name: 'Sprout Co' }, { entity_id: 'e2', name: 'Leafbox' }] },
      { id: 'sg2', name: 'Gift buyers', rolled_usd: { spend_usd: 310_000 }, players: [{ entity_id: 'e1', name: 'Sprout Co' }] },
      { id: 'sg3', name: 'Schools', players: [] },
    ],
  } as unknown as MarketSpend;
  let asked = 0;
  /** Every read answers `status`; the market's spend, read for real only at 200, is counted. */
  function market(status: number) {
    asked = 0;
    serve((url) => {
      if (!url.includes('/market/spend')) return { status, body: { detail: 'down' } };
      asked += 1;
      return status === 200 ? ok(spend) : { status, body: { detail: status === 403 ? 'forbidden' : 'down' } };
    });
  }
  const plain: Strategy = { current: { n: 3, summary: 'Sell kits to new gardeners.', sections: {}, published_by: 'bot:strat', published_at: '2026-11-18T10:00:00Z' }, bets: [] };
  beforeEach(() => {
    queryClient.setQueryData(strategyQuery.queryKey, plain);
    queryClient.setQueryData(strategyVersionsQuery.queryKey, [{ n: 3 }]);
  });

  it('lists the market’s segments with their sizes, each opening its Market panel @s304', async () => {
    market(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, spend);
    show('strategy', '/company/strategy');
    const balcony = await screen.findByText('Balcony growers');
    expect(href(balcony)).toBe('/company/strategy/market?panel=segment:sg1');
    expect(within(balcony.closest('a')!).getByText('$2.4M')).toBeTruthy();
    expect(within(screen.getByText('Gift buyers').closest('a')!).getByText('$310k')).toBeTruthy();
    expect(within(screen.getByText('Schools').closest('a')!).getByText('Not sized')).toBeTruthy();
    // The mock's share column has no server number behind it: no percent is shown.
    expect(screen.queryByText(/%$/)).toBeNull();
  });

  it('leaves Where we play out when the market has no segments @s304', async () => {
    market(404);
    queryClient.setQueryData(marketSpendQuery.queryKey, { segments: [] } as unknown as MarketSpend);
    show('strategy', '/company/strategy');
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Where we play' })).toBeNull();
    expect(screen.queryByText('None yet')).toBeNull();
  });

  it('offers Try again when the market did not load, and reads it again @s304', async () => {
    market(500);
    show('strategy', '/company/strategy');
    expect(await screen.findByText('Market did not load')).toBeTruthy();
    market(200);
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(await screen.findByText('Balcony growers')).toBeTruthy();
    expect(asked).toBe(1);
  });

  it('leaves Where we play out, with no Try again, when the market is closed to this person @s304', async () => {
    market(403);
    show('strategy', '/company/strategy');
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    await waitFor(() => expect(asked).toBeGreaterThan(0));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Where we play' })).toBeNull());
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
});

// ---------------------------------------------------------------- Goals

const kpi: Kpi = {
  id: 'k1',
  name: 'Weekly signups',
  unit: '',
  direction: 'up',
  cadence: 'weekly',
  auto: false,
  definition_version: 1,
  freshness: 'fresh',
  latest: { id: 'r1', value: 12, period_end: '2026-11-10', created_at: '2026-11-10T10:00:00Z', definition_version: 1, quality: 'measured', source: 'Stripe' },
  owner_id: 'bot:growth',
  readings: 1,
  reason: '',
  spark: [8, 12],
  status: 'green',
  link: { goal_id: 'g1', kind: 'improve', target: 40, deadline: '2026-12-31' },
} as Kpi;
const tree = {
  goals: [
    { id: 'g0', title: 'Reach 1,000 customers', body: '', owner_id: 'company', parent_id: null, rank: 0, status: null, status_at: null, status_by_id: null, status_note: '', status_source: null, created_at: '2026-11-01T00:00:00Z', created_by_id: '', updated_at: '', kpis: [], open_tasks: 0 },
    { id: 'g1', title: 'Grow signups', body: '', owner_id: 'bot:growth', parent_id: 'g0', rank: 1, status: 'red', status_at: null, status_by_id: null, status_note: '', status_source: null, created_at: '2026-11-01T00:00:00Z', created_by_id: '', updated_at: '', kpis: [kpi], open_tasks: 0 },
  ],
  other_kpis: [],
  owners: {},
  proposals: [],
  unaligned: {},
} as unknown as GoalTree;
const detail = { kpi, readings: [kpi.latest], links: [{ goal_id: 'g1', goal_title: 'Grow signups', kind: 'improve', target: 40, status: 'ok', target_label: '→ 40 by Dec 31' }], checkins: [], definitions: [], proposals: [], may_edit: true, may_log: true } as unknown as KpiDetail;
const growth = { id: 'bot:growth', kind: 'agent', name: 'Growth Lead', slug: 'growth' };

describe('Goals', () => {
  it('opens another goal with its own title while a rename is unfinished', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    show('goals', '/company/strategy/goals?panel=goal:g1');
    const first = within(await screen.findByRole('complementary', { name: 'Goal' }));
    fireEvent.click(first.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(first.getByRole('textbox', { name: 'Title' }), { target: { value: 'Draft goal name' } });
    fireEvent.click(first.getByRole('link', { name: 'Reach 1,000 customers' }));
    const panel = within(await screen.findByRole('complementary', { name: 'Company' }));
    expect(panel.queryByRole('textbox', { name: 'Title' })).toBeNull();
    expect(panel.getByText('Reach 1,000 customers')).toBeTruthy();
  });

  it('lists each goal with its KPIs under the company goal, each opening its panel, as the tree only @s310 @s337 @s338 @s339', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    show('goals', '/company/strategy/goals');
    const goal = await screen.findByText('Grow signups');
    // the company's own top goal is the headline over the tree
    expect(href(screen.getByText('Reach 1,000 customers'))).toBe('/company/strategy/goals?panel=goal:g0');
    expect(href(goal)).toBe('/company/strategy/goals?panel=goal:g1');
    expect(screen.getByText('Behind')).toBeTruthy();
    expect(href(screen.getByText('Weekly signups'))).toBe('/company/strategy/goals?panel=kpi:k1');
    // the old Loops and Journey views are gone: Goals is the tree, with no view switch
    expect(screen.queryByText(/Loops|Journey/)).toBeNull();
    // Add KPI lives in a goal's panel, not on the tree
    expect(screen.queryByText('Add KPI')).toBeNull();
  });

  it('narrows the tree to the goals whose title or KPI holds the words searched @s310', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    show('goals', '/company/strategy/goals');
    await screen.findByText('Grow signups');
    // the head's search starts as a button and opens into the field
    fireEvent.change(screen.getByRole('textbox', { name: 'Search goals' }), { target: { value: 'weekly' } });
    await waitFor(() => expect(screen.queryByText('Reach 1,000 customers')).toBeNull());
    expect(screen.getByText('Grow signups')).toBeTruthy();
  });

  it('opens a goal from a teammate page in its panel: owner, KPIs and check-ins @s311 @s363 @s365 @s664', async () => {
    const one = { goal: { ...tree.goals[1], checkins: [{ created_at: '2026-11-12T10:00:00Z', body: 'Signups held at 12 this week.' }] } };
    serve((url) => (url.includes('/api/v2/goals/g1') ? ok(one) : nothing()));
    queryClient.setQueryData(['directory'], { actors: [growth], byId: { 'bot:growth': growth } });
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    const link = goalLink('g1');
    expect(link.href).toBe('/company/strategy/goals?panel=goal%3Ag1');
    show('goals', link.href, link.search);
    // the head is the goal's owner; the goal's title and flag stand under it, then what it feeds
    const panel = await screen.findByRole('complementary', { name: 'Growth Lead' });
    expect(within(panel).getByRole('heading', { name: /Grow signups/ }).textContent).toContain('Behind');
    expect(within(panel).getByText('Feeds').nextElementSibling?.textContent).toBe('Reach 1,000 customers');
    expect(href(within(within(panel).getByRole('region', { name: 'KPIs' })).getByText('Weekly signups'))).toBe('/company/strategy/goals?panel=kpi:k1');
    expect(await within(panel).findByText('Signups held at 12 this week.')).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Archive' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Mark done' })).toBeTruthy();
  });

  it('shows a new reading at once with who recorded it, and takes it back when refused @s364', async () => {
    let refuse: (r: Reply) => void = () => {};
    serve((url, method) => {
      if (method === 'POST') return new Promise<Reply>((done) => (refuse = done));
      if (url.includes('/kpis/k1')) return ok(detail);
      return nothing();
    });
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    queryClient.setQueryData(kpiQuery('k1').queryKey, detail);
    show('goals', '/company/strategy/goals?panel=kpi:k1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    expect(within(panel).getByTitle('Reads it: Stripe').querySelector('.src-tool')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Add reading' }));
    fireEvent.change(within(panel).getByRole('spinbutton', { name: 'Reading' }), { target: { value: '31' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(within(panel).getAllByText('31').length).toBeGreaterThan(0));
    expect(within(panel).getByTitle('Reads it: me, by hand')).toBeTruthy();
    refuse({ status: 403, body: { detail: 'forbidden' } });
    await waitFor(() => expect(within(panel).queryAllByText('31')).toHaveLength(0));
    expect(within(panel).getAllByText('12').length).toBeGreaterThan(0);
  });

  it('marks the goal with a proposal, asks on its KPI, and says yes with an idempotency key', async () => {
    const proposal = {
      id: 'pr1',
      kind: 'kpi_target',
      goal_id: 'g1',
      kpi_id: 'k1',
      payload: { target: 50 },
      reason: 'Signups beat the plan two weeks running.',
      proposed_by: 'bot:growth',
      status: 'pending',
      card: { target_label: '50', now_label: '40', measure: { name: 'Weekly signups' } },
      may_decide: true,
    };
    const posts: { url: string; key: string | null; body: unknown }[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push({ url: String(url), key: new Headers(init.headers).get('Idempotency-Key'), body: JSON.parse(String(init.body)) });
      const body = init?.method === 'POST' ? { proposal: { ...proposal, status: 'confirmed' } } : { detail: 'not_found' };
      return new Response(JSON.stringify(body), { status: init?.method === 'POST' ? 200 : 404, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(goalsTreeQuery.queryKey, { ...tree, proposals: [proposal] } as unknown as GoalTree);
    show('goals', '/company/strategy/goals?panel=goal:g1');
    const card = await screen.findByRole('region', { name: 'Move the target from 40 to 50?' });
    expect(document.querySelector('.gt-title .dot')).toBeTruthy();
    fireEvent.click(within(card).getByRole('button', { name: 'Set 50' }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.url).toContain('/api/v2/proposals/pr1/decide');
    expect(posts[0]?.body).toEqual({ decision: 'confirm' });
    expect(posts[0]?.key).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Move the target from 40 to 50?' })).toBeNull());
  });
});

describe('Goals: Needs you @s366', () => {
  const need = (over: Partial<GoalNeed> & Pick<GoalNeed, 'id' | 'kind' | 'title'>): GoalNeed => ({ actions: [], body: null, created_at: null, subject: null, ...over }) as GoalNeed;
  const needs: GoalNeed[] = [
    need({ id: 'n1', kind: 'proposal', title: 'Strategy Owner suggests', proposal: { id: 'p1', kind: 'brand_draft', reason: 'Say kit, not bundle', proposed_by: 'bot:strategy', payload: {} } }),
    need({
      id: 'n2',
      kind: 'recovery',
      title: 'A recovery',
      goal_title: 'Grow refills',
      proposal: { id: 'p2', kind: 'recovery', payload: { evidence: 'Refills fell two weeks running' } },
      options: [{ label: 'Email lapsed buyers', pick: true, actions: [{ text: 'Write the email' }] }, { label: 'Cut the price', actions: [{ text: 'Lower refills to $9' }] }],
    }),
    need({ id: 'n3', kind: 'kpi_red', title: 'Refill orders is off track', kpi_id: 'k1', kpi_name: 'Refill orders', goal_title: 'Grow refills', reason: 'Below target three weeks' }),
    need({ id: 'n4', kind: 'vision', title: 'Say what this company is for' }),
  ];
  beforeEach(() => {
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], proposals: [], other_kpis: [], owners: {} } as never);
    queryClient.setQueryData(goalNeedsQuery.queryKey, needs);
  });

  it('shows one quiet line over the tree that opens what waits on you, each opening where it is done', async () => {
    serve(nothing);
    const router = show('goals', '/company/strategy/goals');
    fireEvent.click(await screen.findByText('4 waiting on you'));
    await waitFor(() => expect(router.state.location.search).toEqual({ panel: 'needs' }));
    const panel = await screen.findByRole('complementary', { name: 'Needs you' });
    expect(within(panel).getByRole('region', { name: 'A change to the brand book' })).toBeTruthy();
    expect(within(panel).getByText('Refills fell two weeks running')).toBeTruthy();
    expect(href(within(panel).getByText('Refill orders on Grow refills'))).toBe('/company/strategy/goals?panel=kpi:k1');
    expect(href(within(panel).getByText('Say what this company is for'))).toBe('/company/strategy');
  });

  it('accepts a suggested brand change and approves the second option of a recovery, each leaving at once', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ proposal: { id: 'p' } }) : nothing()));
    show('goals', '/company/strategy/goals?panel=needs');
    const panel = await screen.findByRole('complementary', { name: 'Needs you' });
    fireEvent.click(within(within(panel).getByRole('region', { name: 'A change to the brand book' })).getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(within(panel).queryByRole('region', { name: 'A change to the brand book' })).toBeNull());
    fireEvent.click(within(panel).getByRole('button', { name: 'Approve 2' }));
    await waitFor(() =>
      expect(sent).toEqual([
        { url: '/api/v2/proposals/p1/decide', method: 'POST', body: { decision: 'confirm' } },
        { url: '/api/v2/proposals/p2/decide', method: 'POST', body: { decision: 'confirm', option: 1 } },
      ]),
    );
  });
});

describe('KPI place @s367', () => {
  const placed: PlacedKpi[] = [
    { id: 'k1', name: 'Weekly signups', parent_kpi_id: null, growth_role: null, may_edit: true },
    { id: 'k2', name: 'Paying customers', parent_kpi_id: null, growth_role: 'north_star', may_edit: true },
    { id: 'k3', name: 'Trial starts', parent_kpi_id: 'k1', growth_role: 'input', may_edit: true },
  ];
  const PLACE = '/company/strategy/goals?panel=kpi-place:k1';
  beforeEach(() => queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], other_kpis: [], owners: {}, proposals: [], unaligned: {} }));

  it('offers Place in the KPI’s menu', async () => {
    serve(nothing);
    queryClient.setQueryData(placedKpisQuery.queryKey, placed);
    const one = { kpi: { id: 'k1', name: 'Weekly signups', unit: '', latest: null, owner_id: 'bot:growth', status: 'none' }, readings: [], links: [], may_edit: true, may_log: false } as unknown as KpiDetail;
    queryClient.setQueryData(kpiQuery('k1').queryKey, one);
    show('goals', '/company/strategy/goals?panel=kpi:k1');
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Place in tree' })).toBeTruthy();
  });

  it('saves what it feeds and its role, never offering itself or a KPI under it', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok() : nothing()));
    queryClient.setQueryData(placedKpisQuery.queryKey, placed);
    show('goals', PLACE);
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    fireEvent.click(within(panel).getByRole('combobox', { name: 'Feeds' }));
    expect(await screen.findByRole('option', { name: /Paying customers/ })).toBeTruthy();
    expect(screen.queryByRole('option', { name: /Trial starts/ })).toBeNull();
    expect(screen.queryByRole('option', { name: /Weekly signups/ })).toBeNull();
    const option = screen.getByRole('option', { name: /Paying customers/ });
    fireEvent.pointerDown(option, { pointerType: 'mouse' });
    fireEvent.click(option);
    await pick(within(panel).getByRole('combobox', { name: 'Role' }), /Input/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/kpis/k1', method: 'POST', body: { parent_kpi_id: 'k2', growth_role: 'input' } }]));
  });

  it('takes a refused place back, saying why', async () => {
    serve((_url, method) => (method === 'POST' ? refused(403, 'forbidden') : nothing()));
    queryClient.setQueryData(placedKpisQuery.queryKey, placed);
    show('goals', PLACE);
    const panel = await screen.findByRole('complementary', { name: 'Weekly signups' });
    await pick(within(panel).getByRole('combobox', { name: 'Role' }), /North Star/);
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toasts).toHaveBeenCalledWith({ text: 'Not allowed', kind: 'failed' }));
    expect(queryClient.getQueryData<PlacedKpi[]>(placedKpisQuery.queryKey)?.find((k) => k.id === 'k2')?.growth_role).toBe('north_star');
  });

  it('says so when the KPI tree does not load', async () => {
    serve(nothing);
    show('goals', PLACE);
    expect(await screen.findByText('The KPI tree did not load')).toBeTruthy();
  });
});

// ---------------------------------------------------------------- Brand

const book = (over: Partial<Brand> = {}): Brand => ({
  published: { n: 2, body: { name: 'Fieldkit Co', positioning: 'Kits for first gardeners' }, published_at: '2026-11-18T10:00:00Z', published_by: 'human:me' },
  draft: null,
  versions: [
    { n: 2, published_at: '2026-11-18T10:00:00Z', published_by: 'human:me' },
    { n: 1, published_at: '2026-11-10T10:00:00Z', published_by: 'human:me' },
  ],
  may_edit: false,
  ...over,
});
const frame = () => screen.getByTitle('Brand book').getAttribute('srcdoc') ?? '';
const BRAND = '/company/strategy/brand';

describe('Brand @s307', () => {
  it('shows the published book in its frame, with Edit only for those who may edit it', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, book());
    show('brand', BRAND);
    await screen.findByTitle('Brand book');
    expect(frame()).toContain('Fieldkit Co');
    expect(frame()).toContain('Kits for first gardeners');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    cleanup();
    queryClient.setQueryData(brandQuery.queryKey, book({ may_edit: true }));
    show('brand', BRAND);
    expect(await screen.findByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('draws the book in its own look: colours as swatches, what we say and never say, and its version', async () => {
    serve(nothing);
    const body = {
      name: 'Fieldkit Co',
      visual: { colors: [{ name: 'Moss (buttons)', hex: '#2F4A36' }, { name: 'Cream', hex: '#F6F1E7' }], fonts: ['Georgia'] },
      examples: [
        { text: 'Plant these on Saturday.', verdict: 'on' as const },
        { text: 'Unlock your green thumb', verdict: 'off' as const },
      ],
      voice: [{ trait: 'Plain', do: 'Say it plainly.', dont: "Don't hype it" }],
    };
    queryClient.setQueryData(brandQuery.queryKey, book({ published: { n: 2, body, published_at: '2026-11-18T10:00:00Z', published_by: 'human:me' } }));
    show('brand', BRAND);
    await screen.findByTitle('Brand book');
    const doc = frame();
    expect(doc).toContain('Brand book, v2');
    expect(doc).toContain('#2F4A36');
    expect(doc).toContain('<b>Moss</b><span>buttons</span>');
    expect(doc).toContain('We never say');
    expect(doc).toContain('<s>Unlock your green thumb</s>');
    // one stop at a sentence's end, never two
    expect(doc).toContain('Say it plainly.<i>');
    expect(doc).not.toContain('..');
  });

  it('says so when there is no book yet, offering Edit to an editor', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, { published: null, draft: null, versions: [], may_edit: true } as Brand);
    show('brand', BRAND);
    expect(await screen.findByText('No brand book yet')).toBeTruthy();
    // one Edit, in the empty state; the head does not repeat it
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
  });

  it('lists the versions in the head and reads a past one at its own address, without Edit @s370', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, book({ may_edit: true }));
    queryClient.setQueryData(brandVersionQuery(1).queryKey, { n: 1, body: { name: 'Fieldkit' } });
    const router = show('brand', BRAND);
    fireEvent.click(await screen.findByRole('button', { name: 'v2' }));
    expect(await screen.findByRole('menuitem', { name: /v2/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /v1/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ v: 1 }));
    await waitFor(() => expect(frame()).not.toContain('Fieldkit Co'));
    expect(frame()).toContain('Fieldkit');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });
});

describe('Brand edit @s308', () => {
  it('saves only the brand sections that changed onto the draft, then offers Publish', async () => {
    const draft: Brand = { published: { n: 1, body: { name: 'Fieldkit', voice: [{ trait: 'Plain', do: '', dont: '', level: 'warn' } as never] } }, draft: null, versions: [{ n: 1 }], may_edit: true };
    const sent = serve((url, method) =>
      method === 'PUT' && url.endsWith('/brand/draft') ? ok({ ...draft, draft: { body: { ...draft.published!.body, name: 'Fieldkit Co' }, revision: 1, changed: ['name'] } }) : nothing(),
    );
    queryClient.setQueryData(brandQuery.queryKey, draft);
    show('brand', `${BRAND}?edit=true`, { edit: true });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Name' }), { target: { value: 'Fieldkit Co' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent.map((x) => x.body)).toEqual([{ sections: { name: 'Fieldkit Co' } }]));
    expect(await screen.findByRole('button', { name: 'Publish' })).toBeTruthy();
  });

  it('adds a suggested change to the draft from the rail', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ proposal: { id: 'bp1' } }) : nothing()));
    queryClient.setQueryData(brandQuery.queryKey, book({ draft: null, versions: [], may_edit: true }));
    queryClient.setQueryData(brandProposalsQuery.queryKey, [{ id: 'bp1', kind: 'brand_draft', reason: 'Say kit, not bundle', proposed_by: 'bot:writer', proposed_at: '2026-11-18T10:00:00Z' }]);
    show('brand', `${BRAND}?edit=true`);
    const rail = await screen.findByRole('region', { name: 'Suggested' });
    expect(within(rail).getByText('Say kit, not bundle')).toBeTruthy();
    fireEvent.click(within(rail).getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/proposals/bp1/decide', method: 'POST', body: { decision: 'confirm' } }]));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Suggested' })).toBeNull());
  });
});
