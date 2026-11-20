import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
// eslint-disable-next-line no-restricted-imports -- the test lends its router to dispatch, as the shell's ActionsRoot does
import { bind } from '@/actions/bind';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FinancePage } from './Finance';
import { addMonths, budgetDay, monthSpan, pnlRange } from './model';
import { budgetsKey, pnlQuery, type Budget, type Pnl } from './queries';
import { validateFinanceSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Finance's tabs, budgets, sources and access. Made-up figures and names only. */
type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const gets: string[] = [];
const posted: Record<string, unknown>[] = [];
/** Answers each read from `reply`; every write is kept and answered by `write` (by default, committed). */
function serve(reply: (url: string) => Reply, write: (body: Record<string, unknown>) => Reply | Promise<Reply> = (body) => ok({ kind: body.kind, status: 'committed' })) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
    let r: Reply;
    if (init?.method === 'POST') {
      posted.push(body);
      r = await write(body);
    } else {
      gets.push(String(url));
      r = reply(String(url));
    }
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(at: string) {
  const Finance = () => <FinancePage search={validateFinanceSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Finance }), history: createMemoryHistory({ initialEntries: [at] }) });
  bind({ router });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}
const searchOf = (href: string | null) => Object.fromEntries(new URL(href ?? '', 'http://x').searchParams);
const tabNames = async () => within(await screen.findByRole('navigation', { name: 'Finance pages' })).getAllByRole('link').map((a) => a.textContent);

const ym = budgetDay().slice(0, 7);
const ROW = (account_id: string, name: string): Pnl['rows'][number] => ({
  account_id,
  name,
  type: 'expense',
  section: 'operating',
  code: '6200',
  parent_id: '',
  private: false,
  actual: [0],
  budget: null,
  projection: null,
  total: null,
});
const PNL = (rows: Pnl['rows']): Pnl => ({
  basis: 'accrual',
  columns: [{ start: `${ym}-01`, end: `${ym}-01`, label: ym, state: 'current' }],
  rows,
  start: `${ym}-01`,
  end: `${ym}-01`,
  excluded: false,
  income: [],
  expense: [],
  net: [0],
  period: 'month',
  view: 'pnl',
  view_version: 1,
});
const BUDGET = (id: string, name: string, more: Partial<Budget> = {}): Budget => ({
  id,
  name,
  account_id: 'acc_ads',
  parent_id: null,
  period: 'month',
  active: true,
  state: { amount: 50000, actual: 20000 },
  ...more,
});

/** The owner, and Lee in Sales with a P&L view of Sales the owner gave. */
const OWNER = {
  can_view_as: true,
  can_edit_defaults: true,
  finance_approver: '',
  teams: [{ id: 'tm_sales', name: 'Sales', parent: null, lead: 'human:lee' }],
  teammates: [
    { teammate: 'human:me', kind: 'person', default: 'everything', rights: [] },
    {
      teammate: 'human:lee',
      kind: 'person',
      default: 'own_expenses',
      rights: [],
      team: 'tm_sales',
      may_grant: true,
      grants: [{ id: 'g1', what: 'pnl', whose: 'team', whose_team: 'tm_sales', granted_by: 'human:me', status: 'active', live: true }],
    },
  ],
};
const only = (row: Record<string, unknown>) => ({ teammates: [{ teammate: 'human:me', kind: 'person', rights: [], ...row }] });

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  gets.length = 0;
  posted.length = 0;
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('Finance tabs @s398 @s402', () => {
  it('shows only the tabs the viewer has; a hidden tab says no access @s398', async () => {
    serve((url) => (url.endsWith('/finance/access') ? ok(only({ default: 'team_expenses' })) : { status: 500, body: {} }));
    const router = show('/company/finance?tab=budgets');
    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
    await router.navigate({ to: '/company/finance', search: { tab: 'cards' } as never });
    const names = await tabNames();
    expect(names).toContain('P&L');
    for (const hidden of ['Budgets', 'Balance sheet', 'Tax', 'Sources', 'Access', 'My budget']) expect(names).not.toContain(hidden);
  });

  it('a teammate who has only budgets sees My budget alone, with only their own @s402 @s398', async () => {
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(only({ default: 'none', budgets: 1 }));
      if (url.includes('/finance/budgets')) return ok({ budgets: [BUDGET('b1', 'Tools', { teammate: 'human:me' }), BUDGET('b2', 'Ads', { teammate: 'human:ann' })] });
      return ok({});
    });
    show('/company/finance');
    const table = await screen.findByRole('table', { name: 'My budget' });
    expect(await within(table).findByText('Tools')).toBeTruthy();
    expect(within(table).queryByText('Ads')).toBeNull();
    expect(within(table).queryByRole('link')).toBeNull();
    expect(await tabNames()).toEqual(['My budget']);
    expect(screen.queryByRole('button', { name: 'New budget' })).toBeNull();
  });

  it('the owner opens My budget at its address: their own budgets under the Finance head, not a no-access page', async () => {
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(OWNER);
      if (url.includes('/finance/budgets')) return ok({ budgets: [BUDGET('b1', 'Tools', { teammate: 'human:me' }), BUDGET('b2', 'Ads', { teammate: 'human:lee' })] });
      return ok({});
    });
    show('/company/finance?tab=mine');
    const table = await screen.findByRole('table', { name: 'My budget' });
    expect(await within(table).findByText('Tools')).toBeTruthy();
    expect(within(table).queryByText('Ads')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Finance' })).toBeTruthy();
    expect(screen.queryByText("You don't have access to this page")).toBeNull();
  });

  it('on a phone the P&L head keeps the range; the period and the basis wait in More', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve((url) => (url.endsWith('/finance/access') ? ok(OWNER) : url.includes('/finance/pnl') ? ok(PNL([ROW('acc_ads', 'Ads')])) : ok({})));
      const router = show('/company/finance?tab=pnl');
      expect(await screen.findByRole('button', { name: 'Earlier' })).toBeTruthy();
      expect(screen.queryByRole('button', { name: 'Quarter' })).toBeNull();
      fireEvent.click(screen.getByRole('button', { name: 'More' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Quarter' }));
      await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'pnl', period: 'quarter' }));
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });

  it('shows a projection and lets its line open when every P&L column is in the future', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      const statement = {
        ...PNL([{ ...ROW('acc_ads', 'Ads'), budget: [16000], projection: [12300] }]),
        columns: [{ start: '2040-01-01', end: '2040-01-31', label: '2040-01', state: 'future' }],
      };
      serve((url) => (url.endsWith('/finance/access') ? ok(OWNER) : url.includes('/finance/pnl') ? ok(statement) : ok({})));
      show('/company/finance?tab=pnl&from=2040-01');
      const line = await screen.findByRole('link', { name: /Ads/ });
      expect(within(line).getByText('$123')).toBeTruthy();
      fireEvent.click(line);
      const panel = await screen.findByRole('complementary', { name: 'Ads' });
      expect(within(panel).getByText('Jan plan')).toBeTruthy();
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });
});

describe('Budgets @s404 @s462 @s463', () => {
  const access = (url: string) => (url.endsWith('/finance/access') ? ok(OWNER) : null);

  it('retries the failed budget list on the quarterly view', async () => {
    let listReads = 0;
    serve((url) => {
      if (url.includes('/finance/budgets')) {
        if (++listReads === 1) return { status: 500, body: {} };
        return ok({ budgets: [BUDGET('b1', 'Tools')] });
      }
      if (url.includes('budget_vs_actual')) return ok({ columns: [], budgets: [] });
      return access(url) ?? ok({});
    });
    show('/company/finance?tab=budgets&period=quarter');
    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('link', { name: 'Tools' })).toBeTruthy();
  });

  it('Month, Quarter and Year: a quarter reads each budget against the quarter that holds today @s404', async () => {
    serve(
      (url) =>
        access(url) ??
        (url.includes('budget_vs_actual')
          ? ok({
              columns: [
                { start: '2000-01-01', end: '2000-03-31' },
                { start: `${ym}-01`, end: monthSpan(ym).end },
              ],
              budgets: [{ id: 'b1', budget: [1, 90000], actual: [1, 45000] }],
            })
          : url.includes('/finance/budgets')
            ? ok({ budgets: [BUDGET('b1', 'Tools')] })
            : ok({})),
    );
    const router = show('/company/finance?tab=budgets');
    expect(await screen.findByRole('link', { name: 'Tools' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Earlier' })).toBeNull();
    expect(screen.queryByLabelText('Search budgets')).toBeNull();
    expect(screen.getByRole('columnheader', { name: 'Left' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Quarter' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'budgets', period: 'quarter' }));
    const row = (await screen.findByRole('link', { name: 'Tools' })).closest('tr')!;
    await waitFor(() => expect(within(row).getByText('$900')).toBeTruthy());
    expect(within(row).getAllByText('$450')).toHaveLength(2); // spent, and left
    expect(gets.some((u) => u.includes('budget_vs_actual') && u.includes('period=quarter') && u.includes(`start=${addMonths(ym, -2)}-01`))).toBe(true);
  });

  it('a budget for a teammate and a vendor sits under the line budget, with its warning and month; Tico may not add one @s462', async () => {
    serve((url) => access(url) ?? ok({}));
    show('/company/finance?tab=budgets');
    const next = addMonths(ym, 1);
    const input = { name: 'lee', line: 'acc_ads', amount: 200, under: 'b_ads', teammate: 'human:lee', vendor: 'Acme', warn: 90, period: 'month', from: next } as const;
    expect(await run('budget.addRow', input, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('budget.addRow', input, { via: 'click' })).toMatchObject({ ok: true });
    expect(posted).toEqual([
      {
        kind: 'budget_create',
        payload: {
          name: 'lee',
          period: 'month',
          account_id: 'acc_ads',
          amount: 20000,
          effective_from: `${next}-01`,
          parent_id: 'b_ads',
          teammate: 'human:lee',
          vendor: 'Acme',
          warn_pct: 90,
        },
      },
    ]);
  });

  it('a P&L line with a budget adds a vendor budget under it; one without adds its line budget @s463', async () => {
    serve(
      (url) =>
        access(url) ??
        (url.includes('/finance/lines/')
          ? ok({ account: {}, total: 0, entries: [], paid_by: null })
          : url.includes('/finance/budgets')
            ? ok({ budgets: [BUDGET('b_ads', 'Ads')] })
            : ok({})),
    );
    const range = pnlRange('quarter', undefined);
    queryClient.setQueryData(
      pnlQuery({ start: range.start, end: range.end, period: 'quarter', basis: 'accrual' }).queryKey,
      PNL([ROW('acc_ads', 'Ads'), ROW('acc_soft', 'Software')]),
    );
    queryClient.setQueryData(budgetsKey(budgetDay()), { budgets: [BUDGET('b_ads', 'Ads')] });
    const router = show('/company/finance?tab=pnl&period=quarter&panel=line:acc_ads');
    const panel = await screen.findByRole('complementary', { name: 'Ads' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Vendor budget' }));
    const dialog = await screen.findByRole('dialog', { name: 'Vendor budget' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Vendor' }), { target: { value: 'Acme' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Amount a month' }), { target: { value: '50' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add budget' }));
    await waitFor(() =>
      expect(posted).toEqual([
        { kind: 'budget_create', payload: { name: 'Acme', period: 'month', account_id: 'acc_ads', amount: 5000, effective_from: `${ym}-01`, parent_id: 'b_ads', vendor: 'Acme' } },
      ]),
    );
    await router.navigate({ to: '/company/finance', search: { tab: 'pnl', period: 'quarter', panel: 'line:acc_soft' } as never });
    const soft = await screen.findByRole('complementary', { name: 'Software' });
    expect(await within(soft).findByRole('button', { name: 'Line budget' })).toBeTruthy();
  });

  it('Tico may lower a budget but not raise or stop one @s462', async () => {
    serve((url) => access(url) ?? ok({}));
    show('/company/finance?tab=pnl');
    queryClient.setQueryData(budgetsKey(budgetDay()), { budgets: [BUDGET('b1', 'Tools')] });
    expect(await run('budget.updateRow', { budget: 'b1', amount: 900 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(posted).toEqual([]);
    expect(await run('budget.updateRow', { budget: 'b1', amount: 100 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(posted[0]).toMatchObject({ kind: 'budget_update', payload: { budget_id: 'b1', amount: 10000 } });
    // Stopping a budget lifts its cap: the person's own press.
    expect(await run('budget.removeRow', { budget: 'b1' }, { via: 'tico' })).toMatchObject({ ok: false });
    expect(posted).toHaveLength(1);
  });

  const budget: Budget = { id: 'b1', name: 'Tools', account_id: 'acc_soft', parent_id: null, period: 'month', active: true, state: { amount: 50000, actual: 20000 } };
  const seed = (rows: Pnl['rows'] = []) => {
    queryClient.setQueryData(budgetsKey(budgetDay()), { budgets: [budget] });
    queryClient.setQueryData(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }).queryKey, PNL(rows));
  };

  it('a new amount shows at once and is taken back when refused', async () => {
    let refuse!: () => void;
    serve(
      (url) => (url.includes('budget_vs_actual') ? ok({ columns: [], budgets: [] }) : (access(url) ?? ok({}))),
      () => new Promise<Reply>((r) => (refuse = () => r(ok({ status: 'refused', code: 'over_limit', reason: 'Over the limit' })))),
    );
    seed();
    show('/company/finance?tab=budgets&panel=budget:b1');

    const row = (await screen.findByRole('link', { name: 'Tools' })).closest('tr')!;
    expect(searchOf(row.querySelector('a')!.getAttribute('href'))).toEqual({ tab: 'budgets', panel: 'budget:b1' });
    const field = await screen.findByRole('textbox', { name: /^Budget for / });
    fireEvent.change(field, { target: { value: '900' } });
    fireEvent.submit(field.closest('form')!);

    await waitFor(() => expect(within(row).getByText('$900')).toBeTruthy());
    expect(within(screen.getByRole('complementary', { name: 'Tools' })).getByText('$700')).toBeTruthy();
    refuse();
    await waitFor(() => expect(within(row).getByText('$500')).toBeTruthy());
    expect(within(row).queryByText('$900')).toBeNull();
    expect(await screen.findByText("Couldn't save")).toBeTruthy();
    expect((screen.getByRole('textbox', { name: /^Budget for / }) as HTMLInputElement).value).toBe('500');
  });

  it('a change pressed again after a lost answer sends the same key; after a refusal, a new one', async () => {
    const sent: (string | null)[] = [];
    const answers: ('lost' | 'refused')[] = ['lost', 'refused', 'lost'];
    let release = () => {};
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      if (init?.method === 'POST') {
        sent.push(new Headers(init.headers).get('Idempotency-Key'));
        // Each answer waits for the test, so the change shows first.
        await new Promise<void>((r) => (release = r));
        // A gateway's 502: the answer is lost, and the client does not send it again on its own (only a 503 or a drop).
        if (answers[sent.length - 1] === 'lost') return new Response('Bad gateway', { status: 502 });
        return json({ kind: 'budget_update', status: 'refused', code: 'over_limit', reason: 'Over the limit' });
      }
      if (String(url).includes('budget_vs_actual')) return json({ columns: [], budgets: [] });
      if (String(url).endsWith('/finance/access')) return json(OWNER);
      return json({});
    });
    seed();
    show('/company/finance?tab=budgets&panel=budget:b1');
    const press = async (n: number) => {
      const field = await screen.findByRole('textbox', { name: /^Budget for / });
      await waitFor(() => expect((field as HTMLInputElement).disabled).toBe(false));
      fireEvent.change(field, { target: { value: '900' } });
      fireEvent.submit(field.closest('form')!);
      await waitFor(() => expect(sent).toHaveLength(n));
      await waitFor(() => expect((screen.getByRole('textbox', { name: /^Budget for / }) as HTMLInputElement).value).toBe('900'));
      release();
      await waitFor(() => expect((screen.getByRole('textbox', { name: /^Budget for / }) as HTMLInputElement).value).toBe('500'));
    };
    await press(1);
    await press(2);
    expect(sent[1]).toBe(sent[0]);
    await press(3);
    expect(sent[2]).not.toBe(sent[1]);
  });

  it('read as a teammate, a budget is read only', async () => {
    serve((url) => {
      if (url.includes('budget_vs_actual')) return ok({ columns: [], budgets: [] });
      if (url.includes('/finance/budgets')) return ok({ budgets: [budget] });
      if (url.endsWith('/finance/access'))
        return ok({
          can_view_as: true,
          teammates: [
            { teammate: 'human:me', kind: 'human', default: 'everything', rights: [] },
            { teammate: 'human:lee', kind: 'human', default: 'team_expenses', rights: ['finance.budgets'] },
          ],
        });
      return ok({});
    });
    seed();
    show('/company/finance?tab=budgets&panel=budget:b1&as=human:lee');
    expect(await screen.findByRole('complementary', { name: 'Tools' })).toBeTruthy();
    await waitFor(() => expect(queryClient.getQueryData(['finance', 'access'])).toBeTruthy());
    expect((screen.getByRole('textbox', { name: /^Budget for / }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'New budget' })).toBeNull();
  });
});

describe('Sources @s412 @s475', () => {
  const sources = {
    groups: [
      {
        group: 'banks',
        name: 'Banks',
        count: 2,
        rows: [
          { id: 'cb', kind: 'connection', name: 'Bank feed', provider: 'mercury', sync: { status: 'active' } },
          { id: 'ma1', kind: 'bank_account', name: 'Checking', last4: '0001', account: null, balance: 100000, sync: { status: 'active', connection_id: 'cb' } },
        ],
      },
      { group: 'accounting', name: 'Accounting', count: 1, rows: [{ id: 'qb', kind: 'connection', name: 'Ledger books', provider: 'quickbooks', sync: { status: 'reconnect' } }] },
    ],
  };
  const reply = (url: string) => {
    if (url.endsWith('/finance/access')) return ok(OWNER);
    if (url.endsWith('/finance/sources')) return ok(sources);
    if (url.includes('/finance/balance-sheet')) return ok({ assets: [{ account_id: 'acc_cash', name: 'Operating cash' }], liabilities: [], equity: [] });
    return { status: 404, body: {} };
  };

  it('a source opens beside at its address and syncs now @s412 @s475', async () => {
    serve(reply);
    show('/company/finance?tab=sources');
    const link = await screen.findByRole('link', { name: 'Bank feed' });
    expect(new URL(link.getAttribute('href')!, 'http://x').searchParams.get('panel')).toBe('source:cb');
    fireEvent.click(link);
    const panel = await screen.findByRole('complementary', { name: 'Bank feed' });
    expect(within(panel).getByText('Checking 0001')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Sync now' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'connection_sync', payload: { connection_id: 'cb' } }]));
    expect(await run('finance.syncSource', { connection: 'cb' }, { via: 'tico' })).toMatchObject({ ok: true });
  });

  it('an account is mapped to its books line @s412', async () => {
    serve(reply);
    show('/company/finance?tab=sources&panel=source:ma1');
    const panel = await screen.findByRole('complementary', { name: 'Checking' });
    await pick(await within(panel).findByRole('combobox', { name: 'Books as' }), /^Operating cash$/);
    await waitFor(() => expect(posted).toEqual([{ kind: 'connection_map', payload: { money_account_id: 'ma1', account_id: 'acc_cash' } }]));
  });

  it('books whose sign-in ran out reconnect through their sign-in @s412', async () => {
    serve(reply);
    show('/company/finance?tab=sources&panel=source:qb');
    const panel = await screen.findByRole('complementary', { name: 'Ledger books' });
    expect(within(panel).queryByRole('button', { name: 'Sync now' })).toBeNull();
    fireEvent.click(within(panel).getByRole('button', { name: 'Reconnect' }));
    expect(await screen.findByRole('dialog', { name: 'Connect QuickBooks' })).toBeTruthy();
  });
});

describe('Access @s413 @s475', () => {
  const reply = (url: string) => (url.endsWith('/finance/access') ? ok(OWNER) : ok({}));

  it('a teammate opens beside with what they see, and a view is taken back @s413 @s475', async () => {
    serve(reply);
    show('/company/finance?tab=access');
    const link = await screen.findByRole('link', { name: /lee/ });
    expect(new URL(link.getAttribute('href')!, 'http://x').searchParams.get('panel')).toBe('mate:human:lee');
    fireEvent.click(link);
    const panel = await screen.findByRole('complementary', { name: 'lee' });
    expect(within(panel).getByText('Sales')).toBeTruthy();
    fireEvent.click(within(panel).getByRole('button', { name: 'Take back P&L, Sales' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'view_revoke', payload: { grant_id: 'g1' } }]));
  });

  it('access is given to a teammate for a team @s413', async () => {
    serve(reply);
    show('/company/finance?tab=access');
    fireEvent.click(await screen.findByRole('button', { name: 'Give access' }));
    const dialog = await screen.findByRole('dialog', { name: 'Give access' });
    await pick(within(dialog).getByRole('combobox', { name: 'Teammate' }), /^lee$/);
    await pick(within(dialog).getByRole('combobox', { name: 'What' }), /^P&L$/);
    await pick(within(dialog).getByRole('combobox', { name: 'Whose' }), /^Sales$/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Give access' }));
    await waitFor(() =>
      expect(posted).toEqual([{ kind: 'view_grant', payload: { subject_kind: 'teammate', subject: 'human:lee', what: 'pnl', whose: 'team', whose_team: 'tm_sales' } }]),
    );
  });

  it('the owner names the Finance approver from the page menu @s413', async () => {
    serve(reply);
    show('/company/finance?tab=access');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Finance approver' }));
    const dialog = await screen.findByRole('dialog', { name: 'Finance approver' });
    await pick(within(dialog).getByRole('combobox', { name: 'Finance approver' }), /^lee$/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'finance_approver_set', payload: { approver: 'human:lee' } }]));
  });

  it('giving access and naming the approver stay the person’s own press; taking access back is open to Tico @s413', async () => {
    serve(reply);
    show('/company/finance?tab=access');
    await screen.findByRole('link', { name: /lee/ });
    expect(await run('finance.giveAccess', { teammate: 'human:lee', what: 'pnl' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('finance.setApprover', { approver: 'human:lee' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(posted).toEqual([]);
    expect(await run('finance.takeAccess', { grant: 'g1' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(posted).toEqual([{ kind: 'view_revoke', payload: { grant_id: 'g1' } }]);
  });
});
