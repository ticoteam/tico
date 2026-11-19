import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import type { MatterRow, ProductList, RiskDetail } from '@/api/schema';
import { ProductsPage } from './ProductsPage';
import {
  brandProposalsQuery,
  brandQuery,
  goalNeedsQuery,
  goalsTreeQuery,
  groupsQuery,
  matterDatesQuery,
  matterFilesQuery,
  mattersQuery,
  productsQuery,
  riskQuery,
  risksQuery,
  waitingTasksQuery,
  type Brand,
  type GoalNeed,
  type Team,
} from './queries';
import { RiskPage, type RiskView } from './RiskPage';
import { StrategyPage, type StrategyTab } from './StrategyPage';

/* The tier 2 gaps closed on Company: Goals' Needs you, one risk, one product, the brand rail, a market segment's asks,
   one matter, Disputes, the risk list's marks, and Org's built-in group. Sample rows only. */

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
const toasts = vi.hoisted(() => vi.fn());
vi.mock('@/kit/Toast', async (real) => ({ ...(await real<object>()), toast: toasts }));

const person = { id: 'human:me', email: '', role: 'person', slug: null };
beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  queryClient.clear();
  toasts.mockClear();
  queryClient.setQueryData(['me'], person);
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

type Reply = { status: number; body: unknown };
type Sent = { url: string; method: string; body: unknown };
/** Answers every read with `reply`, and keeps each write (a file upload as 'form'). */
function serve(reply: (url: string, method: string) => Reply): Sent[] {
  const sent: Sent[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method !== 'GET') sent.push({ url: String(url), method, body: init?.body instanceof FormData ? 'form' : init?.body ? JSON.parse(String(init.body)) : null });
    const r = reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}
const nothing = (): Reply => ({ status: 404, body: { detail: 'not_found' } });
const ok = (body: unknown = {}): Reply => ({ status: 200, body });

function show(page: (search: Record<string, unknown>) => ReactNode, at: string, basepath = '/') {
  function Routed() {
    const search = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <>{page(search)}</>;
  }
  const root = createRootRoute({ component: Outlet });
  const route = createRoute({ getParentRoute: () => root, path: at.split('?')[0] ?? '/', component: Routed });
  const router = createRouter({ routeTree: root.addChildren([route]), basepath, history: createMemoryHistory({ initialEntries: [basepath === '/' ? at : basepath + at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const strategy = (tab: StrategyTab) => (search: Record<string, unknown>) => <StrategyPage tab={tab} search={search} />;
const riskPage = (view: RiskView = 'risks') => (s: Record<string, unknown>) => <RiskPage view={view} search={s} />;
const href = (el: HTMLElement) => decodeURIComponent(el.closest('a')?.getAttribute('href') ?? '');
const recent = new Date(Date.now() - 3_600_000).toISOString();

// ---------------------------------------------------------------- 1. Goals: Needs you

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

describe('Goals: Needs you @s366', () => {
  it('shows one quiet line over the tree that opens what waits on you, each opening where it is done', async () => {
    serve(nothing);
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], proposals: [], other_kpis: [], owners: {} } as never);
    queryClient.setQueryData(goalNeedsQuery.queryKey, needs);
    const router = show(strategy('goals'), '/company/strategy/goals');
    const line = await screen.findByText('4 waiting on you');
    fireEvent.click(line);
    await waitFor(() => expect(router.state.location.search).toEqual({ panel: 'needs' }));
    const panel = await screen.findByRole('complementary', { name: 'Needs you' });
    expect(within(panel).getByRole('region', { name: 'A change to the brand book' })).toBeTruthy();
    expect(within(panel).getByText('Refills fell two weeks running')).toBeTruthy();
    expect(href(within(panel).getByText('Refill orders on Grow refills'))).toBe('/company/strategy/goals?panel=kpi:k1');
    expect(href(within(panel).getByText('Say what this company is for'))).toBe('/company/strategy');
  });

  it('accepts a suggested brand change and approves the second option of a recovery, each leaving at once', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ proposal: { id: 'p' } }) : nothing()));
    queryClient.setQueryData(goalsTreeQuery.queryKey, { goals: [], proposals: [], other_kpis: [], owners: {} } as never);
    queryClient.setQueryData(goalNeedsQuery.queryKey, needs);
    show(strategy('goals'), '/company/strategy/goals?panel=needs');
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

// ---------------------------------------------------------------- Risk: sample rows

const matter = (id: string, title: string, over: { status?: string; amount?: number; created?: string; due?: string; party?: boolean } = {}): MatterRow => ({
  task: { id, title, status: over.status ?? 'open', stage: 'Evidence', owner_id: null, created_at: over.created ?? '2026-10-01T10:00:00Z', due_at: over.due ?? null, pipeline_id: 'p1', number: 7 },
  risks: [{ id: 'r1', key: 'risk:chargebacks', label: 'Chargebacks' }],
  matter: {
    kind: 'dispute',
    amount: over.amount ?? 41200,
    currency: 'USD',
    claim: 'Item not received',
    forum: 'Stripe',
    side: 'theirs',
    party: over.party === false ? null : { id: 'person/c9', name: 'Pat Doe' },
    product: { id: 'product/garden-kit', name: 'Garden kit' },
    created_at: over.created ?? '2026-10-01T10:00:00Z',
    version: 2,
  } as unknown as MatterRow['matter'],
});
const risk = (over: Record<string, unknown> = {}) => ({ id: 'r1', key: 'risk:chargebacks', kind: 'risk', label: 'Chargebacks', level: 'live', open: 1, closed: 0, claimed: [], has_rules: false, next_date: null, owner: null, version: 3, waits: false, ...over });
const date = (over: Record<string, unknown> = {}) => ({
  id: 'd1',
  title: 'Send evidence to Stripe',
  basis: 'contract',
  due_on: '2026-11-24',
  group: 'next',
  state: 'due',
  mark: null,
  can_satisfy: true,
  can_waive: true,
  risks: [{ id: 'r1', key: 'risk:chargebacks', label: 'Chargebacks' }],
  rule: { id: 'ru1', kind: 'rule', source: null, text: 'Send evidence within 7 days' },
  task: { id: 't1', label: null, status: 'open', title: 'Refund dispute A' },
  ...over,
});
const detail = (over: Partial<RiskDetail> = {}) =>
  ({ risk: risk({ markdown: 'Most say the kit did not arrive.' }), editable: true, matters: [matter('t1', 'Refund dispute A')], next_offset: null, obligations: [date()], rules: [], sources: [], coverage: [], ...over }) as unknown as RiskDetail;

// ---------------------------------------------------------------- 2. One risk: Add matter, Edit, a date's marks

describe('One risk @n:re:risk-one', () => {
  it('adds a matter: a task carrying the risk, then its kind', async () => {
    const sent = serve((url, method) => (method === 'POST' && url === '/api/v2/tasks' ? ok({ task: { id: 't9' } }) : method === 'POST' ? ok() : nothing()));
    queryClient.setQueryData(riskQuery('r1').queryKey, detail());
    const router = show(riskPage(), '/company/risk?risk=r1');
    fireEvent.click(await screen.findByRole('button', { name: 'Add matter' }));
    const panel = await screen.findByRole('complementary', { name: 'New matter' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Refund dispute B' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(sent).toEqual([
        { url: '/api/v2/tasks', method: 'POST', body: { title: 'Refund dispute B', labels: ['risk:chargebacks'] } },
        { url: '/api/v2/tasks/t9/matter', method: 'POST', body: { kind: 'dispute' } },
      ]),
    );
    await waitFor(() => expect(router.state.location.search).toEqual({ risk: 'r1' }));
  });

  it("edits the risk's name and page from ⋯, for those who may", async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ risk: risk() }) : nothing()));
    queryClient.setQueryData(riskQuery('r1').queryKey, detail());
    show(riskPage(), '/company/risk?risk=r1');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit' }));
    const panel = await screen.findByRole('complementary', { name: 'Edit risk' });
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Name' }), { target: { value: 'Card chargebacks' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/risks/r1', method: 'POST', body: { label: 'Card chargebacks', markdown: 'Most say the kit did not arrive.', version: 3 } }]));
  });

  it('opens a date and adds its proof as a link; a waiver is the person’s own', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok() : nothing()));
    queryClient.setQueryData(riskQuery('r1').queryKey, detail());
    show(riskPage(), '/company/risk?risk=r1');
    const row = within(await screen.findByRole('region', { name: 'Dates' })).getByText('Send evidence to Stripe');
    expect(href(row)).toBe('/company/risk?risk=r1&panel=due:d1:2026-11-24');
    fireEvent.click(row);
    const panel = await screen.findByRole('complementary', { name: 'Send evidence to Stripe' });
    expect(within(panel).getByRole('button', { name: 'Waive' })).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Missed' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Add proof' }));
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Link' }), { target: { value: 'https://stripe.example/case/1' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/rule-due/d1/satisfied', method: 'POST', body: { due_on: '2026-11-24', proof: { kind: 'link', ref: 'https://stripe.example/case/1' } } }]));
    bind({ router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/company/risk'] }) }) });
    expect(await run('risk.markDate', { risk: 'r1', date: 'd1', on: '2026-11-24', how: 'waived', note: 'Settled' }, { via: 'tico' })).toMatchObject({ ok: false });
  });
});

// ---------------------------------------------------------------- 3. One product

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

describe('One product @n:re:product', () => {
  it('adds and removes an agreement, a repository and a vendor, the whole set sent', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ product }) : nothing()));
    queryClient.setQueryData(productsQuery.queryKey, { products: [product] } as unknown as ProductList);
    show((s) => <ProductsPage search={s} />, '/company/products?panel=garden-kit', '/next');
    const panel = await screen.findByRole('complementary', { name: 'Garden kit' });
    expect(href(within(panel).getByText('Terms of sale, v4'))).toBe('/next/library/f1');
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
    queryClient.setQueryData(productsQuery.queryKey, { products: [product] } as unknown as ProductList);
    show((s) => <ProductsPage search={s} />, '/company/products?panel=garden-kit');
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

// ---------------------------------------------------------------- 4. Brand edit: suggested changes

const book: Brand = { published: { n: 2, body: { name: 'Fieldkit Co' } }, draft: null, versions: [], may_edit: true };

describe('Brand edit @s308', () => {
  it('adds a suggested change to the draft from the rail', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ proposal: { id: 'bp1' } }) : nothing()));
    queryClient.setQueryData(brandQuery.queryKey, book);
    queryClient.setQueryData(brandProposalsQuery.queryKey, [{ id: 'bp1', kind: 'brand_draft', reason: 'Say kit, not bundle', proposed_by: 'bot:writer', proposed_at: '2026-11-18T10:00:00Z' }]);
    show(strategy('brand'), '/company/strategy/brand?edit=true');
    const rail = await screen.findByRole('region', { name: 'Suggested' });
    expect(within(rail).getByText('Say kit, not bundle')).toBeTruthy();
    fireEvent.click(within(rail).getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/proposals/bp1/decide', method: 'POST', body: { decision: 'confirm' } }]));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Suggested' })).toBeNull());
  });

});

// ---------------------------------------------------------------- 6. One matter: dates, files, party and product

describe('One matter @n:re:matter', () => {
  it('links its party and product, lists its dates and files, and adds a file', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ file_id: 'f2', link: '', version: 1, file: {} }) : nothing()));
    queryClient.setQueryData(risksQuery.queryKey, { risks: [risk()] } as never);
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    queryClient.setQueryData(matterDatesQuery('r1').queryKey, [date(), date({ id: 'd2', title: 'Another matter’s date', task: { id: 't8', label: null, status: 'open', title: 'Other' } })] as never);
    queryClient.setQueryData(matterFilesQuery('t1').queryKey, [{ id: 'f1', name: 'tracking.pdf', title: 'tracking.pdf', archived: false }] as never);
    show(riskPage(), '/company/risk?panel=matter:t1');
    const panel = await screen.findByRole('complementary', { name: 'Refund dispute A' });
    expect(href(within(panel).getByText('Pat Doe'))).toBe('/library/contacts?contact=person/c9');
    expect(href(within(panel).getByText('Garden kit'))).toBe('/company/products?panel=garden-kit');
    const dates = within(panel).getByRole('region', { name: 'Dates' });
    expect(href(within(dates).getByText('Send evidence to Stripe'))).toBe('/company/risk?risk=r1&panel=due:d1:2026-11-24');
    expect(within(dates).queryByText('Another matter’s date')).toBeNull();
    const files = within(panel).getByRole('region', { name: 'Files' });
    expect(within(files).getByText('tracking.pdf').closest('a')?.getAttribute('href')).toBe('/api/v2/files/f1');
    const input = files.querySelector('input[type=file]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['x'], 'photo.jpg', { type: 'image/jpeg' })] } });
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/tasks/t1/files', method: 'POST', body: 'form' }]));
  });
});

describe("A matter's own facts @n:re:matter", () => {
  function open() {
    queryClient.setQueryData(risksQuery.queryKey, { risks: [risk()] } as never);
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    queryClient.setQueryData(productsQuery.queryKey, { products: [product, { ...product, id: 'product/seed-box', key: 'seed-box', name: 'Seed box' }] } as unknown as ProductList);
    show(riskPage(), '/company/risk?panel=matter:t1');
    return screen.findByRole('complementary', { name: 'Refund dispute A' });
  }
  const posted = (sent: Sent[]) => sent.filter((x) => x.url === '/api/v2/tasks/t1/matter').map((x) => x.body);

  it('changes kind, amount and forum in place, each saved at once with the version read', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ task_id: 't1', matter: null }) : nothing()));
    const panel = await open();
    fireEvent.click(within(panel).getByRole('combobox', { name: 'Kind' }));
    const request = await screen.findByRole('option', { name: /Request/ });
    fireEvent.pointerDown(request, { pointerType: 'mouse' });
    fireEvent.click(request);
    await waitFor(() => expect(posted(sent)).toEqual([{ kind: 'request', version: 2 }]));
    fireEvent.click(within(panel).getByRole('button', { name: '$412' }));
    const amount = within(panel).getByRole('textbox', { name: 'Amount' });
    fireEvent.change(amount, { target: { value: '1,240.50' } });
    fireEvent.submit(amount.closest('form')!);
    await waitFor(() => expect(posted(sent)[1]).toEqual({ amount: 124050, currency: 'USD', version: 2 }));
    expect(await within(panel).findByRole('button', { name: '$1,241' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Stripe' }));
    const forum = within(panel).getByRole('textbox', { name: 'Forum' });
    fireEvent.change(forum, { target: { value: 'Small claims' } });
    fireEvent.submit(forum.closest('form')!);
    await waitFor(() => expect(posted(sent)[2]).toEqual({ forum: 'Small claims', version: 2 }));
  });

  it('picks the party from Contacts and the product from ours, and takes a refused change back', async () => {
    let refuse = false;
    const sent = serve((url, method) =>
      method === 'POST'
        ? refuse
          ? { status: 403, body: { error: { code: 'forbidden' } } }
          : ok({ task_id: 't1', matter: null })
        : url.startsWith('/api/v2/entities/find/person')
          ? ok({ rows: [{ id: 'person/c4', name: 'Sam Lee' }] })
          : url.startsWith('/api/v2/entities/find/organization')
            ? ok({ rows: [] })
            : nothing(),
    );
    const panel = await open();
    fireEvent.click(within(panel).getByRole('button', { name: 'Change party' }));
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Party' }), { target: { value: 'Sam' } });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Sam Lee' }));
    await waitFor(() => expect(posted(sent)).toEqual([{ party_entity_id: 'person/c4', version: 2 }]));
    expect(href(await within(panel).findByText('Sam Lee'))).toBe('/library/contacts?contact=person/c4');
    refuse = true;
    fireEvent.click(within(panel).getByRole('button', { name: 'Change product' }));
    fireEvent.click(within(panel).getByRole('combobox', { name: 'Product' }));
    const seed = await screen.findByRole('option', { name: /Seed box/ });
    fireEvent.pointerDown(seed, { pointerType: 'mouse' });
    fireEvent.click(seed);
    await waitFor(() => expect(posted(sent)[1]).toEqual({ product_id: 'product/seed-box', version: 2 }));
    await waitFor(() => expect(toasts).toHaveBeenCalled());
    expect(href(within(panel).getByText('Garden kit'))).toBe('/company/products?panel=garden-kit');
  });

  it('is open to Tico: ordinary work on a task', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ task_id: 't1', matter: null }) : nothing()));
    bind({ router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/company/risk'] }) }) });
    expect(await run('risk.setMatter', { task: 't1', version: 2, forum: 'PayPal' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(posted(sent)).toEqual([{ forum: 'PayPal', version: 2 }]);
  });
});

// ---------------------------------------------------------------- 7. Disputes

describe('Disputes @n:re:disputes', () => {
  it('shows what is claimed across the open ones, each one’s next date, and New on a new one', async () => {
    serve(nothing);
    queryClient.setQueryData(waitingTasksQuery.queryKey, []);
    queryClient.setQueryData(mattersQuery('disputes').queryKey, {
      matters: [matter('t1', 'Refund dispute A', { due: '2026-11-24T17:00:00Z' }), matter('t2', 'Refund dispute B', { amount: 12900, created: recent }), matter('t3', 'Refund dispute C', { status: 'done' })],
      next_offset: null,
    });
    show(riskPage('disputes'), '/company/risk/disputes');
    const open = await screen.findByRole('region', { name: 'Open' });
    expect(open.textContent).toContain('$541');
    const table = within(open).getByRole('table', { name: 'Open disputes' });
    expect(within(table).getByRole('columnheader', { name: 'Next' })).toBeTruthy();
    expect(within(table).getByText('Refund dispute A').closest('tr')!.textContent).toContain('Nov 24');
    expect(within(within(table).getByText('Refund dispute B').closest('tr')!).getByText('New')).toBeTruthy();
    expect(within(within(table).getByText('Refund dispute A').closest('tr')!).queryByText('New')).toBeNull();
  });
});

// ---------------------------------------------------------------- 8. The risk list's matter rows

describe('Risk list @n:re:risk', () => {
  it('marks a new matter New and one that waits on you with Flow’s mark', async () => {
    serve(nothing);
    queryClient.setQueryData(risksQuery.queryKey, { risks: [risk()] } as never);
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A', { created: recent }), matter('t2', 'Refund dispute B')], next_offset: null });
    queryClient.setQueryData(waitingTasksQuery.queryKey, ['t2']);
    show(riskPage(), '/company/risk');
    const live = await screen.findByRole('region', { name: 'Live' });
    const a = within(live).getByText('Refund dispute A').closest('a')!;
    const b = within(live).getByText('Refund dispute B').closest('a')!;
    expect(within(a).getByText('New')).toBeTruthy();
    expect(within(a).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    expect(within(b).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(within(b).queryByText('New')).toBeNull();
  });
});

// ---------------------------------------------------------------- 9. Org: the built-in group folded

describe('Org @s309', () => {
  it('keeps the Built-in group folded until it is opened', async () => {
    serve(nothing);
    const actors = [
      { id: 'human:ana', kind: 'person', name: 'Ana Ruiz' },
      { id: 'bot:assistant', kind: 'agent', name: 'Assistant' },
    ];
    queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
    const g = (id: string, name: string, over: Partial<Team> = {}): Team => ({ id, name, parent: '', order: 0, people: [], bots: [], bot_slugs: [], ...over });
    queryClient.setQueryData(groupsQuery.queryKey, [g('sales', 'Sales', { order: 1, people: ['ana'] }), g('built-in', 'Built-in', { order: 2, bots: ['assistant'], bot_slugs: ['assistant'] })]);
    show(strategy('org'), '/company/strategy/org');
    const built = await screen.findByRole('region', { name: 'Built-in' });
    expect(within(screen.getByRole('region', { name: 'Sales' })).getByText('Ana Ruiz')).toBeTruthy();
    expect(within(built).queryByText('Assistant')).toBeNull();
    fireEvent.click(within(built).getByRole('button', { name: /^Built-in/ }));
    expect(within(built).getByText('Assistant')).toBeTruthy();
  });
});
