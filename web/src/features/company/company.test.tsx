import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type { CompanyFloor, GoalTree, Kpi, KpiDetail, ProductList } from '@/api/schema';
import { CompanyPage } from './CompanyPage';
import { goalLink } from './exports';
import { ProductsPage } from './ProductsPage';
import { brandQuery, floorQuery, goalsTreeQuery, kpiQuery, productsQuery, strategyQuery, strategyVersionsQuery, type Brand, type Strategy } from './queries';
import { RiskPage } from './RiskPage';
import { StrategyPage } from './StrategyPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
const toasts = vi.hoisted(() => vi.fn());
vi.mock('@/kit/Toast', async (real) => ({ ...(await real<object>()), toast: toasts }));

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const r = await reply(String(url), init?.method ?? 'GET');
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });

/** The page under a route at its own address, so a link to "." resolves as it does in the app. */
function show(node: ReactNode, at: string) {
  const root = createRootRoute({ component: Outlet });
  const page = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: () => node });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');

const stage = (id: string, name: string, count: number, needs = 0) => ({ id, name, count, into: 0, needs_you: needs, more: { count: 0, owners: [] }, owners: count ? [{ actor: 'bot:builder', count, needs_you: needs }] : [] });
const floor = {
  factories: [
    {
      id: 'eng',
      manager_id: null,
      name: 'Engineering',
      pipelines: [{ id: 'p1', name: 'Features', manager_id: null, moved_into: 0, in: { day: 3, week: 12 }, out: { day: 2, week: 9 }, stages: [stage('s1', 'Spec', 2), stage('s2', 'Review', 1, 1)] }],
    },
  ],
} as unknown as CompanyFloor;

describe('Company @s248 @s250', () => {
  it('opens a stage with its owner and its tasks, the ones waiting on you first @s270', async () => {
    const tasks = {
      tasks: [
        { id: 't1', number: 4, title: 'Check the gift notes', turn: null, late: null, stage_since: null },
        { id: 't2', number: 5, title: 'Approve the spring kit', turn: { state: 'person', who: 'human:me' }, late: null, stage_since: null },
      ],
      cursor: '',
    };
    serve((url) => (url.startsWith('/api/v2/tasks?') ? { status: 200, body: tasks } : nothing()));
    queryClient.setQueryData(['directory'], { actors: [], byId: { 'bot:builder': { id: 'bot:builder', kind: 'agent', name: 'Builder' } } });
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{ panel: 'stage:p1:s2' }} />, '/company?panel=stage:p1:s2');
    const panel = await screen.findByRole('complementary', { name: 'Review' });
    expect(within(panel).getByText('Owner').nextElementSibling?.textContent).toContain('Builder');
    const rows = await within(panel).findAllByRole('link', { name: /Check the gift notes|Approve the spring kit/ });
    expect(rows.map((r) => r.textContent)).toEqual(['Approve the spring kit', 'Check the gift notes']);
    expect(href(within(panel).getByRole('link', { name: 'All tasks' }))).toBe('/tasks/p1');
  });

  it('draws each stage on the Floor, opening its tasks in the panel', async () => {
    serve(nothing);
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{}} />, '/company');
    const review = await screen.findByTitle('Review: 1');
    expect(href(review)).toBe('/company?panel=stage:p1:s2');
    expect(within(review).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Engineering' })).toBeTruthy();
    // a long pipeline name is clamped in its column; the full name stays in its title
    expect(screen.getByTitle('Features').textContent).toBe('Features');
  });

  it('says so when nothing runs yet', async () => {
    serve(nothing);
    queryClient.setQueryData(floorQuery.queryKey, { factories: [] } as unknown as CompanyFloor);
    show(<CompanyPage search={{}} />, '/company');
    expect(await screen.findByText('Nothing is running yet')).toBeTruthy();
  });

  it('shows the fault with Try again when the Floor fails', async () => {
    serve((url) => (url.includes('/company/floor') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(<CompanyPage search={{}} />, '/company');
    expect(await screen.findByText('Company did not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('Floor card, on a phone @s271', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
    cleanup();
  });
  const tasks = { tasks: [{ id: 't1', number: 4, title: 'Check the gift notes', turn: null, late: null, stage_since: null }], cursor: '' };

  it('opens a stage as a page over the Floor, its back arrow closing it', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve((url) => (url.startsWith('/api/v2/tasks?') ? { status: 200, body: tasks } : nothing()));
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyRoute />, '/company?panel=stage:p1:s2');
    const panel = await screen.findByRole('complementary', { name: 'Review' });
    expect(href(await within(panel).findByText('Check the gift notes'))).toBe('/tasks/p1/4');
    fireEvent.click(within(panel).getByRole('button', { name: 'Back' }));
    await waitFor(() => expect(screen.queryByRole('complementary', { name: 'Review' })).toBeNull());
  });

  it('keeps the x and no back arrow on a computer', async () => {
    serve((url) => (url.startsWith('/api/v2/tasks?') ? { status: 200, body: tasks } : nothing()));
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{ panel: 'stage:p1:s2' }} />, '/company?panel=stage:p1:s2');
    const panel = await screen.findByRole('complementary', { name: 'Review' });
    expect(within(panel).queryByRole('button', { name: 'Back' })).toBeNull();
    expect(within(panel).getByRole('button', { name: 'Close' })).toBeTruthy();
  });
});

/** The page reading its panel from the address, as its route does. */
function CompanyRoute() {
  const panel = useLocation({ select: (l) => (l.search as { panel?: string }).panel });
  return <CompanyPage search={{ panel }} />;
}

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
    { id: 'g1', title: 'Grow signups', body: '', owner_id: 'company', parent_id: null, rank: 1, status: 'red', status_at: null, status_by_id: null, status_note: '', status_source: null, created_at: '2026-11-01T00:00:00Z', created_by_id: '', updated_at: '', kpis: [kpi], open_tasks: 0 },
  ],
  other_kpis: [],
  owners: {},
  proposals: [],
  unaligned: {},
} as unknown as GoalTree;
const detail = { kpi, readings: [kpi.latest], links: [{ goal_id: 'g1', goal_title: 'Grow signups', kind: 'improve', target: 40, status: 'ok', target_label: '→ 40 by Dec 31' }], checkins: [], definitions: [], proposals: [], may_edit: true, may_log: true } as unknown as KpiDetail;

describe('Goals', () => {
  it('lists each goal with its KPIs, each opening its panel, as the tree only @s310 @s337 @s338 @s339', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    show(<StrategyPage tab="goals" />, '/company/strategy/goals');
    const goal = await screen.findByText('Grow signups');
    expect(href(goal)).toBe('/company/strategy/goals?panel=goal:g1');
    expect(screen.getByText('Behind')).toBeTruthy();
    expect(href(screen.getByText('Weekly signups'))).toBe('/company/strategy/goals?panel=kpi:k1');
    // the old Loops and Journey views are gone: Goals is the tree, with no view switch
    expect(screen.queryByText(/Loops|Journey/)).toBeNull();
  });

  it('opens a goal from a teammate page in its panel: owner, KPIs and check-ins @s311 @s363 @s365 @s664', async () => {
    const one = { goal: { ...tree.goals[0], checkins: [{ created_at: '2026-11-12T10:00:00Z', body: 'Signups held at 12 this week.' }] } };
    serve((url) => (url.includes('/api/v2/goals/g1') ? { status: 200, body: one } : nothing()));
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    const link = goalLink('g1');
    expect(link.href).toBe('/company/strategy/goals?panel=goal%3Ag1');
    show(<StrategyPage tab="goals" search={link.search} />, link.href);
    const panel = await screen.findByRole('complementary', { name: 'Grow signups' });
    expect(within(panel).getByRole('combobox', { name: 'Owner' })).toBeTruthy();
    expect(href(within(within(panel).getByRole('region', { name: 'KPIs' })).getByText('Weekly signups'))).toBe('/company/strategy/goals?panel=kpi:k1');
    expect(await within(panel).findByText('Signups held at 12 this week.')).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Mark done' })).toBeTruthy();
  });

  it('shows a new reading at once with who recorded it, and takes it back when refused @s364', async () => {
    let refuse: (r: Reply) => void = () => {};
    serve((url, method) => {
      if (method === 'POST') return new Promise<Reply>((ok) => (refuse = ok));
      if (url.includes('/kpis/k1')) return { status: 200, body: detail };
      return nothing();
    });
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    queryClient.setQueryData(kpiQuery('k1').queryKey, detail);
    show(<StrategyPage tab="goals" search={{ panel: 'kpi:k1' }} />, '/company/strategy/goals?panel=kpi:k1');
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
});

describe('Goal proposals', () => {
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
  const asked = { ...tree, proposals: [proposal] } as unknown as GoalTree;

  it('marks the goal, asks on its KPI, and says yes with an idempotency key', async () => {
    const posts: { url: string; key: string | null; body: unknown }[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push({ url: String(url), key: new Headers(init.headers).get('Idempotency-Key'), body: JSON.parse(String(init.body)) });
      const body = init?.method === 'POST' ? { proposal: { ...proposal, status: 'confirmed' } } : { detail: 'not_found' };
      return new Response(JSON.stringify(body), { status: init?.method === 'POST' ? 200 : 404, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(goalsTreeQuery.queryKey, asked);
    show(<StrategyPage tab="goals" search={{ panel: 'goal:g1' }} />, '/company/strategy/goals?panel=goal:g1');
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

describe('Strategy and Brand versions', () => {
  const strategy: Strategy = {
    current: { n: 3, summary: 'Sell kits to new gardeners.', sections: { serve: 'First gardeners.\n\nRenters with a balcony.' }, published_by: 'bot:strat', published_at: '2026-11-18T10:00:00Z' },
    bets: [{ id: 'b1', statement: 'Balcony kits', status: 'active' }],
  };

  it('marks what changed from the version before, from the count in the head', async () => {
    serve((url) => {
      if (url.includes('/strategy/diff?from=2&to=3'))
        return { status: 200, body: { fields: [{ key: 'sections.serve', from: 'First gardeners.', to: 'First gardeners.\n\nRenters with a balcony.' }], bets: { added: [{ id: 'b1', statement: 'Balcony kits', status: 'active' }], retired: [], changed: [] } } };
      return nothing();
    });
    queryClient.setQueryData(strategyQuery.queryKey, strategy);
    queryClient.setQueryData(strategyVersionsQuery.queryKey, [{ n: 3 }, { n: 2 }]);
    show(<StrategyPage tab="strategy" search={{ compare: true }} />, '/company/strategy?compare=true');
    const count = await screen.findByRole('button', { name: '2 changes' });
    expect(count.getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: 'v3' })).toBeTruthy();
    expect(screen.getByText('Renters with a balcony.').tagName).toBe('INS');
    expect(screen.getByText('First gardeners.').tagName).toBe('P');
    expect(screen.getByText('Balcony kits').tagName).toBe('INS');
  });

  it('saves only the brand sections that changed onto the draft, then offers Publish', async () => {
    const book: Brand = { published: { n: 1, body: { name: 'Fieldkit', voice: [{ trait: 'Plain', do: '', dont: '', level: 'warn' } as never] } }, draft: null, versions: [{ n: 1 }], may_edit: true };
    const sent: unknown[] = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const put = init?.method === 'PUT' && String(url).endsWith('/brand/draft');
      if (put) sent.push(JSON.parse(String(init.body)));
      const body = put ? { ...book, draft: { body: { ...book.published!.body, name: 'Fieldkit Co' }, revision: 1, changed: ['name'] } } : { detail: 'not_found' };
      return new Response(JSON.stringify(body), { status: put ? 200 : 404, headers: { 'Content-Type': 'application/json' } });
    });
    queryClient.setQueryData(brandQuery.queryKey, book);
    show(<StrategyPage tab="brand" search={{ edit: true }} />, '/company/strategy/brand?edit=true');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Name' }), { target: { value: 'Fieldkit Co' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ sections: { name: 'Fieldkit Co' } }]));
    expect(await screen.findByRole('button', { name: 'Publish' })).toBeTruthy();
  });
});

/** Each route file's address, from its flat file name: `a.b_.$c.tsx` is /a/b/<any>. */
const addresses = Object.keys(import.meta.glob('/src/routes/*.tsx')).map((f) => {
  const parts = (f.split('/').pop() ?? '').replace(/\.tsx$/, '').split('.').map((s) => s.replace(/_$/, '')).filter((s) => s && s !== 'index' && s !== '__root');
  return new RegExp(`^/${parts.map((s) => (s.startsWith('$') ? '[^/]+' : s.replace(/[-]/g, '\\-'))).join('/')}$`);
});
const tabHrefs = (name: string) =>
  within(screen.getByRole('navigation', { name: `${name} pages` }))
    .getAllByRole('link')
    .map((a) => a.getAttribute('href') ?? '');

describe('Tabs', () => {
  it('sends every Strategy and Risk tab to an address the app has', async () => {
    serve((url) => (url.includes('/api/v2/risks') ? { status: 200, body: { risks: [] } } : { status: 200, body: { matters: [], next_offset: null } }));
    queryClient.setQueryData(goalsTreeQuery.queryKey, tree);
    show(<StrategyPage tab="goals" />, '/company/strategy/goals');
    await screen.findByText('Grow signups');
    const strategy = tabHrefs('Strategy');
    expect(strategy).toHaveLength(6);
    // Budget is the agents' spend limits, in Usage (old #/strategy/budget)
    expect(strategy).toContain('/company/usage?view=limits');
    cleanup();
    show(<RiskPage search={{}} />, '/company/risk');
    await screen.findByText('No risks yet');
    const risk = tabHrefs('Risk');
    expect(risk).toEqual(['/company/risk', '/company/risk/disputes']);
    for (const h of [...strategy, ...risk]) expect(addresses.some((re) => re.test(h.split('?')[0] ?? h)), h).toBe(true);
  });
});

describe('Products and Risk', () => {
  it('opens a product from its row, and says so when there are none', async () => {
    serve(nothing);
    const list = { products: [{ id: 'pr1', key: 'widget', name: 'Widget', kind: 'software', status: 'live', tax_kind: null, owner_teammate: null, agreements: [], depends_on: [], repositories: [], risks: [], open_matters: 0 }] } as unknown as ProductList;
    queryClient.setQueryData(productsQuery.queryKey, list);
    show(<ProductsPage search={{}} />, '/company/products');
    expect(href(await screen.findByText('Widget'))).toBe('/company/products?panel=widget');
    expect(screen.getByText('Live')).toBeTruthy();
  });

  it("shows a product owner pick at once, and takes it back with the server's reason", async () => {
    let refuse: (r: Reply) => void = () => {};
    serve((_url, method) => (method === 'POST' ? new Promise<Reply>((ok) => (refuse = ok)) : nothing()));
    const ana = { id: 'bot:ana', kind: 'agent', name: 'Ana', slug: 'ana' };
    queryClient.setQueryData(['directory'], { actors: [ana], byId: { 'bot:ana': ana, ana } });
    const list = { products: [{ id: 'pr1', key: 'widget', name: 'Widget', kind: 'software', status: 'live', tax_kind: null, owner_teammate: null, agreements: [], depends_on: [], repositories: [], risks: [], open_matters: 0 }] } as unknown as ProductList;
    queryClient.setQueryData(productsQuery.queryKey, list);
    show(<ProductsPage search={{ panel: 'widget' }} />, '/company/products?panel=widget');
    const panel = await screen.findByRole('complementary', { name: 'Widget' });
    fireEvent.click(within(panel).getByRole('combobox', { name: 'Owner' }));
    const option = await screen.findByRole('option', { name: /Ana/ });
    fireEvent.pointerDown(option, { pointerType: 'mouse' });
    fireEvent.click(option);
    await waitFor(() => expect(queryClient.getQueryData<ProductList>(productsQuery.queryKey)?.products[0]?.owner_teammate).toBe('bot:ana'));
    expect(screen.getByRole('table', { name: 'Products' }).querySelector('.av')).toBeTruthy();
    refuse({ status: 409, body: { error: { code: 'owner_not_found', detail: 'No such teammate' } } });
    await waitFor(() => expect(queryClient.getQueryData<ProductList>(productsQuery.queryKey)?.products[0]?.owner_teammate).toBeNull());
    expect(toasts).toHaveBeenCalledWith({ text: "Couldn't save", name: 'No such teammate', kind: 'failed' });
  });

  it('shows the empty Risk list', async () => {
    serve((url) => (url.includes('/api/v2/risks') ? { status: 200, body: { risks: [] } } : { status: 200, body: { matters: [], next_offset: null } }));
    show(<RiskPage search={{}} />, '/company/risk');
    expect(await screen.findByText('No risks yet')).toBeTruthy();
  });
});
