import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the router's error component, as the app wires it
import { PageFault } from '@/shell/PageFault';
import type { CompanyFloor, GoalTree, ProductList } from '@/api/schema';
import { CompanyPage } from './CompanyPage';
import { ProductsPage } from './ProductsPage';
import { floorQuery, goalsTreeQuery, productsQuery } from './queries';
import { RiskPage } from './RiskPage';
import { loadCompany } from './routing';
import { StrategyPage } from './StrategyPage';

/* Company: the Floor, the Strategy and Risk tabs' addresses, and Products. Sample data only. */

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

/** The page under a route at its own address, so a link to "." resolves as it does in the app. Given as a function, the page gets the address's search, as its route hands it in. */
function show(page: ReactNode | ((search: Record<string, unknown>) => ReactNode), at: string, basepath = '/') {
  function Routed() {
    const search = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <>{typeof page === 'function' ? page(search) : page}</>;
  }
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: Routed });
  const history = createMemoryHistory({ initialEntries: [basepath === '/' ? at : basepath + at] });
  const router = createRouter({ routeTree: root.addChildren([route]), basepath, history, defaultErrorComponent: PageFault });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
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
  it('starts both figure reads while the Floor is still loading', async () => {
    let finishFloor: (reply: Reply) => void = () => {};
    const pendingFloor = new Promise<Reply>((resolve) => (finishFloor = resolve));
    const reads = vi.fn((url: string) => (url === '/api/v2/company/floor' ? pendingFloor : nothing()));
    serve(reads);

    const loaded = loadCompany(queryClient);
    finishFloor({ status: 200, body: floor });

    expect(reads.mock.calls.map(([url]) => url)).toEqual([
      '/api/v2/company/floor',
      '/api/v2/finance/metrics/revenue?days=7',
      expect.stringMatching(/^\/api\/v2\/usage\?group=day&/),
    ]);
    expect(await loaded).toEqual(floor);
  });

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
    expect(href(within(panel).getByRole('link', { name: 'Open in Tasks' }))).toBe('/tasks/p1');
  });

  it('draws each stage on the Floor, opening its tasks in the panel', async () => {
    serve(nothing);
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{}} />, '/company');
    const review = await screen.findByTitle('Review: 1');
    expect(screen.getByRole('link', { name: 'Review: 1, waits on you' })).toBe(review);
    expect(href(review)).toBe('/company?panel=stage:p1:s2');
    expect(within(review).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Engineering' })).toBeTruthy();
    // a long pipeline name is clamped in its column; the full name stays in its title
    expect(screen.getByTitle('Features').textContent).toBe('Features');
  });

  it('loads the rest of a stage using the server cursor', async () => {
    const seen: URLSearchParams[] = [];
    const task = (id: string, title: string) => ({ id, title, turn: null });
    const early = task('t1', 'Check the gift notes');
    serve((url) => {
      if (!url.startsWith('/api/v2/tasks?')) return nothing();
      const params = new URLSearchParams(url.split('?')[1]);
      seen.push(params);
      return {
        status: 200,
        body: params.has('after') ? { tasks: [early, task('t2', 'Check the stock')] } : { tasks: [early], next_after: 'next-page' },
      };
    });
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{ panel: 'stage:p1:s2' }} />, '/company?panel=stage:p1:s2');
    const panel = within(await screen.findByRole('complementary', { name: 'Review' }));
    fireEvent.click(await panel.findByRole('button', { name: 'Load more' }));
    expect(await panel.findByRole('link', { name: 'Check the stock' })).toBeTruthy();
    expect(panel.getAllByRole('link', { name: 'Check the gift notes' })).toHaveLength(1);
    expect(panel.queryByRole('button', { name: 'Load more' })).toBeNull();
    expect(Object.fromEntries(seen[1]!)).toMatchObject({ pipeline: 'p1', stage: 's2', snoozed: 'all', after: 'next-page' });
  });

  it('says so when nothing runs yet, and offers the owner Set up @s248', async () => {
    serve(nothing);
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
    queryClient.setQueryData(floorQuery.queryKey, { factories: [] } as unknown as CompanyFloor);
    show(<CompanyPage search={{}} />, '/company');
    expect(await screen.findByText('Nothing is running yet')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Set up' })).toBeTruthy();
  });

  it('shows the mock’s Revenue and Agent spend, and no Goals or Needs you figures @s248', async () => {
    serve((url) =>
      url.includes('/finance/metrics/revenue')
        ? { status: 200, body: { value: 48210, unit: 'usd' } }
        : url.includes('/api/v2/usage')
          ? { status: 200, body: { rows: [], totals: { est_cost_usd: 1284 } } }
          : nothing(),
    );
    queryClient.setQueryData(floorQuery.queryKey, floor);
    show(<CompanyPage search={{}} />, '/company');
    const figs = await screen.findByRole('group', { name: 'Company figures' });
    await waitFor(() => expect(figs.textContent).toContain('$1,284'));
    expect(figs.textContent).toContain('$48,210');
    expect(figs.textContent).not.toMatch(/Goals behind|Needs you/);
  });

  it('says no access, without Try again, when the Floor is closed to the reader', async () => {
    serve((url) => (url.includes('/company/floor') ? { status: 403, body: { detail: 'forbidden' } } : nothing()));
    show(<CompanyPage search={{}} />, '/company');
    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('shows the fault with Try again when the Floor fails', async () => {
    serve((url) => (url.includes('/company/floor') ? { status: 500, body: { detail: 'boom' } } : nothing()));
    show(<CompanyPage search={{}} />, '/company');
    expect(await screen.findByText('This page did not load')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it("opens a hall manager's chat beside the Floor, at the address @s291", async () => {
    serve(nothing);
    const em = { id: 'bot:em', kind: 'agent', name: 'Engineering Manager' };
    queryClient.setQueryData(['directory'], { actors: [em], byId: { 'bot:em': em } });
    queryClient.setQueryData(floorQuery.queryKey, { factories: [{ id: 'eng', manager_id: 'bot:em', name: 'Engineering', pipelines: [] }] } as unknown as CompanyFloor);
    const router = show(<CompanyPage search={{}} />, '/company');
    const face = await screen.findByRole('link', { name: 'Chat with Engineering Manager' });
    expect(href(face)).toBe('/company?chat=agent:em');
    fireEvent.click(face);
    await waitFor(() => expect(router.state.location.search).toEqual({ chat: 'agent:em' }));
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
  it('sends every Strategy and Risk tab to an address the app has; an empty Risk list says so', async () => {
    serve((url) => (url.includes('/api/v2/risks') ? ok({ risks: [] }) : ok({ matters: [], next_offset: null })));
    const goal = { id: 'g1', title: 'Grow signups', owner_id: 'company', parent_id: null, rank: 0, status: null, kpis: [], open_tasks: 0 };
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [goal], other_kpis: [], owners: {}, proposals: [], unaligned: {} } as unknown as GoalTree);
    show(<StrategyPage tab="goals" />, '/company/strategy/goals');
    await screen.findByText('Grow signups');
    const strategy = tabHrefs('Strategy');
    expect(strategy).toHaveLength(6);
    // Budget is the agents' spend limits, in Usage (old #/strategy/budget)
    expect(strategy).toContain('/company/usage?view=limits');
    for (const h of [...strategy, '/company/risk', '/company/risk/disputes']) expect(addresses.some((re) => re.test(h.split('?')[0] ?? h)), h).toBe(true);
    cleanup();
    // Risk switches to Disputes in its head, not with a tab row
    show(<RiskPage search={{}} />, '/company/risk');
    await screen.findByText('No risks yet');
    const shown = screen.getByRole('group', { name: 'Show' });
    expect(within(shown).getByRole('button', { name: 'Risks' }).getAttribute('aria-pressed')).toBe('true');
    expect(within(shown).getByRole('button', { name: 'Disputes' })).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Risk pages' })).toBeNull();
  });
});

// ---------------------------------------------------------------- Products

const product = {
  id: 'product/garden-kit',
  key: 'garden-kit',
  name: 'Garden kit',
  kind: 'goods',
  status: 'live',
  tax_kind: 'goods',
  owner_teammate: null,
  agreements: [{ id: 'document/terms', name: 'Terms of sale', link_id: 'l1', file: { id: 'f1', title: 'Terms of sale, v4', page: true } }],
  depends_on: [{ id: 'organization/oaks', name: 'Tall Oaks Timber', link_id: 'l2' }],
  repositories: ['fieldkit/storefront'],
  risks: [],
  open_matters: 1,
};
const products = (...list: (typeof product)[]) => queryClient.setQueryData(productsQuery.queryKey, { products: list } as unknown as ProductList);
const productsPage = (s: Record<string, unknown>) => <ProductsPage search={s} />;

describe('Products @n:re:products @n:re:product', () => {
  it('opens a product from its row', async () => {
    serve(nothing);
    products(product);
    show(productsPage, '/company/products');
    expect(href(await screen.findByText('Garden kit'))).toBe('/company/products?panel=garden-kit');
    expect(screen.getByText('Live')).toBeTruthy();
  });

  it('says so when there are none, then adds a product from its panel and closes it', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ product: { id: 'pr2', key: 'gadget', name: 'Gadget' } }) : nothing()));
    products();
    const router = show(productsPage, '/company/products');
    expect(await screen.findByText('No products yet')).toBeTruthy();
    fireEvent.click(screen.getAllByRole('button', { name: 'Add product' })[0]!);
    const panel = await screen.findByRole('complementary', { name: 'New product' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Gadget' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/products', method: 'POST', body: { name: 'Gadget', status: 'planned' } }]));
    await waitFor(() => expect(router.state.location.search).toEqual({}));
  });

  it("shows a product owner pick at once, and takes it back with the server's reason", async () => {
    let refuse: (r: Reply) => void = () => {};
    serve((_url, method) => (method === 'POST' ? new Promise<Reply>((done) => (refuse = done)) : nothing()));
    const ana = { id: 'bot:ana', kind: 'agent', name: 'Ana', slug: 'ana' };
    queryClient.setQueryData(['directory'], { actors: [ana], byId: { 'bot:ana': ana, ana } });
    products(product);
    show(productsPage, '/company/products?panel=garden-kit');
    const panel = await screen.findByRole('complementary', { name: 'Garden kit' });
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

  it('opens a different product without carrying over an unfinished rename', async () => {
    const sent = serve(nothing);
    products(product, { ...product, id: 'product/tool-kit', key: 'tool-kit', name: 'Tool kit' });
    show(productsPage, '/company/products?panel=garden-kit');
    const first = within(await screen.findByRole('complementary', { name: 'Garden kit' }));
    fireEvent.click(first.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }));
    fireEvent.change(first.getByRole('textbox', { name: 'Name' }), { target: { value: 'Raised bed kit' } });
    fireEvent.click(screen.getByRole('link', { name: 'Tool kit' }));
    const panel = within(await screen.findByRole('complementary', { name: 'Tool kit' }));
    expect(panel.queryByRole('textbox', { name: 'Name' })).toBeNull();
    expect(sent).toEqual([]);
  });

  it('adds and removes an agreement, a repository and a vendor, the whole set sent', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ product }) : nothing()));
    products(product);
    show(productsPage, '/company/products?panel=garden-kit', '/next');
    const panel = await screen.findByRole('complementary', { name: 'Garden kit' });
    expect(href(within(panel).getByText('Terms of sale, v4'))).toBe('/next/library/f1');
    expect(href(within(panel).getByText('Tall Oaks Timber'))).toBe('/next/library/contacts?type=organization&panel=organization/oaks');
    fireEvent.click(within(panel).getByRole('button', { name: 'Add an agreement' }));
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Agreement' }), { target: { value: 'Rental terms' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Add' }));
    await waitFor(() => expect(sent[0]).toEqual({ url: '/api/v2/products/garden-kit', method: 'POST', body: { agreements: ['document/terms', 'Rental terms'] } }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove Tall Oaks Timber' }));
    await waitFor(() => expect(sent[1]).toEqual({ url: '/api/v2/products/garden-kit', method: 'POST', body: { depends_on: [] } }));
    fireEvent.click(within(panel).getByRole('button', { name: 'Remove fieldkit/storefront' }));
    await waitFor(() => expect(sent[2]).toEqual({ url: '/api/v2/products/garden-kit', method: 'POST', body: { repositories: [] } }));
  });

  it('renames from ⋯, its record holding Agreement, Code and Depends on only', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ product: { ...product, name: 'Raised bed kit' } }) : nothing()));
    products(product);
    show(productsPage, '/company/products?panel=garden-kit');
    const panel = await screen.findByRole('complementary', { name: 'Garden kit' });
    expect(within(panel).getAllByRole('region').map((r) => r.getAttribute('aria-label'))).toEqual(['Agreement', 'Code', 'Depends on']);
    fireEvent.click(within(panel).getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }));
    const name = within(panel).getByRole('textbox', { name: 'Name' });
    fireEvent.change(name, { target: { value: 'Raised bed kit' } });
    fireEvent.submit(name.closest('form')!);
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/products/garden-kit', method: 'POST', body: { name: 'Raised bed kit' } }]));
  });
});
