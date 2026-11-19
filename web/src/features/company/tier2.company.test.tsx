import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type { CompanyFloor, MatterRow, ProductList, RiskDetail, RiskList } from '@/api/schema';
import { CompanyPage } from './CompanyPage';
import { ProductsPage } from './ProductsPage';
import {
  accessQuery,
  brandQuery,
  brandVersionQuery,
  floorQuery,
  groupsQuery,
  marketBriefingQuery,
  marketCountQuery,
  marketSpendQuery,
  mattersQuery,
  productsQuery,
  riskQuery,
  risksQuery,
  strategyQuery,
  strategyVersionQuery,
  strategyVersionsQuery,
  valueChainQuery,
  type Brand,
  type MarketSpend,
  type Strategy,
  type Team,
} from './queries';
import { RiskPage, type RiskView } from './RiskPage';
import { StrategyPage, type StrategyTab } from './StrategyPage';

/* Tier 2 checks for Company and Strategy (Market, Org, Brand, versions) and Products and Risk. Sample rows only. */

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
function serve(reply: (url: string, method: string) => Reply): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });

/** The page at its own address; `page` gets the address's search, as the route hands it in. */
function show(page: (search: Record<string, unknown>) => ReactNode, at: string) {
  function Routed() {
    const search = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <>{page(search)}</>;
  }
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: Routed });
  const router = createRouter({ routeTree: root.addChildren([route]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const strategy = (tab: StrategyTab) => (search: Record<string, unknown>) => <StrategyPage tab={tab} search={search} />;
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');
const actor = (id: string, name: string, kind: 'person' | 'agent' = 'person') => ({ id, kind, name });
function directory(...actors: ReturnType<typeof actor>[]) {
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
}

// ---------------------------------------------------------------- Company

describe('A chat opened over Company @s291', () => {
  it("opens a hall manager's chat beside the Floor, at the address", async () => {
    serve(nothing);
    directory(actor('bot:em', 'Engineering Manager', 'agent'));
    const floor = { factories: [{ id: 'eng', manager_id: 'bot:em', name: 'Engineering', pipelines: [] }] } as unknown as CompanyFloor;
    queryClient.setQueryData(floorQuery.queryKey, floor);
    const router = show(() => <CompanyPage search={{}} />, '/company');
    const face = await screen.findByRole('link', { name: 'Chat with Engineering Manager' });
    expect(href(face)).toBe('/company?chat=agent:em');
    fireEvent.click(face);
    await waitFor(() => expect(router.state.location.search).toEqual({ chat: 'agent:em' }));
  });
});

// ---------------------------------------------------------------- Market

const spend: MarketSpend = {
  name: 'Dog grooming in Ohio',
  year: 2026,
  total: { spend_usd: 48_000_000 },
  segments: [
    {
      id: 's1',
      name: 'Mobile grooming',
      size: { id: 'z1', spend_usd: 12_000_000, low_usd: 9_000_000, high_usd: 15_000_000, growth_pct: 6.5, confidence: 'medium', accepted: true },
      players: [{ entity_id: 'e1', name: 'Groomly', figures: [{ metric: 'revenue', value: 3_000_000, kind: 'reported' }] }],
    },
  ],
};

describe('Market @s302 @s319 @s320 @s324', () => {
  it('sizes the market on top, then what changed, the segments and the organizations, each opening its panel', async () => {
    serve(nothing);
    queryClient.setQueryData(marketSpendQuery.queryKey, spend);
    queryClient.setQueryData(marketBriefingQuery.queryKey, { briefing: { week: '2026-W46', title: 'Week of Nov 9', body: '', created_at: '2026-11-10T10:00:00Z', items: [{ text: 'Groomly raised prices' }] } });
    queryClient.setQueryData(valueChainQuery.queryKey, { layers: [] });
    queryClient.setQueryData(marketCountQuery.queryKey, 2);
    show(strategy('market'), '/company/strategy/market');
    // where the money goes: the market's yearly spend, then each segment's
    const figures = await screen.findByRole('group', { name: 'Market figures' });
    expect(figures.textContent).toContain('$48M');
    expect(figures.textContent).toContain('Dog grooming in Ohio');
    const segs = screen.getByRole('table', { name: 'Segments' });
    expect(within(segs).getByText('$12M')).toBeTruthy();
    expect(within(segs).getByText('6.5%')).toBeTruthy();
    // what changed this week opens the briefing in full
    const changes = screen.getByRole('region', { name: 'Changes' });
    expect(href(within(changes).getByText('Groomly raised prices'))).toBe('/company/strategy/market?panel=briefing');
    // who we are up against: each organization with what it sells and its figure, opening its note
    const orgs = screen.getByRole('table', { name: 'Organizations' });
    const row = within(orgs).getByText('Groomly').closest('tr')!;
    expect(row.textContent).toContain('Mobile grooming');
    expect(row.textContent).toContain('$3M');
    expect(href(within(orgs).getByText('Groomly'))).toBe('/company/strategy/market?panel=player:e1');
  });
});

// ---------------------------------------------------------------- Brand and Strategy versions

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

describe('Brand @s307', () => {
  it('shows the published book in its frame, with Edit only for those who may edit it', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, book());
    queryClient.setQueryData(brandVersionQuery(1).queryKey, { n: 1, body: { name: 'Fieldkit' } });
    show(strategy('brand'), '/company/strategy/brand');
    await screen.findByTitle('Brand book');
    expect(frame()).toContain('Fieldkit Co');
    expect(frame()).toContain('Kits for first gardeners');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    cleanup();
    queryClient.setQueryData(brandQuery.queryKey, book({ may_edit: true }));
    show(strategy('brand'), '/company/strategy/brand');
    expect(await screen.findByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('says so when there is no book yet, offering Edit to an editor', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, { published: null, draft: null, versions: [], may_edit: true } as Brand);
    show(strategy('brand'), '/company/strategy/brand');
    expect(await screen.findByText('No brand book yet')).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'Edit' }).length).toBeGreaterThan(0);
  });
});

describe('Brand versions @s370', () => {
  it('lists the versions in the head and reads a past one at its own address, without Edit', async () => {
    serve(nothing);
    queryClient.setQueryData(brandQuery.queryKey, book({ may_edit: true }));
    queryClient.setQueryData(brandVersionQuery(1).queryKey, { n: 1, body: { name: 'Fieldkit' } });
    const router = show(strategy('brand'), '/company/strategy/brand');
    fireEvent.click(await screen.findByRole('button', { name: 'v2' }));
    expect(await screen.findByRole('menuitem', { name: /v2/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /v1/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ v: 1 }));
    await waitFor(() => expect(frame()).not.toContain('Fieldkit Co'));
    expect(frame()).toContain('Fieldkit');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
  });
});

describe('Strategy versions @s355', () => {
  it('lists the versions in the head and reads a past one, its own summary, at its own address', async () => {
    serve(nothing);
    const now: Strategy = { current: { n: 3, summary: 'Sell kits to new gardeners.', sections: {}, published_by: 'human:me', published_at: '2026-11-18T10:00:00Z' }, bets: [] };
    queryClient.setQueryData(strategyQuery.queryKey, now);
    queryClient.setQueryData(strategyVersionsQuery.queryKey, [{ n: 3 }, { n: 2, published_at: '2026-11-12T10:00:00Z' }]);
    queryClient.setQueryData(strategyVersionQuery(2).queryKey, { n: 2, summary: 'Sell kits to gardeners.', sections: {}, bets: [], published_by: 'human:me' });
    const router = show(strategy('strategy'), '/company/strategy');
    expect(await screen.findByText('Sell kits to new gardeners.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'v3' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /v2/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ v: 2 }));
    expect(await screen.findByText('Sell kits to gardeners.')).toBeTruthy();
    expect(screen.queryByText('Sell kits to new gardeners.')).toBeNull();
  });
});

// ---------------------------------------------------------------- Org

describe('Org @s309', () => {
  const g = (id: string, name: string, over: Partial<Team> = {}): Team => ({ id, name, parent: '', order: 0, people: [], bots: [], bot_slugs: [], ...over });

  it('draws each team under its lead, a group inside it folding on its own, each teammate opening their sheet', async () => {
    serve(nothing);
    directory(actor('human:lee', 'Lee Park'), actor('human:ana', 'Ana Ruiz'), actor('human:bo', 'Bo Chen'));
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { order: 1, people: ['lee', 'ana'], lead: 'person:lee' }), g('east', 'East', { parent: 'sales', order: 2, people: ['bo'] })]);
    queryClient.setQueryData(accessQuery.queryKey, { you: 'owner', owner: 'lee' });
    show(strategy('org'), '/company/strategy/org');
    const sales = await screen.findByRole('region', { name: 'Sales' });
    // the company's owner (Access names them) is the root, with Tico beside them; Add teammate is the filled button
    const root = document.querySelector('.oc-root')!;
    expect(root.querySelector('.oc-me')?.getAttribute('href')).toBe('/team/lee');
    expect(root.textContent).toContain('Lee Park');
    expect(within(root as HTMLElement).getByText('Tico')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add teammate' })).toBeTruthy();
    expect(href(within(sales).getByRole('link', { name: 'Sales lead' }))).toBe('/company/strategy/org?panel=mate:human:lee');
    // the lead heads the team and is not listed again among its teammates
    expect(within(sales).getAllByText('Lee Park')).toHaveLength(1);
    expect(href(within(sales).getByText('Ana Ruiz'))).toBe('/company/strategy/org?panel=mate:human:ana');
    expect(within(sales).getByText('Bo Chen')).toBeTruthy();
    fireEvent.click(within(sales).getByRole('button', { name: /^East/ }));
    expect(within(sales).queryByText('Bo Chen')).toBeNull();
    expect(within(sales).getByText('Ana Ruiz')).toBeTruthy();
    fireEvent.click(within(sales).getByRole('button', { name: /^Sales/ }));
    expect(within(sales).queryByText('Ana Ruiz')).toBeNull();
  });

  it('says so when there are no teams, and offers Try again when the groups fail', async () => {
    serve(nothing);
    queryClient.setQueryData(groupsQuery.queryKey, []);
    show(strategy('org'), '/company/strategy/org');
    expect(await screen.findByText('No teams yet')).toBeTruthy();
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
    serve((url) => (url.includes('/api/v2/groups') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(strategy('org'), '/company/strategy/org');
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

// ---------------------------------------------------------------- Products

describe('Products @n:re:products', () => {
  it('adds a product from its panel, then closes it', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? { status: 200, body: { product: { id: 'pr2', key: 'gadget', name: 'Gadget' } } } : nothing()));
    queryClient.setQueryData(productsQuery.queryKey, { products: [] } as unknown as ProductList);
    const router = show((s) => <ProductsPage search={s} />, '/company/products');
    expect(await screen.findByText('No products yet')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Add product' })[0]!);
    const panel = await screen.findByRole('complementary', { name: 'New product' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Gadget' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/products', method: 'POST', body: { name: 'Gadget', status: 'planned' } }]));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });
});

// ---------------------------------------------------------------- Risk

const matter = (id: string, title: string, over: { status?: string; amount?: number; forum?: string; risk?: string } = {}): MatterRow => ({
  task: { id, title, status: over.status ?? 'open', stage: 'Evidence', owner_id: null, created_at: '2026-11-18T10:00:00Z', pipeline_id: 'p1', number: 7 },
  risks: [{ id: over.risk ?? 'r1', key: 'chargebacks', label: 'Chargebacks' }],
  matter: { kind: 'dispute', amount: over.amount ?? 41200, currency: 'USD', claim: 'Item not received', forum: over.forum ?? 'Stripe', side: 'theirs', party: { id: 'c9', name: 'Pat Doe' }, product: null } as unknown as MatterRow['matter'],
});
const risk = (id: string, label: string, level: 'live' | 'open' | 'quiet', over: Record<string, unknown> = {}) => ({
  id,
  key: id,
  kind: 'risk',
  label,
  level,
  open: 0,
  closed: 0,
  claimed: [],
  has_rules: false,
  next_date: null,
  owner: null,
  version: 1,
  waits: false,
  ...over,
});
const risks = () =>
  queryClient.setQueryData(risksQuery.queryKey, {
    risks: [
      risk('r1', 'Chargebacks', 'live', { open: 1, closed: 2, claimed: [{ amount: 41200, currency: 'USD' }], has_rules: true }),
      risk('r2', 'Trademark', 'open', { open: 1 }),
      risk('r3', 'Insurance', 'quiet', { next_date: '2027-03-01' }),
    ],
  } as unknown as RiskList);
const riskPage = (view: RiskView = 'risks') => (s: Record<string, unknown>) => <RiskPage view={view} search={s} />;

describe('Risk @n:re:risk', () => {
  it('groups the risks by level, a live one heading its open matters, each opening where it should', async () => {
    serve(nothing);
    risks();
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    show(riskPage(), '/company/risk');
    const live = await screen.findByRole('region', { name: 'Live' });
    expect(href(within(live).getByText('Chargebacks'))).toBe('/company/risk?risk=r1');
    expect(live.textContent).toContain('$412 claimed');
    expect(href(within(live).getByText('Refund dispute A'))).toBe('/company/risk?panel=matter:t1');
    expect(href(within(live).getByText('2 closed'))).toBe('/company/risk?risk=r1');
    fireEvent.click(within(live).getByRole('button', { name: 'Fold Chargebacks' }));
    expect(within(live).queryByText('Refund dispute A')).toBeNull();
    expect(href(within(screen.getByRole('region', { name: 'Open' })).getByText('Trademark'))).toBe('/company/risk?risk=r2');
    expect(within(screen.getByRole('region', { name: 'Quiet' })).getByText('Insurance')).toBeTruthy();
  });

  it('says so when the risks do not load, with Try again', async () => {
    serve((url) => (url.includes('/api/v2/risks') ? { status: 500, body: { detail: 'boom' } } : { status: 200, body: { matters: [], next_offset: null } }));
    show(riskPage(), '/company/risk');
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('One matter @n:re:matter', () => {
  it('opens a matter in the panel with its facts, its task and its risks', async () => {
    serve(nothing);
    risks();
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    show(riskPage(), '/company/risk?panel=matter:t1');
    const panel = await screen.findByRole('complementary', { name: 'Refund dispute A' });
    for (const said of ['Dispute', 'Pat Doe', '$412', 'Item not received', 'Theirs', 'Stripe']) expect(within(panel).getByText(said)).toBeTruthy();
    expect(href(within(panel).getByText('Open task'))).toBe('/tasks/p1/7');
    expect(href(within(within(panel).getByRole('region', { name: 'Risks' })).getByText('Chargebacks'))).toBe('/company/risk?risk=r1');
  });
});

describe('One risk @n:re:risk-one', () => {
  it('opens a risk as its own page: figures, matters, dates, rules, sources and its text', async () => {
    serve(nothing);
    const detail = {
      risk: { ...risk('r1', 'Chargebacks', 'live', { open: 1, closed: 2, claimed: [{ amount: 41200, currency: 'USD' }] }), markdown: 'Most say the kit did not arrive.' },
      editable: false,
      matters: [matter('t1', 'Refund dispute A')],
      next_offset: null,
      obligations: [{ id: 'o1', rule: { text: 'Send evidence within 7 days' }, basis: 'Contract', mark: null, due_on: '2026-11-24' }],
      rules: [{ link_id: 'l1', id: 'ru1', text: 'A chargeback opens, send evidence' }],
      sources: [{ link_id: 'l2', id: 'c1', name: 'Card network rules' }],
      coverage: [],
    } as unknown as RiskDetail;
    queryClient.setQueryData(riskQuery('r1').queryKey, detail);
    show(riskPage(), '/company/risk?risk=r1');
    const figures = await screen.findByRole('group', { name: 'Risk figures' });
    expect(figures.textContent).toContain('$412');
    expect(href(within(screen.getByRole('region', { name: 'Matters' })).getByText('Refund dispute A'))).toBe('/company/risk?risk=r1&panel=matter:t1');
    expect(within(screen.getByRole('region', { name: 'Dates' })).getByText('Send evidence within 7 days')).toBeTruthy();
    expect(href(within(screen.getByRole('region', { name: 'Rules' })).getByText('A chargeback opens, send evidence'))).toBe('/company/rules?rule=ru1');
    expect(href(within(screen.getByRole('region', { name: 'Sources' })).getByText('Card network rules'))).toBe('/library/contacts?contact=c1');
    expect(screen.getByText('Most say the kit did not arrive.')).toBeTruthy();
    // the owner pick is only for those who may change the risk
    expect(screen.queryByRole('combobox', { name: 'Owner' })).toBeNull();
  });
});

describe('Risk as Disputes @n:re:disputes', () => {
  it('tables every dispute, open ones first, each opening its matter', async () => {
    serve(nothing);
    queryClient.setQueryData(mattersQuery('disputes').queryKey, {
      matters: [matter('t1', 'Refund dispute A'), matter('t2', 'Refund dispute B', { status: 'done', amount: 12900, forum: 'PayPal' })],
      next_offset: null,
    });
    show(riskPage('disputes'), '/company/risk/disputes');
    const open = await screen.findByRole('table', { name: 'Open disputes' });
    const row = within(open).getByText('Refund dispute A').closest('tr')!;
    for (const said of ['Chargebacks', '$412', 'Stripe', 'Evidence']) expect(row.textContent).toContain(said);
    expect(href(within(open).getByText('Refund dispute A'))).toBe('/company/risk/disputes?panel=matter:t1');
    expect(within(screen.getByRole('table', { name: 'Closed disputes' })).getByText('Refund dispute B')).toBeTruthy();
    expect(within(screen.getByRole('group', { name: 'Show' })).getByRole('button', { name: 'Disputes' }).getAttribute('aria-pressed')).toBe('true');
  });
});
