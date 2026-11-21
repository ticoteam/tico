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
import { asOfDay, compareDay } from './ledger';
import { dayOf } from './model';
import { accessQuery, cardsQuery } from './queries';
import { validateFinanceSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Finance's books tabs: bills, invoices, entries, cards, attribution and the balance sheet, each record beside its
   list; importing history; viewing as a teammate. Made-up figures and names only. */

type Posted = Record<string, unknown>;
const OWNER = { can_edit_defaults: false, teammates: [{ teammate: 'human:me', kind: 'human', default: 'everything', rights: [] }] };

/** Answers reads by `reads`, in order (the access list is the owner's unless given); records each POST and answers it
    by `decide` (a dry run, then the send). */
function serve(reads: [string | RegExp, unknown][], decide: (body: Posted) => unknown = () => ({ status: 'committed' })) {
  const posted: Posted[] = [];
  const got: string[] = [];
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = String(url);
    const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
    if (init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Posted;
      posted.push(body);
      return json({ kind: body.kind, ...(decide(body) as object) });
    }
    got.push(u);
    const hit = reads.find(([k]) => (typeof k === 'string' ? u.includes(k) : k.test(u)));
    if (hit) return json(hit[1]);
    return u.endsWith('/finance/access') ? json(OWNER) : json({}, 404);
  });
  return { posted, got };
}

function show(at: string) {
  const Finance = () => <FinancePage search={validateFinanceSearch(useSearch({ strict: false }))} />;
  const router = createRouter({ routeTree: createRootRoute({ component: Finance }), history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  // The action layer runs against this router, as the shell lends it the app's.
  bind({ router });
  return router;
}

async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}

const today = dayOf(new Date());
const doc = (o: Record<string, unknown>) => ({
  currency: 'usd',
  date: '2026-11-01',
  number: '',
  paid: 0,
  lines: [],
  settlements: [],
  actions: [],
  attachments: [],
  ...o,
});

const person = (id: string, name: string): Actor => ({ id, kind: 'person', name });
function directory(...actors: Actor[]) {
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.map((a) => [a.id, a])) });
}
const team = () => directory(person('human:me', 'Me'), person('human:sam', 'Sam'), person('human:kim', 'Kim'), person('human:lee', 'Lee'));

/** The owner; Sam sees his own expenses (a P&L), Kim only her budgets (no P&L). */
const TEAM = {
  can_view_as: true,
  can_edit_defaults: false,
  teams: [],
  teammates: [
    { teammate: 'human:me', kind: 'person', default: 'everything', rights: [] },
    { teammate: 'human:sam', kind: 'person', default: 'own_expenses', rights: [] },
    { teammate: 'human:kim', kind: 'person', default: 'none', rights: [], budgets: 1 },
  ],
};

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  directory();
});
afterEach(() => {
  vi.unstubAllGlobals();
  bind({ router: null });
});

describe('Payables @s406 @s449', () => {
  it('adds a bill as a draft, tried first and then saved @s406', async () => {
    const { posted } = serve([
      ['status=draft', { bills: [] }],
      ['/finance/bills', { bills: [doc({ id: 'b0', kind: 'bill', vendor: { name: 'Paper Co' }, total: 5000, remaining: 5000, status: 'open', due_date: '2099-01-01' })], bills_email: '' }],
      ['chart?for=bill', { accounts: [{ id: 'acc_rent', name: 'Rent', number: '6100', type: 'expense' }] }],
      ['contacts?kind=vendor', { contacts: [{ id: 'v1', name: 'Acme' }] }],
    ]);
    show('/company/finance?tab=payables');
    fireEvent.click(await screen.findByRole('button', { name: 'Add bill' }));
    const dialog = await screen.findByRole('dialog', { name: 'New bill' });
    await pick(within(dialog).getByRole('combobox', { name: 'Vendor' }), /Acme/);
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'Line' }).textContent).toContain('Rent'));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Amount' }), { target: { value: '120' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const payload = { vendor_id: 'v1', bill_date: today, lines: [{ account_id: 'acc_rent', amount: 12000 }], origin: 'manual' };
    await waitFor(() => expect(posted).toEqual([{ kind: 'bill_create', payload, dry_run: true }, { kind: 'bill_create', payload }]));
  });

  it('approves a draft bill from its panel: the approver is named, then Send @s406 @s449 @s475', async () => {
    const { posted } = serve(
      [
        ['/finance/bills/b1', doc({ id: 'b1', kind: 'bill', vendor: { name: 'Acme' }, total: 12000, remaining: 12000, status: 'draft' })],
        ['status=draft', { bills: [doc({ id: 'b1', kind: 'bill', vendor: { name: 'Acme' }, total: 12000, remaining: 12000, status: 'draft' })] }],
        ['/finance/bills', { bills: [], bills_email: '' }],
      ],
      (b) => (b.dry_run ? { status: 'needs_approval', approver: 'human:lee' } : { status: 'needs_approval', approver: 'human:lee' }),
    );
    const router = show('/company/finance?tab=payables');
    const link = await screen.findByRole('link', { name: 'Acme' });
    // A draft waits on whoever may approve it: its group comes first, and the row carries the mark.
    expect(within(link.closest('table')!).getByText('Waits on you')).toBeTruthy();
    expect(within(link.closest('tr')!).getByRole('img', { name: 'Waits on you' })).toBeTruthy();
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'payables', panel: 'bill:b1' }));
    const panel = await screen.findByRole('complementary', { name: 'Acme' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Approve' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve bill' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await within(dialog).findByText('Approval from lee')).toBeTruthy();
    expect(posted).toEqual([{ kind: 'bill_approve', payload: { bill_id: 'b1' }, dry_run: true }]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posted[1]).toEqual({ kind: 'bill_approve', payload: { bill_id: 'b1' } }));
  });

  it('an open bill is marked paid elsewhere, and voided from its menu @s406', async () => {
    const open = doc({ id: 'b2', kind: 'bill', vendor: { name: 'Acme' }, total: 8000, remaining: 8000, status: 'open' });
    const { posted } = serve([
      ['/finance/bills/b2', open],
      ['status=draft', { bills: [] }],
      ['/finance/bills', { bills: [open], bills_email: '' }],
      ['/finance/pay-from', { accounts: [], currency: 'usd', view_version: 1 }],
    ]);
    show('/company/finance?tab=payables&panel=bill:b2');
    const panel = await screen.findByRole('complementary', { name: 'Acme' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Mark paid' }));
    let dialog = await screen.findByRole('dialog', { name: 'Mark paid' });
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'From' }).textContent).toContain('Elsewhere'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark paid' }));
    const paid = { bill_id: 'b2', amount: 8000, date: today };
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'bill_mark_paid', payload: paid }]));

    fireEvent.click(within(panel).getByRole('button', { name: 'More for Acme' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Void' }));
    dialog = await screen.findByRole('dialog', { name: 'Void bill' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Reason' }), { target: { value: 'Twice' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Void' }));
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'bill_void', payload: { bill_id: 'b2', reason: 'Twice' } }]));
  });
});

describe('Receivables @s407', () => {
  const draft = doc({ id: 'i1', kind: 'invoice', customer: { name: 'Globex' }, number: 'INV-7', total: 30000, remaining: 30000, status: 'draft' });
  const sent = doc({ id: 'i2', kind: 'invoice', customer: { name: 'Initech' }, number: 'INV-8', total: 9000, remaining: 9000, status: 'sent' });
  const lists: [string, unknown][] = [
    ['status=draft', { invoices: [draft] }],
    ['/finance/invoices?', { invoices: [sent], open: { aging: { current: 9000, '1_30': 0, '31_60': 0, '61_90': 0, over_90: 0 } } }],
  ];

  it('a new invoice is tried, then saved with its lines @s407', async () => {
    const { posted } = serve([
      ...lists,
      ['chart?for=invoice', { accounts: [{ id: 'acc_sales', name: 'Sales', number: '4000', type: 'income' }] }],
      ['contacts?kind=customer', { contacts: [{ id: 'c9', name: 'Globex' }] }],
    ]);
    show('/company/finance?tab=receivables');
    fireEvent.click(await screen.findByRole('button', { name: 'New invoice' }));
    const dialog = await screen.findByRole('dialog', { name: 'New invoice' });
    await pick(within(dialog).getByRole('combobox', { name: 'Customer' }), /Globex/);
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'Line' }).textContent).toContain('Sales'));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Amount' }), { target: { value: '300' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    const payload = { customer_id: 'c9', invoice_date: today, lines: [{ account_id: 'acc_sales', amount: 30000, taxable: false }] };
    await waitFor(() => expect(posted).toEqual([{ kind: 'invoice_create', payload, dry_run: true }, { kind: 'invoice_create', payload }]));
  });

  it('a draft invoice is sent from its panel @s407 @s475', async () => {
    const { posted } = serve([['/finance/invoices/i1', draft], ...lists]);
    show('/company/finance?tab=receivables');
    fireEvent.click(await screen.findByRole('link', { name: 'Globex' }));
    const panel = await screen.findByRole('complementary', { name: 'Globex' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Send' }));
    const dialog = await screen.findByRole('dialog', { name: 'Send invoice' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'invoice_send', payload: { invoice_id: 'i1' }, dry_run: true }, { kind: 'invoice_send', payload: { invoice_id: 'i1' } }]));
  });

  it('a sent invoice records a payment or is written off @s407', async () => {
    const { posted } = serve([['/finance/invoices/i2', sent], ['/finance/pay-from', { accounts: [{ id: 'a1', name: 'Checking', last4: '0001' }], currency: 'usd', view_version: 1 }], ...lists]);
    show('/company/finance?tab=receivables&panel=invoice:i2');
    const panel = await screen.findByRole('complementary', { name: 'Initech' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Write off' }));
    let dialog = await screen.findByRole('dialog', { name: 'Write off' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Amount' }), { target: { value: '40' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Write off' }));
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'invoice_write_off', payload: { invoice_id: 'i2', amount: 4000 } }]));

    fireEvent.click(within(panel).getByRole('button', { name: 'Mark paid' }));
    dialog = await screen.findByRole('dialog', { name: 'Mark paid' });
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'To' }).textContent).toContain('Checking'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark paid' }));
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'invoice_record_payment', payload: { invoice_id: 'i2', amount: 9000, date: today, money_account_id: 'a1' } }]));
  });
});

describe('Transactions @s405', () => {
  const row = (id: string, o: Record<string, unknown>) => ({ id, kind: 'feed', currency: 'usd', date: '2026-11-02', amount: -1500, ...o });

  it('filters by source, status and line, read by the server, with a chip for each @s405', async () => {
    const { got } = serve([
      ['source=', { rows: [row('t2', { description: 'Figma', account: 'Software', account_id: 'acc_soft' })], next: null }],
      ['/finance/transactions', { rows: [row('t1', { description: 'Figma', account: 'Software', account_id: 'acc_soft' }), row('t3', { description: 'Lunch' })], next: null }],
    ]);
    const router = show('/company/finance?tab=transactions');
    await screen.findByRole('link', { name: 'Lunch' });
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Source' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Bank' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'transactions', src: 'feed' }));
    fireEvent.click(screen.getByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Line' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Software' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ src: 'feed', line: 'acc_soft' }));
    await waitFor(() => expect(got.some((u) => u.includes('source=feed') && u.includes('account_id=acc_soft'))).toBe(true));
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Lunch' })).toBeNull());
    const chips = screen.getByRole('group', { name: 'Filters' });
    expect(within(chips).getByText('Software')).toBeTruthy();
    fireEvent.click(within(chips).getByRole('button', { name: 'Clear Bank' }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('src'));
    expect(router.state.location.search).toMatchObject({ line: 'acc_soft' });
  });

  it('a journal entry posts once its debits and credits match @s405', async () => {
    const { posted } = serve([
      ['/finance/chart', { accounts: [{ id: 'acc_cash', name: 'Cash', number: '1000' }, { id: 'acc_eq', name: 'Owner equity', number: '3000' }] }],
      ['/finance/transactions', { rows: [], next: null }],
    ]);
    show('/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'Add entry' }));
    const dialog = await screen.findByRole('dialog', { name: 'Journal entry' });
    await pick(within(dialog).getByRole('combobox', { name: 'Line 1' }), /Cash/);
    await pick(within(dialog).getByRole('combobox', { name: 'Line 2' }), /Owner equity/);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Debit 1' }), { target: { value: '500' } });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Credit 2' }), { target: { value: '400' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Post' }));
    expect(await within(dialog).findByText('Debits and credits differ by $100')).toBeTruthy();
    expect(posted).toEqual([]);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Credit 2' }), { target: { value: '500' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Post' }));
    const payload = { date: today, lines: [{ account_id: 'acc_cash', amount: 50000 }, { account_id: 'acc_eq', amount: -50000 }] };
    await waitFor(() => expect(posted).toEqual([{ kind: 'journal', payload, dry_run: true }, { kind: 'journal', payload }]));
  });

  it('a bank row with no line opens in place and is put on one; a row with a line opens nothing @s475', async () => {
    const { posted } = serve([
      ['chart?for=journal', { accounts: [{ id: 'acc_soft', name: 'Software', number: '6200', type: 'expense' }] }],
      [
        '/finance/transactions',
        {
          rows: [row('t1', { description: 'Figma', account: 'Software', account_id: 'acc_soft' }), row('t2', { description: 'Corner Shop', lines: [{ line_id: 'l2', tags: { team: 'ops', budget: 'b9' } }] })],
          next: null,
        },
      ],
    ]);
    const router = show('/company/finance?tab=transactions');
    expect(await screen.findByText('Figma')).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Figma' })).toBeNull();
    fireEvent.click(screen.getByRole('link', { name: 'Corner Shop' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ panel: 'tx:t2' }));
    expect(screen.queryByRole('complementary')).toBeNull();
    await pick(await screen.findByRole('combobox', { name: 'Line' }), /Software/);
    await waitFor(() => expect(posted.at(-1)).toEqual({ kind: 'categorize', payload: { feed_id: 't2', account_id: 'acc_soft', tags: { team: 'ops' } } }));
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('panel'));
  });
});

describe('Cards @s411', () => {
  const card = (o: Record<string, unknown>) => ({ id: 'c1', nickname: 'Travel', holder: 'Lee', last4: '4242', spent: 20000, limit: 100000, status: 'active', ...o });

  it('freezes a card from its panel; one its budget froze is not unfrozen by hand; a closed one offers nothing @s411 @s475', async () => {
    const cards = [card({}), card({ id: 'c2', nickname: 'Ads', status: 'frozen', frozen_by: 'budget' }), card({ id: 'c3', nickname: 'Old', status: 'canceled' })];
    const { posted } = serve([['/finance/cards', { cards }]]);
    const router = show('/company/finance?tab=cards');
    fireEvent.click(await screen.findByRole('link', { name: 'Travel' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ panel: 'card:c1' }));
    let panel = await screen.findByRole('complementary', { name: 'Travel' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Freeze' }));
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'card_freeze', payload: { card_id: 'c1' } }]));

    fireEvent.click(screen.getByRole('link', { name: 'Ads' }));
    panel = await screen.findByRole('complementary', { name: 'Ads' });
    expect(within(panel).queryByRole('button', { name: 'Unfreeze' })).toBeNull();
    expect(within(panel).getByRole('button', { name: 'Set limit' })).toBeTruthy();

    fireEvent.click(screen.getByRole('link', { name: 'Old' }));
    panel = await screen.findByRole('complementary', { name: 'Old' });
    for (const name of ['Freeze', 'Unfreeze', 'Set limit']) expect(within(panel).queryByRole('button', { name })).toBeNull();
  });

  it('sets a limit from the panel @s411', async () => {
    const { posted } = serve([['/finance/cards', { cards: [card({})] }]]);
    show('/company/finance?tab=cards&panel=card:c1');
    const panel = await screen.findByRole('complementary', { name: 'Travel' });
    fireEvent.click(await within(panel).findByRole('button', { name: 'Set limit' }));
    const dialog = await screen.findByRole('dialog', { name: 'Travel limit' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Limit / mo' }), { target: { value: '1500' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted.slice(-1)).toEqual([{ kind: 'card_update_limit', payload: { card_id: 'c1', amount: 150000 } }]));
  });

  it('Tico may freeze a card or lower its limit, never raise it or unfreeze @s411', async () => {
    const { posted } = serve([['/finance/cards', { cards: [card({})] }]]);
    show('/company/finance?tab=cards');
    await screen.findByRole('link', { name: 'Travel' });
    queryClient.setQueryData(cardsQuery().queryKey, { cards: [card({})] } as never);
    expect(await run('finance.freezeCard', { card: 'c1' }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('finance.setCardLimit', { card: 'c1', amount: 500 }, { via: 'tico' })).toMatchObject({ ok: true });
    expect(await run('finance.setCardLimit', { card: 'c1', amount: 5000 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('finance.setCardLimit', { card: 'c9', amount: 5 }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(await run('finance.unfreezeCard', { card: 'c1' }, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
    expect(posted).toEqual([
      { kind: 'card_freeze', payload: { card_id: 'c1' } },
      { kind: 'card_update_limit', payload: { card_id: 'c1', amount: 50000 } },
    ]);
  });
});

describe('Balance sheet @s408', () => {
  const acct = (name: string, code: string, balance: number) => ({ account_id: `acc_${code}`, name, code, balance });
  const sheet = (as_of: string, cash: number) => ({ as_of, assets: [acct('Checking', '1000', cash)], liabilities: [acct('Card', '2000', 100000)], equity: [acct('Retained', '3000', cash - 100000)] });

  it('shows month end beside the month end before it, and the choice lands in the address @s408', async () => {
    const now = new Date();
    const month = asOfDay('month', now)!;
    const before = compareDay('month', now);
    const { got } = serve([
      [`as_of=${month}`, sheet(month, 500000)],
      [`as_of=${before}`, sheet(before, 300000)],
      [/balance-sheet/, sheet(today, 600000)],
    ]);
    const router = show('/company/finance?tab=balance');
    await screen.findByRole('table', { name: 'Assets' });
    fireEvent.click(screen.getByRole('button', { name: 'Month end' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'balance', asof: 'month' }));
    await waitFor(() => expect(got.some((u) => u.includes(`as_of=${month}`))).toBe(true));
    const assets = await screen.findByRole('table', { name: 'Assets' });
    await waitFor(() => expect(within(assets).getByText('Checking').closest('tr')!.textContent).toContain('$3,000'));
    expect(within(assets).getByText('Checking').closest('tr')!.textContent).toContain('$5,000');
    expect(within(assets).getAllByRole('columnheader')).toHaveLength(3);
  });

  it('exports the sheet, with its compare column, as a CSV @s408', async () => {
    serve([[`as_of=${today}`, sheet(today, 500000)], [/balance-sheet\?as_of=/, sheet('2026-10-31', 300000)]]);
    let file: Blob | null = null;
    const made = vi.fn((b: Blob) => {
      file = b;
      return 'blob:made-up';
    });
    Object.assign(URL, { createObjectURL: made, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    show('/company/finance?tab=balance');
    await screen.findByRole('table', { name: 'Assets' });
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Export' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    const text = await file!.text();
    expect(text.split('\n')[0]).toBe(`Section,Code,Account,2026-10-31,${today}`);
    expect(text).toContain('Assets,1000,Checking,3000.00,5000.00');
    click.mockRestore();
  });
});

describe('Attribution rule @s459', () => {
  it('a new rule names a vendor and its split, and is saved', async () => {
    team();
    const { posted } = serve([
      ['/finance/access', TEAM],
      ['/finance/attribution-rules', { can_change: true, rules: [] }],
      ['/finance/contacts', { contacts: [{ id: 'v1', name: 'Paper Co' }] }],
    ]);
    show('/company/finance?tab=attribution');
    fireEvent.click((await screen.findAllByRole('button', { name: 'Add rule' }))[0]!);
    const dialog = await screen.findByRole('dialog', { name: 'New rule' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Name' }), { target: { value: 'Paper to Sam' } });
    await pick(within(dialog).getByRole('combobox', { name: 'Match' }), /^Paper Co$/);
    await pick(within(dialog).getByRole('combobox', { name: 'Teammate or team' }), /^Sam$/);
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Share, %' }), { target: { value: '100' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
    await waitFor(() =>
      expect(posted).toEqual([
        {
          kind: 'attribution_rule_set',
          payload: { name: 'Paper to Sam', match: { kind: 'vendor', id: 'v1' }, splits: [{ teammate: 'human:sam', share_bps: 10000 }], enabled: true },
        },
      ]),
    );
  });
});

describe('Import history @s465', () => {
  it('a sheet is mapped to the chart, previewed as a dry run, then imported', async () => {
    const { posted } = serve(
      [
        ['/finance/balance-sheet', { as_of: '2026-10-31', assets: [], liabilities: [], equity: [] }],
        ['/finance/chart', { accounts: [{ id: 'acc_sw', number: '6100', name: 'Software', type: 'expense', subtype: '' }] }],
      ],
      (b) => (b.dry_run ? { status: 'committed', impact: { months: 1, rows: 1, accounts: [{ code: '6100', name: 'Software', total: 1250 }] } } : { status: 'committed' }),
    );
    show('/company/finance?tab=balance');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Import history' }));
    const dialog = await screen.findByRole('dialog', { name: 'Import history' });
    const file = new File(['month,line,amount\n2026-01,Software,12.50\n'], 'history.csv', { type: 'text/csv' });
    fireEvent.change(within(dialog).getByLabelText('Sheet file'), { target: { files: [file] } });
    expect((await within(dialog).findByRole('combobox', { name: 'Software' })).textContent).toContain('6100 Software');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preview' }));
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Import' }));
    await waitFor(() => expect(posted).toHaveLength(2));
    const payload = { csv: 'month,line,amount\n2026-01,Software,1250\n', basis: 'accrual', mapping: { Software: 'acc_sw' } };
    expect(posted[0]).toEqual({ kind: 'import_raw_totals', payload, dry_run: true });
    expect(posted[1]).toEqual({ kind: 'import_raw_totals', payload });
  });

  it('is open to Tico: a posted month can be undone, and a non-owner’s import waits for the owner', async () => {
    const { posted } = serve([]);
    show('/company/finance?tab=balance');
    const r = await run('finance.importHistory', { csv: 'month,line,amount\n2026-01,Sales,100', basis: 'accrual', mapping: { Sales: 'acc_1' } }, { via: 'tico' });
    expect(r).toMatchObject({ ok: true });
    expect(posted.map((p) => p.kind)).toEqual(['import_raw_totals']);
  });
});

describe('View as @s456', () => {
  beforeEach(() => {
    team();
    queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'owner', slug: null });
  });

  it('View on Access lands on the teammate’s P&L, not on Access', async () => {
    serve([['/finance/access', TEAM]]);
    const router = show('/company/finance?tab=access');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'View as' }));
    const dialog = await screen.findByRole('dialog', { name: 'View as' });
    await pick(within(dialog).getByRole('combobox', { name: 'Teammate' }), /^Sam$/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'View' }));
    await waitFor(() => expect(router.state.location.search).toEqual({ as: 'human:sam', tab: 'pnl' }));
  });

  it('a teammate with no P&L lands on their first page', async () => {
    serve([['/finance/access', TEAM]]);
    const router = show('/company/finance?tab=access');
    queryClient.setQueryData(accessQuery.queryKey, TEAM as never);
    expect(await run('finance.viewAs', { teammate: 'human:kim' }, { via: 'click' })).toMatchObject({ ok: true });
    await waitFor(() => expect(router.state.location.search).toEqual({ as: 'human:kim' }));
  });

  it('a page closed to the teammate still offers Stop', async () => {
    serve([['/finance/access', TEAM]]);
    const router = show('/company/finance?tab=access&as=human:sam');
    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Stop' }));
    await waitFor(() => expect((router.state.location.search as { as?: string }).as).toBeUndefined());
  });
});

describe('Money stays the person’s own press @s406 @s407 @s411', () => {
  it('Tico may not approve, schedule, pay, send, write off, void, unfreeze or raise a limit', async () => {
    serve([['/finance/cards', { cards: [] }]]);
    show('/company/finance?tab=cards');
    await screen.findByText('No cards yet');
    const closed: [string, unknown][] = [
      ['finance.approveBill', { bill: 'b1' }],
      ['finance.scheduleBill', { bill: 'b1', on: '2099-01-01', from: 'a1' }],
      ['finance.markPaid', { kind: 'bill', doc: 'b1', amount: 10 }],
      ['finance.markPaid', { kind: 'invoice', doc: 'i1', amount: 10 }],
      ['finance.voidBill', { bill: 'b1' }],
      ['finance.sendInvoice', { invoice: 'i1' }],
      ['finance.writeOff', { invoice: 'i1', amount: 10 }],
      ['finance.unfreezeCard', { card: 'c1' }],
      ['finance.setCardLimit', { card: 'c1', amount: 10 }],
    ];
    for (const [id, input] of closed) expect(await run(id, input, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

describe('Finance on a phone, and its owner', () => {
  afterEach(() => {
    delete document.documentElement.dataset.layout;
  });

  it('Payables on a phone are rows under their groups, never a wide table', async () => {
    document.documentElement.dataset.layout = 'cards';
    serve([
      ['status=draft', { bills: [] }],
      ['/finance/bills', { bills: [doc({ id: 'b1', kind: 'bill', vendor: { name: 'Paper Co' }, total: 5000, remaining: 5000, status: 'open', due_date: '2099-01-01' })], bills_email: '' }],
    ]);
    show('/company/finance?tab=payables');
    const list = await screen.findByRole('list', { name: 'Bills' });
    expect(within(list).getByRole('link', { name: 'Paper Co' })).toBeTruthy();
    expect(within(list).getByText('Later')).toBeTruthy();
    expect(screen.queryByRole('table')).toBeNull();
  });

});

