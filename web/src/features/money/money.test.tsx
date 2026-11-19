import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FinancePage } from './Finance';
import { GrowthPage } from './Growth';
import { budgetDay, monthSpan, pnlRange } from './model';
import { budgetsKey, pnlQuery, type Budget, type GrowthRead, type Pnl } from './queries';
import { validateFinanceSearch, validateGrowthSearch } from './search';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

// Made-up figures and names only.
type Reply = { status: number; body: unknown };
function serve(reply: (url: string, method: string, body: Record<string, unknown>) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const r = await reply(String(url), init?.method ?? 'GET', typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {});
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}
const ok = (body: unknown): Reply => ({ status: 200, body });

/** The page under a router whose address drives it, as the route does. */
function show(page: 'finance' | 'growth', at: string) {
  const Finance = () => <FinancePage search={validateFinanceSearch(useSearch({ strict: false }))} />;
  const Growth = () => <GrowthPage search={validateGrowthSearch(useSearch({ strict: false }))} />;
  const root = createRootRoute({ component: page === 'finance' ? Finance : Growth });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}
const searchOf = (href: string | null) => Object.fromEntries(new URL(href ?? '', 'http://x').searchParams);

const COL = (start: string, state: 'past' | 'current' | 'future') => ({ start, end: start, label: start, state });
const ROW = (account_id: string, name: string, type: string, section: Pnl['rows'][number]['section'], code: string, actual: number[]) => ({
  account_id,
  name,
  type,
  section,
  code,
  parent_id: '',
  private: false,
  actual,
  budget: null,
  projection: null,
  total: null,
});
const PNL = (columns: Pnl['columns'], rows: Pnl['rows']): Pnl => ({
  basis: 'accrual',
  columns,
  rows,
  start: columns[0]?.start ?? '',
  end: columns.at(-1)?.end ?? '',
  excluded: false,
  income: [],
  expense: [],
  net: columns.map(() => 0),
  period: 'month',
  view: 'pnl',
  view_version: 1,
});

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
});
afterEach(() => vi.unstubAllGlobals());

describe('Finance @s398 @s403 @s399', () => {
  it('a P&L line opens beside at its own address, keeping the period', async () => {
    serve((url) => (url.includes('/finance/lines/') ? ok({ account: {}, total: 0, entries: [], paid_by: null }) : ok({})));
    const range = pnlRange('quarter', undefined);
    queryClient.setQueryData(
      pnlQuery({ start: range.start, end: range.end, period: 'quarter', basis: 'accrual' }).queryKey,
      PNL([COL('2026-07-01', 'past'), COL('2026-10-01', 'current')], [ROW('acc_soft', 'Software', 'expense', 'operating', '6100', [120000, 80000])]),
    );
    const router = show('finance', '/company/finance?tab=pnl&period=quarter');
    const link = await screen.findByRole('link', { name: 'Software' });
    expect(searchOf(link.getAttribute('href'))).toEqual({ tab: 'pnl', period: 'quarter', panel: 'line:acc_soft' });
    expect(within(link.closest('tr')!).getByText('$800')).toBeTruthy();
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'pnl', period: 'quarter', panel: 'line:acc_soft' }));
    expect(await screen.findByRole('complementary', { name: 'Software' })).toBeTruthy();
  });

  it('with no source connected says so, with its one action', async () => {
    serve((url) => (url.endsWith('/finance/sources') ? ok({ groups: [{ group: 'manual', name: 'Manual', count: 1, rows: [{ id: 'm1', kind: 'manual', name: 'Entered' }] }] }) : { status: 404, body: {} }));
    show('finance', '/company/finance');
    expect(await screen.findByText('No source connected')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add source' })).toBeTruthy();
  });

  it('a P&L that does not load says so and offers Try again', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'internal' } } }));
    show('finance', '/company/finance?tab=pnl');
    expect(await screen.findByRole('heading', { name: 'Finance did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});

describe('Budgets @s404 @s463', () => {
  const budget: Budget = { id: 'b1', name: 'Tools', account_id: 'acc_soft', parent_id: null, period: 'month', active: true, state: { amount: 50000, actual: 20000 } };

  it('a new amount shows at once and is taken back when refused', async () => {
    let refuse!: () => void;
    serve((url, method) => {
      if (method === 'POST') return new Promise<Reply>((r) => (refuse = () => r(ok({ status: 'refused', code: 'over_limit', reason: 'Over the limit' }))));
      if (url.includes('budget_vs_actual')) return ok({ columns: [], budgets: [] });
      return ok({});
    });
    const ym = budgetDay().slice(0, 7);
    queryClient.setQueryData(budgetsKey(budgetDay()), { budgets: [budget] });
    queryClient.setQueryData(pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }).queryKey, PNL([], []));
    show('finance', '/company/finance?tab=budgets&panel=budget:b1');

    const row = (await screen.findByRole('link', { name: 'Tools' })).closest('tr')!;
    expect(searchOf(row.querySelector('a')!.getAttribute('href'))).toEqual({ tab: 'budgets', panel: 'budget:b1' });
    const field = await screen.findByRole('textbox', { name: /^Budget for / });
    fireEvent.change(field, { target: { value: '900' } });
    fireEvent.submit(field.closest('form')!);

    await waitFor(() => expect(within(row).getByText('$900')).toBeTruthy());
    expect(within(row).getByText('$700')).toBeTruthy();
    refuse();
    await waitFor(() => expect(within(row).getByText('$500')).toBeTruthy());
    expect(within(row).queryByText('$900')).toBeNull();
    expect(await screen.findByText("Couldn't save")).toBeTruthy();
    expect((screen.getByRole('textbox', { name: /^Budget for / }) as HTMLInputElement).value).toBe('500');
  });
});

describe('Growth @s400 @s444', () => {
  const read: GrowthRead = {
    scope: 'all',
    period: '2026-10',
    headline: { signups: { value: 400 }, new_customers: { value: 40 }, cost: { value: 2000 }, cac: { value: 50 } },
    channels: [{ slug: 'search', name: 'Search ads', new_customers: 40, cost: 2000, cac: 50 }],
    metrics: [],
  };

  it('a segment is its own tab and a channel opens at its address, keeping the segment', async () => {
    serve((url) => {
      if (url.endsWith('/growth/segments')) return ok({ segments: [{ slug: 'alpha', name: 'Alpha', channels: ['search'] }] });
      if (url.endsWith('/growth/sources')) return ok({ sources: [{ id: 's1', name: 'Ledger', health: 'fresh', fills: ['spend'] }] });
      return ok(read);
    });
    show('growth', '/company/growth?segment=alpha');
    const tab = await screen.findByRole('link', { name: 'Alpha' });
    expect(tab.getAttribute('aria-current')).toBe('page');
    expect(searchOf(screen.getByRole('link', { name: 'All' }).getAttribute('href'))).toEqual({});
    const channel = await screen.findByRole('link', { name: 'Search ads' });
    expect(searchOf(channel.getAttribute('href'))).toEqual({ segment: 'alpha', panel: 'channel:search' });
    expect(screen.getByRole('list', { name: 'Funnel, Alpha' })).toBeTruthy();
  });

  it('with no source says there are no numbers yet', async () => {
    serve((url) => (url.endsWith('/growth/sources') ? ok({ sources: [] }) : url.endsWith('/growth/segments') ? ok({ segments: [] }) : ok(read)));
    show('growth', '/company/growth');
    expect(await screen.findByText('No numbers yet')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Set up' })).toBeTruthy();
  });
});

describe('Finance dialogs @s449 @s461 @s453 @s454', () => {
  const access = { can_edit_defaults: false, teammates: [{ teammate: 'human:me', kind: 'human', default: 'everything', rights: [] }] };

  it('a transfer that needs approval names the approver and what the budget keeps, then sends', async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok(
          body.dry_run
            ? { kind: 'payment_create', status: 'needs_approval', approver: 'human:lee', impact: { budgets: [{ name: 'Operations', remaining: 50000, remaining_after: 40000 }] } }
            : { kind: 'payment_create', status: 'needs_approval', approver: 'human:lee' },
        );
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/finance/pay-from')) return ok({ accounts: [{ id: 'a1', name: 'Checking', last4: '0001' }, { id: 'a2', name: 'Reserve', last4: '0002' }], currency: 'usd', view_version: 1 });
      return ok({ rows: [] });
    });
    show('finance', '/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'New transfer' }));
    const dialog = await screen.findByRole('dialog', { name: 'Transfer' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Amount' }), { target: { value: '100' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Transfer' }));

    expect(await within(dialog).findByText('Approval from lee')).toBeTruthy();
    expect(within(dialog).getByText('Operations left')).toBeTruthy();
    expect(posted).toEqual([{ kind: 'payment_create', payload: { method: 'internal', from_account: 'a1', to_account: 'a2', amount: 10000 }, dry_run: true }]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('Sent to lee for approval')).toBeTruthy();
    expect(posted[1]).toEqual({ kind: 'payment_create', payload: { method: 'internal', from_account: 'a1', to_account: 'a2', amount: 10000 } });
  });

  it('a transfer pressed again after a lost answer sends the same key; after a refusal, or with a changed amount, a new one', async () => {
    const sent: (string | null)[] = [];
    // What each real send gets: lost, refused (over budget), lost, lost.
    const answers: ('lost' | 'refused')[] = ['lost', 'refused', 'lost', 'lost'];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as Record<string, unknown>) : {};
      const json = (b: unknown, status = 200) => new Response(JSON.stringify(b), { status, headers: { 'Content-Type': 'application/json' } });
      if (init?.method === 'POST' && body.dry_run) return json({ kind: 'payment_create', status: 'committed' });
      if (init?.method === 'POST') {
        sent.push(new Headers(init.headers).get('Idempotency-Key'));
        // the server may have queued it; the answer is lost
        if (answers[sent.length - 1] === 'lost') throw new TypeError('connection reset');
        return json({ error: { code: 'over_budget', detail: 'Over budget' } }, 422);
      }
      if (String(url).endsWith('/finance/access')) return json(access);
      if (String(url).endsWith('/finance/pay-from')) return json({ accounts: [{ id: 'a1', name: 'Checking' }, { id: 'a2', name: 'Reserve' }], currency: 'usd', view_version: 1 });
      return json({ rows: [] });
    });
    show('finance', '/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'New transfer' }));
    const dialog = await screen.findByRole('dialog', { name: 'Transfer' });
    const amount = within(dialog).getByRole('textbox', { name: 'Amount' });
    const button = within(dialog).getByRole('button', { name: 'Transfer' });
    const press = async (n: number) => {
      fireEvent.click(button);
      await waitFor(() => expect(sent).toHaveLength(n));
      await waitFor(() => expect(button.getAttribute('aria-busy')).toBeNull());
      await within(dialog).findByRole('alert');
    };
    fireEvent.change(amount, { target: { value: '100' } });
    await press(1);
    await press(2);
    expect(sent[1]).toBe(sent[0]);
    await press(3);
    expect(sent[2]).not.toBe(sent[1]);
    fireEvent.change(amount, { target: { value: '200' } });
    await press(4);
    expect(sent[3]).not.toBe(sent[2]);
  });

  it("Stripe's webhook shows its address and secret once", async () => {
    serve((url, method) => {
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/finance/sources'))
        return ok({ groups: [{ group: 'payments', name: 'Payments', count: 1, rows: [{ id: 'c1', kind: 'connection', name: 'Stripe', provider: 'stripe', sync: { status: 'active' } }] }] });
      if (url.endsWith('/signing')) return ok({ trigger: { signing_secret: 'whsec_made_up' } });
      if (url.endsWith('/triggers')) return ok(method === 'POST' ? { trigger: { id: 't1', url: 'https://hooks.example.test/t1' } } : { triggers: [] });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=sources');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Stripe' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Add webhook' }));
    const dialog = await screen.findByRole('dialog', { name: 'Stripe webhook' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));

    const once = await screen.findByRole('alertdialog', { name: 'Stripe webhook' });
    expect(within(once).getByText('https://hooks.example.test/t1')).toBeTruthy();
    expect(within(once).getByText('whsec_made_up')).toBeTruthy();
    expect(within(once).getAllByText('Shown once')).toHaveLength(2);
  });
});

describe('Finance books @s420 @s455 @s456 @s458 @s459 @s464 @s465', () => {
  const access = { can_edit_defaults: false, can_view_as: true, teammates: [{ teammate: 'human:me', kind: 'human', default: 'everything', rights: [] }] };
  const tx = (id: string, name: string, amount: number) => ({ id, kind: 'entry', entry_id: `e_${id}`, date: '2026-10-02', description: name, account: 'Software', amount });
  const cursors = (urls: string[]) => urls.filter((u) => u.includes('cursor=')).map((u) => new URL(u, 'http://x').searchParams.get('cursor'));

  it('transactions past the first page load as the last row is drawn', async () => {
    const urls: string[] = [];
    serve((url) => {
      urls.push(url);
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/transactions'))
        return ok(url.includes('cursor=') ? { rows: [tx('t2', 'Paper Co', -2000)], next: null } : { rows: [tx('t1', 'Ink Co', -1000)], next: 'page 2' });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=transactions');
    expect(await screen.findByText('Paper Co')).toBeTruthy();
    expect(screen.getByText('Ink Co')).toBeTruthy();
    expect(cursors(urls)).toEqual(['page 2']);
    expect(screen.getByText('2 entries')).toBeTruthy();
  });

  it("a line's shares are saved from its row", async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok({ kind: 'attribute', status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/attribution'))
        return ok({ line_id: 't1', amount: -1000, can_change: true, history: [], splits: [{ teammate: 'human:lee', team: '', share_bps: 10000 }], source: 'manual', set_by: 'human:me', set_at: null, rule_id: null, rule_name: null });
      if (url.includes('/finance/transactions')) return ok({ rows: [tx('t1', 'Ink Co', -1000)], next: null });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Ink Co' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Attributed to' }));
    const dialog = await screen.findByRole('dialog', { name: 'Attributed to' });
    await within(dialog).findByRole('button', { name: 'Clear' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'attribute', payload: { line_id: 't1', splits: [{ teammate: 'human:lee', share_bps: 10000 }] } }]));
  });

  it('the bills address is set from Payables', async () => {
    const puts: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'PUT' && url.endsWith('/finance/bills-address')) {
        puts.push(body);
        return ok({ bills_email: 'bills@example.test', inbox_id: 'in_1' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/bills')) return ok({ bills: [], bills_email: null });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=payables');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /^Bills address/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Bills address' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Address' }), { target: { value: 'bills@example.test' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts).toEqual([{ address: 'bills@example.test' }]));
    expect(await screen.findByText('Saved')).toBeTruthy();
  });

  it('a rule is deleted from its row', async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok({ kind: 'attribution_rule_delete', status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/finance/attribution-rules'))
        return ok({ can_change: true, rules: [{ id: 'r1', name: 'Paper to Lee', enabled: true, match: { kind: 'vendor', id: 'v1', label: 'Paper Co' }, splits: [{ teammate: 'human:lee', share_bps: 10000 }] }] });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=attribution');
    expect(await screen.findByRole('button', { name: 'Add rule' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'More for Paper to Lee' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Delete' }));
    const confirm = await screen.findByRole('dialog', { name: 'Delete Paper to Lee' });
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'attribution_rule_delete', payload: { id: 'r1' } }]));
  });

  it('a statement is previewed, then its new rows posted with that preview', async () => {
    const posted: { url: string; body: Record<string, unknown> }[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push({ url, body });
        if (url.endsWith('/statements/preview'))
          return ok({
            token: 'tok_1',
            expires_at: '2026-10-02T00:00:00Z',
            new: 1,
            rows: [{ id: 'x1', date: '2026-10-01', description: 'Paper Co', amount: -1000, status: 'new', vendor: null, attribution: null }],
            tie: { opening: 0, added: 0, removed: 1000, ends: -1000, off_by: 0, statement_says: null },
          });
        return ok({ kind: 'statement_post', status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/finance/sources')) return ok({ groups: [{ group: 'banks', name: 'Banks', count: 1, rows: [{ id: 'ma1', kind: 'bank', name: 'Checking', last4: '0001' }] }] });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=sources');
    fireEvent.click(await screen.findByRole('button', { name: 'More for Checking' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Upload statement' }));
    const dialog = await screen.findByRole('dialog', { name: 'Upload statement' });
    const file = new File(['date,amount\n2026-10-01,-10.00\n'], 'october.csv', { type: 'text/csv' });
    fireEvent.change(within(dialog).getByLabelText('Statement file'), { target: { files: [file] } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Preview' }));
    const data = btoa('date,amount\n2026-10-01,-10.00\n');
    fireEvent.click(await within(dialog).findByRole('button', { name: 'Post 1 row' }));
    await waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[0]).toEqual({ url: expect.stringContaining('/finance/statements/preview') as string, body: { money_account_id: 'ma1', file: data, file_name: 'october.csv', signs: 'auto' } });
    expect(posted[1]!.body).toEqual({ kind: 'statement_post', payload: { money_account_id: 'ma1', file: data, file_name: 'october.csv', preview_token: 'tok_1' } });
  });

  it('viewing as a teammate reads as them and offers no changes', async () => {
    const urls: string[] = [];
    serve((url) => {
      urls.push(url);
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/transactions')) return ok({ rows: [tx('t1', 'Ink Co', -1000)], next: null });
      return { status: 404, body: {} };
    });
    show('finance', '/company/finance?tab=transactions&as=human:sam');
    expect(await screen.findByText('Ink Co')).toBeTruthy();
    const read = urls.find((u) => u.includes('/finance/transactions'))!;
    expect(new URL(read, 'http://x').searchParams.get('view_as')).toBe('human:sam');
    expect(screen.getByText(/^Viewing as /)).toBeTruthy();
    expect(searchOf(screen.getByRole('link', { name: 'Stop' }).getAttribute('href')).as).toBeUndefined();
    expect(screen.queryByRole('button', { name: 'New transfer' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More for Ink Co' })).toBeNull();
  });
});
