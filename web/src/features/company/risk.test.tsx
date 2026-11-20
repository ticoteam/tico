import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider, useLocation } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
// eslint-disable-next-line no-restricted-imports -- the router's error component, as the app wires it
import { PageFault } from '@/shell/PageFault';
import type { MatterRow, ProductList, RiskDetail, RiskList } from '@/api/schema';
import { matterDatesQuery, matterFilesQuery, mattersQuery, productsQuery, riskQuery, risksQuery, waitingTasksQuery } from './queries';
import { RiskPage, type RiskView } from './RiskPage';

/* Risk: the risk list, one risk, one matter and Disputes. Sample rows only. */

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

/** The Risk page at its own address, reading its search from it as the route does. */
function show(at: string, view: RiskView = 'risks') {
  function Routed() {
    const search = useLocation({ select: (l) => l.search as Record<string, unknown> });
    return <RiskPage view={view} search={search} />;
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
const recent = new Date(Date.now() - 3_600_000).toISOString();
const tico = () => bind({ router: createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ['/company/risk'] }) }) });

const matter = (id: string, title: string, over: { status?: string; amount?: number; forum?: string; created?: string; due?: string } = {}): MatterRow => ({
  task: { id, title, status: over.status ?? 'open', stage: 'Evidence', owner_id: null, created_at: over.created ?? '2026-10-01T10:00:00Z', due_at: over.due ?? null, pipeline_id: 'p1', number: 7 },
  risks: [{ id: 'r1', key: 'risk:chargebacks', label: 'Chargebacks' }],
  matter: {
    kind: 'dispute',
    amount: over.amount ?? 41200,
    currency: 'USD',
    claim: 'Item not received',
    forum: over.forum ?? 'Stripe',
    side: 'theirs',
    party: { id: 'person/c9', name: 'Pat Doe' },
    product: { id: 'product/garden-kit', name: 'Garden kit' },
    created_at: over.created ?? '2026-10-01T10:00:00Z',
    version: 2,
  } as unknown as MatterRow['matter'],
});
const risk = (over: Record<string, unknown> = {}) => ({ id: 'r1', key: 'risk:chargebacks', kind: 'risk', label: 'Chargebacks', level: 'live', open: 1, closed: 0, claimed: [], has_rules: false, next_date: null, owner: null, version: 3, waits: false, ...over });
const risks = () =>
  queryClient.setQueryData(risksQuery.queryKey, {
    risks: [
      risk({ closed: 2, claimed: [{ amount: 41200, currency: 'USD' }], has_rules: true }),
      risk({ id: 'r2', key: 'r2', label: 'Trademark', level: 'open' }),
      risk({ id: 'r3', key: 'r3', label: 'Insurance', level: 'quiet', open: 0, next_date: '2027-03-01' }),
    ],
  } as unknown as RiskList);
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

describe('Risk list @n:re:risk', () => {
  it('groups the risks by level, a live one heading its open matters, each opening where it should', async () => {
    serve(nothing);
    risks();
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    show('/company/risk');
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

  it('marks a new matter New and one that waits on you with Flow’s mark', async () => {
    serve(nothing);
    queryClient.setQueryData(risksQuery.queryKey, { risks: [risk()] } as never);
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A', { created: recent }), matter('t2', 'Refund dispute B')], next_offset: null });
    queryClient.setQueryData(waitingTasksQuery.queryKey, ['t2']);
    show('/company/risk');
    const live = await screen.findByRole('region', { name: 'Live' });
    const a = within(live).getByText('Refund dispute A').closest('a')!;
    const b = within(live).getByText('Refund dispute B').closest('a')!;
    expect(within(a).getByText('New')).toBeTruthy();
    expect(within(a).queryByRole('img', { name: 'Waits on you' })).toBeNull();
    expect(within(b).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    expect(within(b).queryByText('New')).toBeNull();
  });

  it('says so when the risks do not load, with Try again', async () => {
    serve((url) => (url.includes('/api/v2/risks') ? { status: 500, body: { detail: 'boom' } } : ok({ matters: [], next_offset: null })));
    show('/company/risk');
    expect(await screen.findByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('One risk @n:re:risk-one', () => {
  it("reads the rest of a risk's matters past the detail's first page", async () => {
    const asked: string[] = [];
    serve((url) => {
      asked.push(url);
      if (url.startsWith('/api/v2/risks/r1?')) return ok({ risk: risk(), matters: [matter('t1', 'Refund dispute A')], next_offset: 200 });
      if (url.startsWith('/api/v2/matters?')) return ok({ matters: [matter('t2', 'Refund dispute B')], next_offset: null });
      return nothing();
    });
    const d = await queryClient.fetchQuery(riskQuery('r1'));
    expect(d.matters.map((m) => m.task.id)).toEqual(['t1', 't2']);
    expect(asked).toEqual(['/api/v2/risks/r1?limit=200', '/api/v2/matters?risk=r1&limit=200&offset=200']);
  });

  it('opens a risk as its own page: figures, matters, dates, rules, sources and its text', async () => {
    serve(nothing);
    const page = detail({
      risk: risk({ closed: 2, claimed: [{ amount: 41200, currency: 'USD' }], markdown: 'Most say the kit did not arrive.' }),
      editable: false,
      obligations: [{ id: 'o1', rule: { text: 'Send evidence within 7 days' }, basis: 'Contract', mark: null, due_on: '2026-11-24' }],
      rules: [{ link_id: 'l1', id: 'ru1', text: 'A chargeback opens, send evidence' }],
      sources: [{ link_id: 'l2', id: 'document/card-network-rules', name: 'Card network rules' }],
    } as unknown as Partial<RiskDetail>);
    queryClient.setQueryData(riskQuery('r1').queryKey, page);
    show('/company/risk?risk=r1');
    const figures = await screen.findByRole('group', { name: 'Risk figures' });
    expect(figures.textContent).toContain('$412');
    expect(href(within(screen.getByRole('region', { name: 'Matters' })).getByText('Refund dispute A'))).toBe('/company/risk?risk=r1&panel=matter:t1');
    expect(within(screen.getByRole('region', { name: 'Dates' })).getByText('Send evidence within 7 days')).toBeTruthy();
    expect(href(within(screen.getByRole('region', { name: 'Rules' })).getByText('A chargeback opens, send evidence'))).toBe('/company/rules?panel=org:ru1');
    expect(href(within(screen.getByRole('region', { name: 'Sources' })).getByText('Card network rules'))).toBe('/library/contacts?panel=document/card-network-rules');
    expect(screen.getByText('Most say the kit did not arrive.')).toBeTruthy();
    // the owner pick is only for those who may change the risk
    expect(screen.queryByRole('combobox', { name: 'Owner' })).toBeNull();
  });

  it('adds a matter: a task carrying the risk, then its kind', async () => {
    const sent = serve((url, method) => (method === 'POST' && url === '/api/v2/tasks' ? ok({ task: { id: 't9' } }) : method === 'POST' ? ok() : nothing()));
    queryClient.setQueryData(riskQuery('r1').queryKey, detail());
    const router = show('/company/risk?risk=r1');
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
    show('/company/risk?risk=r1');
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
    show('/company/risk?risk=r1');
    const row = within(await screen.findByRole('region', { name: 'Dates' })).getByText('Send evidence to Stripe');
    expect(href(row)).toBe('/company/risk?risk=r1&panel=due:d1:2026-11-24');
    fireEvent.click(row);
    const panel = await screen.findByRole('complementary', { name: 'Send evidence to Stripe' });
    expect(href(within(panel).getByRole('link', { name: 'Send evidence within 7 days' }))).toBe('/company/rules?panel=org:ru1');
    expect(within(panel).getByRole('button', { name: 'Waive' })).toBeTruthy();
    expect(within(panel).getByRole('button', { name: 'Missed' })).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Add proof' }));
    fireEvent.change(within(panel).getByRole('textbox', { name: 'Link' }), { target: { value: 'https://stripe.example/case/1' } });
    fireEvent.click(within(panel).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(sent).toEqual([{ url: '/api/v2/rule-due/d1/satisfied', method: 'POST', body: { due_on: '2026-11-24', proof: { kind: 'link', ref: 'https://stripe.example/case/1' } } }]));
    tico();
    expect(await run('risk.markDate', { risk: 'r1', date: 'd1', on: '2026-11-24', how: 'waived', note: 'Settled' }, { via: 'tico' })).toMatchObject({ ok: false });
  });

  it('keeps unsaved proof with the occurrence it was entered for', async () => {
    const sent = serve(nothing);
    const obligations = [date(), date({ due_on: '2026-12-01', title: 'Send more evidence' })] as RiskDetail['obligations'];
    queryClient.setQueryData(riskQuery('r1').queryKey, detail({ obligations }));
    show('/company/risk?risk=r1&panel=due:d1:2026-11-24');
    const first = within(await screen.findByRole('complementary', { name: 'Send evidence to Stripe' }));
    fireEvent.click(first.getByRole('button', { name: 'Add proof' }));
    fireEvent.change(first.getByRole('textbox', { name: 'Link' }), { target: { value: 'https://example.com/proof' } });
    fireEvent.click(screen.getByRole('link', { name: 'Send more evidence' }));
    const second = within(await screen.findByRole('complementary', { name: 'Send more evidence' }));
    fireEvent.click(second.getByRole('button', { name: 'Add proof' }));
    expect((second.getByRole('textbox', { name: 'Link' }) as HTMLInputElement).value).toBe('');
    expect(sent).toEqual([]);
  });
});

const product = { id: 'product/garden-kit', key: 'garden-kit', name: 'Garden kit' };

describe('One matter @n:re:matter', () => {
  function open() {
    risks();
    queryClient.setQueryData(mattersQuery('open').queryKey, { matters: [matter('t1', 'Refund dispute A')], next_offset: null });
    queryClient.setQueryData(productsQuery.queryKey, { products: [product, { id: 'product/seed-box', key: 'seed-box', name: 'Seed box' }] } as unknown as ProductList);
    show('/company/risk?panel=matter:t1');
    return screen.findByRole('complementary', { name: 'Refund dispute A' });
  }
  const posted = (sent: Sent[]) => sent.filter((x) => x.url === '/api/v2/tasks/t1/matter').map((x) => x.body);

  it('opens in the panel with its facts, its task, its risks, its party and product, its dates and files, and adds a file', async () => {
    const sent = serve((_url, method) => (method === 'POST' ? ok({ file_id: 'f2', link: '', version: 1, file: {} }) : nothing()));
    queryClient.setQueryData(matterDatesQuery('r1').queryKey, [date(), date({ id: 'd2', title: 'Another matter’s date', task: { id: 't8', label: null, status: 'open', title: 'Other' } })] as never);
    queryClient.setQueryData(matterFilesQuery('t1').queryKey, [{ id: 'f1', name: 'tracking.pdf', title: 'tracking.pdf', archived: false }] as never);
    const panel = await open();
    for (const said of ['Dispute', '$412', 'Item not received', 'Theirs', 'Stripe']) expect(within(panel).getByText(said)).toBeTruthy();
    expect(href(within(panel).getByText('Open task'))).toBe('/tasks/p1/7');
    expect(href(within(within(panel).getByRole('region', { name: 'Risks' })).getByText('Chargebacks'))).toBe('/company/risk?risk=r1');
    expect(href(within(panel).getByText('Pat Doe'))).toBe('/library/contacts?panel=person/c9');
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
    expect(href(await within(panel).findByText('Sam Lee'))).toBe('/library/contacts?panel=person/c4');
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
    tico();
    expect(await run('risk.setMatter', { task: 't1', version: 2, forum: 'PayPal' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(posted(sent)).toEqual([{ forum: 'PayPal', version: 2 }]);
  });
});

describe('Risk as Disputes @n:re:disputes', () => {
  it('reads every page of disputes, and the Open total counts them all', async () => {
    const offsets: (string | null)[] = [];
    serve((url) => {
      if (!url.startsWith('/api/v2/matters?')) return nothing();
      const offset = new URLSearchParams(url.split('?')[1]).get('offset');
      offsets.push(offset);
      return offset === '200' ? ok({ matters: [matter('t2', 'Refund dispute B', { amount: 10000 })], next_offset: null }) : ok({ matters: [matter('t1', 'Refund dispute A')], next_offset: 200 });
    });
    show('/company/risk/disputes', 'disputes');
    const open = await screen.findByRole('rowgroup', { name: 'Open disputes' });
    expect(within(open).getByText('Refund dispute B')).toBeTruthy();
    expect(screen.getByText('$512')).toBeTruthy();
    expect(offsets).toEqual(['0', '200']);
  });

  it('tables every dispute, open ones first with what is claimed, each one’s next date and New on a new one, each opening its matter', async () => {
    serve(nothing);
    queryClient.setQueryData(waitingTasksQuery.queryKey, []);
    queryClient.setQueryData(mattersQuery('disputes').queryKey, {
      matters: [
        matter('t1', 'Refund dispute A', { due: '2026-11-24T17:00:00Z' }),
        matter('t2', 'Refund dispute B', { amount: 12900, created: recent }),
        matter('t3', 'Refund dispute C', { status: 'done', forum: 'PayPal' }),
      ],
      next_offset: null,
    });
    show('/company/risk/disputes', 'disputes');
    const open = await screen.findByRole('rowgroup', { name: 'Open disputes' });
    expect(open.textContent).toContain('$541');
    expect(within(screen.getByRole('table', { name: 'Disputes' })).getByRole('columnheader', { name: 'Next' })).toBeTruthy();
    const a = within(open).getByText('Refund dispute A').closest('tr')!;
    for (const said of ['Chargebacks', '$412', 'Stripe', 'Evidence', 'Nov 24']) expect(a.textContent).toContain(said);
    expect(within(a).queryByText('New')).toBeNull();
    expect(within(within(open).getByText('Refund dispute B').closest('tr')!).getByText('New')).toBeTruthy();
    expect(href(within(open).getByText('Refund dispute A'))).toBe('/company/risk/disputes?panel=matter:t1');
    expect(within(screen.getByRole('rowgroup', { name: 'Closed disputes' })).getByText('Refund dispute C')).toBeTruthy();
    // a group's name folds its rows; the head row stays
    fireEvent.click(screen.getByRole('button', { name: /Closed/ }));
    expect(within(screen.getByRole('rowgroup', { name: 'Closed disputes' })).queryByText('Refund dispute C')).toBeNull();
    expect(within(screen.getByRole('group', { name: 'Show' })).getByRole('button', { name: 'Disputes' }).getAttribute('aria-pressed')).toBe('true');
  });
});
