import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient, type Actor } from '@/data';
import { Toasts } from '@/kit/Toast';
import './actions';
import { FinancePage } from './Finance';
import { GrowthPage } from './Growth';
import { accessQuery, type GrowthRead } from './queries';
import { validateFinanceSearch, validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Tier 2 gaps: View as lands on the P&L and always keeps Stop; a line's split history; a Growth channel's "Where this
   comes from" and cost chart; the Sources tab gone; Import history open to Tico. Made-up figures, keys and names only. */

const posted: { path: string; body: Record<string, unknown> }[] = [];
type Reply = (url: string) => unknown;

/** Reads answered by `reply` (undefined: not found); every POST is kept and committed. */
function serve(reply: Reply) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if ((init?.method ?? 'GET') === 'POST') {
      const body = JSON.parse(String(init?.body ?? '{}')) as Record<string, unknown>;
      posted.push({ path: new URL(u, 'http://x').pathname.replace('/api/v2', ''), body });
      return json({ kind: body.kind, status: 'committed' });
    }
    const hit = reply(u);
    return hit === undefined ? json({}, 404) : json(hit);
  });
}

function show(page: 'finance' | 'growth', at: string) {
  const Page = () => {
    const search = useSearch({ strict: false });
    return page === 'finance' ? <FinancePage search={validateFinanceSearch(search)} /> : <GrowthPage search={validateGrowthSearch(search)} />;
  };
  const router = createRouter({ routeTree: createRootRoute({ component: Page }), history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const person = (id: string, name: string): Actor => ({ id, kind: 'person', name });
function directory(...actors: Actor[]) {
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
}

/** The owner; Sam sees his own expenses (a P&L), Kim only her budgets (no P&L). */
const ACCESS = {
  can_view_as: true,
  can_edit_defaults: false,
  teams: [],
  teammates: [
    { teammate: 'human:me', kind: 'person', default: 'everything', rights: [] },
    { teammate: 'human:sam', kind: 'person', default: 'own_expenses', rights: [] },
    { teammate: 'human:kim', kind: 'person', default: 'none', rights: [], budgets: 1 },
  ],
};
const finance = (u: string) => (u.endsWith('/finance/access') ? ACCESS : undefined);

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  posted.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  directory(person('human:me', 'Me'), person('human:sam', 'Sam'), person('human:kim', 'Kim'), person('human:lee', 'Lee'));
});
afterEach(() => {
  vi.unstubAllGlobals();
  bind({ router: null });
});

// ---------------------------------------------------------------- Finance

describe('View as @s456', () => {
  it('View on Access lands on the teammate’s P&L, not on Access @s456', async () => {
    serve(finance);
    const router = show('finance', '/company/finance?tab=access');
    fireEvent.click(await screen.findByRole('button', { name: 'View as' }));
    const dialog = await screen.findByRole('dialog', { name: 'View as' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Teammate' }));
    const sam = await screen.findByRole('option', { name: 'Sam' });
    fireEvent.pointerDown(sam, { pointerType: 'mouse' });
    fireEvent.click(sam);
    fireEvent.click(within(dialog).getByRole('button', { name: 'View' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ as: 'human:sam', tab: 'pnl' }));
  });

  it('a teammate with no P&L lands on their first page @s456', async () => {
    serve(finance);
    const router = show('finance', '/company/finance?tab=access');
    queryClient.setQueryData(accessQuery.queryKey, ACCESS as never);
    expect(await run('finance.viewAs', { teammate: 'human:kim' }, { via: 'click' })).toMatchObject({ ok: true });
    await waitFor(() => expect(router.state.location.search).toEqual({ as: 'human:kim' }));
  });

  it('a page closed to the teammate still offers Stop @s456', async () => {
    serve(finance);
    const router = show('finance', '/company/finance?tab=access&as=human:sam');
    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Stop' }));
    await waitFor(() => expect((router.state.location.search as { as?: string }).as).toBeUndefined());
  });
});

describe('Attributed to @s458', () => {
  it('lists the line’s past splits, newest first, with who set each @s458', async () => {
    const line = {
      line_id: 't1',
      amount: -1000,
      can_change: true,
      splits: [{ teammate: 'human:lee', team: '', share_bps: 10000, amount: -1000 }],
      source: 'person',
      set_by: 'human:me',
      set_at: '2026-10-05T10:00:00Z',
      rule_id: null,
      rule_name: null,
      tags: {},
      history: [
        { id: 'h2', set_at: '2026-10-05T10:00:00Z', set_by: 'human:me', source: 'person', rule_id: null, superseded_at: null, splits: [{ teammate: 'human:lee', team: '', share_bps: 10000 }] },
        { id: 'h1', set_at: '2026-10-02T10:00:00Z', set_by: '', source: 'tags', rule_id: null, superseded_at: '2026-10-05T10:00:00Z', splits: [] },
      ],
    };
    serve((u) =>
      u.endsWith('/finance/access')
        ? ACCESS
        : u.includes('/attribution')
          ? line
          : u.includes('/finance/transactions')
            ? { rows: [{ id: 't1', kind: 'entry', entry_id: 'e_t1', date: '2026-10-02', description: 'Ink Co', account: 'Software', amount: -1000 }], next: null }
            : undefined,
    );
    show('finance', '/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Ink Co' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Attributed to' }));
    const dialog = await screen.findByRole('dialog', { name: 'Attributed to' });
    const history = (await within(dialog).findByText('History')).closest('details') as HTMLElement;
    const rows = [...history.querySelectorAll('.prow')].map((r) => [r.querySelector('.row-title')?.textContent, r.querySelector('.row-sub')?.textContent]);
    expect(rows[0]).toEqual(['Lee 100%', expect.stringContaining('Me')]);
    expect(rows[1]).toEqual(['No split', expect.stringContaining('Tags')]);
  });
});

describe('Import history @s465', () => {
  it('is open to Tico: a posted month can be undone, and a non-owner’s import waits for the owner', async () => {
    serve(finance);
    show('finance', '/company/finance?tab=balance');
    const r = await run('finance.importHistory', { csv: 'month,line,amount\n2026-01,Sales,100', basis: 'accrual', mapping: { Sales: 'acc_1' } }, { via: 'tico' });
    expect(r).toMatchObject({ ok: true });
    expect(posted.map((p) => p.body.kind)).toEqual(['import_raw_totals']);
  });
});

// ---------------------------------------------------------------- Growth

const fig = (value: number | null, more: object = {}) => ({ value, ...more });
const point = (period: string, cac: number | null) => ({ period, signups: null, new_customers: 10, cost: cac && cac * 10, cac, kept: null });
const CHANNEL: GrowthRead = {
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
  series: [point('2026-08', 40), point('2026-09', 45), point('2026-10', 50)],
  campaigns: {},
  more: [],
};
const growth = (u: string) =>
  u.includes('/growth/segments') ? { segments: [] } : u.includes('/page-owners') ? { pages: {} } : u.includes('/growth/read') ? CHANNEL : undefined;

describe('Growth channel @s444', () => {
  it('says where its numbers come from, from the read, and draws its cost per customer by month @s444', async () => {
    serve(growth);
    show('growth', '/company/growth?month=2026-10&panel=channel:search');
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

describe('Growth has no Sources tab', () => {
  it('an old Sources address lands on the board', async () => {
    serve(growth);
    const router = show('growth', '/company/growth?tab=sources&panel=source:src1');
    expect(await screen.findByRole('link', { name: 'Search ads' })).toBeTruthy();
    expect(screen.queryByRole('table', { name: 'Sources' })).toBeNull();
    expect(validateGrowthSearch(router.state.location.search)).toEqual({});
  });
});
