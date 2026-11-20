import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider, useSearch } from '@tanstack/react-router';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { run } from '@/actions';
import { queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FinancePage } from './Finance';
import { loadFinance } from './load';
import { pnlRange } from './model';
import { pnlQuery, type Pnl } from './queries';
import { validateFinanceSearch } from './search';

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
function show(at: string) {
  const Finance = () => <FinancePage search={validateFinanceSearch(useSearch({ strict: false }))} />;
  const root = createRootRoute({ component: Finance });
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
/** Picks an option from a Select. */
async function pick(box: HTMLElement, name: RegExp) {
  fireEvent.click(box);
  const option = await screen.findByRole('option', { name });
  fireEvent.pointerDown(option, { pointerType: 'mouse' });
  fireEvent.click(option);
}
const OWNER = { can_edit_defaults: false, teammates: [{ teammate: 'human:me', kind: 'human', default: 'everything', rights: [] }] };

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
  it('warms access and the overview P&L without waiting for either read', async () => {
    let finishPnl: (reply: Reply) => void = () => {};
    const pendingPnl = new Promise<Reply>((resolve) => (finishPnl = resolve));
    const reads = vi.fn((url: string) => (url.includes('/finance/pnl') ? pendingPnl : ok(OWNER)));
    serve(reads);

    const loaded = loadFinance(queryClient, {});
    finishPnl(ok(PNL([], [])));

    expect(loaded).toBeUndefined();
    expect(reads.mock.calls.map(([url]) => url)).toEqual(['/api/v2/finance/access', expect.stringMatching(/^\/api\/v2\/finance\/pnl\?/)]);
    await pendingPnl;
  });

  it('a P&L line opens beside at its own address, keeping the period', async () => {
    serve((url) => (url.includes('/finance/lines/') ? ok({ account: {}, total: 0, entries: [], paid_by: null }) : ok({})));
    const range = pnlRange('quarter', undefined);
    queryClient.setQueryData(
      pnlQuery({ start: range.start, end: range.end, period: 'quarter', basis: 'accrual' }).queryKey,
      PNL([COL('2026-07-01', 'past'), COL('2026-10-01', 'current')], [ROW('acc_soft', 'Software', 'expense', 'operating', '6100', [120000, 80000])]),
    );
    const router = show('/company/finance?tab=pnl&period=quarter');
    const link = await screen.findByRole('link', { name: 'Software' });
    expect(searchOf(link.getAttribute('href'))).toEqual({ tab: 'pnl', period: 'quarter', panel: 'line:acc_soft' });
    expect(within(link.closest('tr')!).getByText('$800')).toBeTruthy();
    fireEvent.click(link);
    await waitFor(() => expect(router.state.location.search).toMatchObject({ tab: 'pnl', period: 'quarter', panel: 'line:acc_soft' }));
    expect(await screen.findByRole('complementary', { name: 'Software' })).toBeTruthy();
  });

  it('with no source connected says so, with its one action', async () => {
    const reads = vi.fn((url: string) =>
      url.endsWith('/finance/sources')
        ? ok({ groups: [{ group: 'manual', name: 'Manual', count: 1, rows: [{ id: 'm1', kind: 'manual', name: 'Entered' }] }] })
        : { status: 404, body: {} },
    );
    serve(reads);
    show('/company/finance');
    expect(await screen.findByText('No source connected')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Add source' })).toBeTruthy();
    expect(reads.mock.calls.map(([url]) => url)).not.toContain('/api/v2/finance/brief');
  });

  it('shows the complete unpaid totals without asking for the unpaid rows', async () => {
    const reads = vi.fn((url: string) => {
      if (url.endsWith('/finance/access')) return ok(OWNER);
      if (url.endsWith('/finance/sources')) return ok({ groups: [{ group: 'bank', count: 1, rows: [{ kind: 'stripe' }] }] });
      if (url.startsWith('/api/v2/finance/bills?')) return ok({ open: { count: 25, remaining: 4200 } });
      if (url.startsWith('/api/v2/finance/invoices?')) return ok({ open: { count: 32, remaining: 6100 } });
      return { status: 404, body: {} };
    });
    serve(reads);
    show('/company/finance');
    expect(await screen.findByText('$42')).toBeTruthy();
    expect(screen.getByText('$61')).toBeTruthy();
    expect(reads.mock.calls.map(([url]) => url).filter((url) => /\/finance\/(bills|invoices)\?/.test(url)).sort()).toEqual([
      '/api/v2/finance/bills?status=unpaid&limit=1',
      '/api/v2/finance/invoices?status=unpaid&limit=1',
    ]);
  });

  it('shows the permitted overview when viewing as a teammate without sources, payables, or receivables access', async () => {
    const scoped = {
      ...OWNER,
      can_view_as: true,
      teammates: [...OWNER.teammates, { teammate: 'human:lee', default: 'own_expenses', rights: [] }],
    };
    const deniedReads = vi.fn();
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(scoped);
      if (/\/finance\/(sources|bills|invoices)\b/.test(url)) {
        deniedReads();
        return { status: 403, body: {} };
      }
      if (url.includes('/finance/pnl')) return ok({ ...PNL([COL('2026-11-01', 'current')], []), net: [12300] });
      return { status: 404, body: {} };
    });
    show('/company/finance?as=human:lee');
    expect(await screen.findByText('$123')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Add source' })).toBeNull();
    expect(deniedReads).not.toHaveBeenCalled();
  });

  it('a P&L that does not load says so and offers Try again', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'internal' } } }));
    show('/company/finance?tab=pnl');
    expect(await screen.findByRole('heading', { name: 'Finance did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a tab outside your view says you have no access, without Try again', async () => {
    serve(() => ({ status: 403, body: { error: { code: 'forbidden' } } }));
    show('/company/finance?tab=balance');
    expect(await screen.findByText("You don't have access to this page")).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
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
    show('/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New transfer' }));
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
        // the server may have queued it; a gateway's 502 loses the answer (a drop or a 503 the client sends again itself)
        if (answers[sent.length - 1] === 'lost') return new Response('Bad gateway', { status: 502 });
        return json({ error: { code: 'over_budget', detail: 'Over budget' } }, 422);
      }
      if (String(url).endsWith('/finance/access')) return json(access);
      if (String(url).endsWith('/finance/pay-from')) return json({ accounts: [{ id: 'a1', name: 'Checking' }, { id: 'a2', name: 'Reserve' }], currency: 'usd', view_version: 1 });
      return json({ rows: [] });
    });
    show('/company/finance?tab=transactions');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'New transfer' }));
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
    show('/company/finance?tab=sources');
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

  it('transactions past the first page load as the last row is drawn @s405', async () => {
    const urls: string[] = [];
    serve((url) => {
      urls.push(url);
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/transactions'))
        return ok(url.includes('cursor=') ? { rows: [tx('t2', 'Paper Co', -2000)], next: null } : { rows: [tx('t1', 'Ink Co', -1000)], next: 'page 2' });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=transactions');
    expect(await screen.findByText('Paper Co')).toBeTruthy();
    expect(screen.getByText('Ink Co')).toBeTruthy();
    expect(cursors(urls)).toEqual(['page 2']);
    expect(screen.getByText('2 entries')).toBeTruthy();
  });

  it('keeps all transaction filters and the viewed teammate on later pages', async () => {
    const pages: URLSearchParams[] = [];
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(access);
      if (!url.includes('/finance/transactions')) return { status: 404, body: {} };
      const params = new URL(url, 'http://x').searchParams;
      pages.push(params);
      return params.has('cursor') ? ok({ rows: [tx('t2', 'Paper Co', -2000)], next: null }) : ok({ rows: [tx('t1', 'Ink Co', -1000)], next: 'page 2' });
    });
    show('/company/finance?tab=transactions&src=feed&status=posted&line=acc_soft&as=human:sam');
    expect(await screen.findByText('Paper Co')).toBeTruthy();
    expect(screen.getByText('Ink Co')).toBeTruthy();
    expect(pages.map((p) => Object.fromEntries(p))).toEqual([
      { limit: '200', source: 'feed', status: 'posted', account_id: 'acc_soft', view_as: 'human:sam' },
      { limit: '200', cursor: 'page 2', source: 'feed', status: 'posted', account_id: 'acc_soft', view_as: 'human:sam' },
    ]);
  });

  it('keeps earlier transactions visible and retries the failed cursor', async () => {
    let attempts = 0;
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(access);
      if (!url.includes('/finance/transactions')) return { status: 404, body: {} };
      if (!url.includes('cursor=')) return ok({ rows: [tx('t1', 'Ink Co', -1000)], next: 'page 2' });
      if (++attempts === 1) return { status: 500, body: {} };
      return ok({ rows: [tx('t2', 'Paper Co', -2000)], next: null });
    });
    show('/company/finance?tab=transactions');
    const retry = await screen.findByRole('button', { name: 'Try again' });
    expect(screen.getByText('Ink Co')).toBeTruthy();
    fireEvent.click(retry);
    expect(await screen.findByText('Paper Co')).toBeTruthy();
    expect(attempts).toBe(2);
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
    show('/company/finance?tab=transactions');
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
    show('/company/finance?tab=payables');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /^Bills address/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Bills address' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Address' }), { target: { value: 'bills@example.test' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(puts).toEqual([{ address: 'bills@example.test' }]));
    expect(await screen.findByText('Saved')).toBeTruthy();
  });

  it('a rule is deleted from its row @s410', async () => {
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
    show('/company/finance?tab=attribution');
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
    show('/company/finance?tab=sources');
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
    show('/company/finance?tab=transactions&as=human:sam');
    expect(await screen.findByText('Ink Co')).toBeTruthy();
    const read = urls.find((u) => u.includes('/finance/transactions'))!;
    expect(new URL(read, 'http://x').searchParams.get('view_as')).toBe('human:sam');
    expect(screen.getByText(/^Viewing as /)).toBeTruthy();
    expect(searchOf(screen.getByRole('link', { name: 'Stop' }).getAttribute('href')).as).toBeUndefined();
    expect(screen.queryByRole('button', { name: 'More' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'More for Ink Co' })).toBeNull();
  });
});

describe('Finance tabs and sources', () => {
  const access = {
    can_edit_defaults: true,
    can_view_as: true,
    finance_approver: 'human:lee',
    defaults: { manager: 'none', manager_payroll: 'totals', member: 'none' },
    teammates: [
      { teammate: 'human:me', kind: 'human', default: 'everything', rights: [] },
      { teammate: 'human:lee', kind: 'human', default: 'team_expenses', rights: [] },
      { teammate: 'human:sam', kind: 'human', default: 'none', rights: [] },
    ],
  };
  const acct = (name: string, code: string, balance: number) => ({ name, code, balance });

  it('the balance sheet sets assets beside liabilities and equity, with totals @s408', async () => {
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/balance-sheet'))
        return ok({ as_of: '2026-10-31', assets: [acct('Checking', '1000', 500000)], liabilities: [acct('Card', '2000', 100000)], equity: [acct('Retained', '3000', 400000)] });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=balance');
    const assets = await screen.findByRole('table', { name: 'Assets' });
    expect(within(assets).getAllByText('Assets').at(-1)!.closest('tr')!.textContent).toContain('$5,000');
    const other = screen.getByRole('table', { name: 'Liabilities and equity' });
    expect(within(other).getByText('Total liabilities').closest('tr')!.textContent).toContain('$1,000');
    expect(within(other).getByText('Liabilities and equity').closest('tr')!.textContent).toContain('$5,000');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'Import history' })).toBeTruthy();
  });

  it('sales tax splits what is due from what is filed @s409', async () => {
    const row = (code: string, period: string, due: number, collected: number) => ({ code, jurisdiction: code, period, taxable_sales: 100000, due, due_on: '2026-11-20', tax_collected: collected, tax_remitted: null });
    serve((url) => {
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/tax/sales-tax')) return ok({ rows: [row('North', '2026-10', 7000, 7000), row('South', '2026-09', 0, 5000)] });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=tax');
    const due = await screen.findByRole('table', { name: 'Due' });
    expect(within(due).getByText('North').closest('tr')!.textContent).toContain('$70');
    const filed = screen.getByRole('table', { name: 'Filed' });
    expect(within(filed).getByText('South').closest('tr')!.textContent).toContain('$50');
  });

  it('cards list spend against limit, and a new card is issued from a budget @s411 @s460', async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok({ kind: 'card_create', status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.includes('/finance/cards')) return ok({ cards: [{ id: 'c1', nickname: 'Travel', holder: 'Lee', last4: '4242', spent: 20000, limit: 100000, status: 'active' }] });
      if (url.includes('/finance/budgets')) return ok({ budgets: [{ id: 'b1', name: 'Tools', account_id: 'acc_soft', parent_id: null, period: 'month', active: true, state: { amount: 50000 } }] });
      if (url.endsWith('/finance/mercury-users')) return ok({ users: [{ id: 'u1', name: 'Lee' }] });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=cards');
    const cards = await screen.findByRole('table', { name: 'Cards' });
    expect(within(cards).getByText('Travel').closest('tr')!.textContent).toContain('$1,000');
    fireEvent.click(await screen.findByRole('button', { name: 'Add card' }));
    const dialog = await screen.findByRole('dialog', { name: 'New card' });
    await waitFor(() => expect(within(dialog).getByRole('combobox', { name: 'Mercury user' }).textContent).toContain('Lee'));
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Limit / mo' }), { target: { value: '250' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'card_create', payload: { holder: 'human:me', budget_id: 'b1', amount: 25000, provider_user_id: 'u1' } }]));
  });

  it('access lists who sees what and who approves; defaults are saved from Edit defaults @s413 @s457', async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok({ kind: 'view_defaults_set', status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=access');
    const table = await screen.findByRole('table', { name: 'Access' });
    expect(within(table).getByText('Everything').closest('tr')!.textContent).toContain('Any amount');
    expect(within(table).getByText("Their team's lines").closest('tr')!.textContent).toContain('Any amount');
    // Those who see nothing fold into one line, which opens them.
    fireEvent.click(screen.getByRole('button', { name: /1 other sees nothing/ }));
    expect(await within(table).findAllByRole('row')).toHaveLength(4);
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(await screen.findByRole('menuitem', { name: 'View as' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Edit defaults' }));
    const dialog = await screen.findByRole('dialog', { name: 'Edit defaults' });
    await pick(within(dialog).getByRole('combobox', { name: 'Managers' }), /^Own expenses$/);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'view_defaults_set', payload: { manager: 'own_expenses' } }]));
  });

  it('a pasted key is stored on Tools, then the source connected with it; the key stays out of the cache @s450', async () => {
    const calls: { url: string; body: Record<string, unknown> }[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        calls.push({ url: new URL(url, 'http://x').pathname, body });
        if (url.endsWith('/api-tools')) return ok({ id: 'tool1' });
        if (url.endsWith('/keys')) return ok({ id: 'k1' });
        return ok({ status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/tool-index')) return ok({ rows: [] });
      if (url.endsWith('/finance/sources')) return ok({ groups: [] });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=sources');
    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Brex' }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect Brex' });
    fireEvent.change(await within(dialog).findByLabelText('API token'), { target: { value: 'made-up-token' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }));
    expect(await screen.findByText('Brex connected')).toBeTruthy();
    expect(calls.map((c) => c.url)).toEqual(['/api/v2/api-tools', '/api/v2/api-tools/tool1/keys', '/api/v2/finance/connections']);
    expect(calls[1]!.body).toMatchObject({ value: 'made-up-token', access_level: 'read' });
    expect(calls[2]!.body).toMatchObject({ provider: 'brex', credential_id: 'k1', name: 'Brex' });
    expect(JSON.stringify(queryClient.getQueryCache().getAll().map((q) => q.state.data))).not.toContain('made-up-token');
  });

  it('QuickBooks is signed in to on its own page; no address back says it did not answer @s451', async () => {
    const posted: { url: string; body: Record<string, unknown> }[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push({ url, body });
        return ok({});
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/finance/sources')) return ok({ groups: [] });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=sources');
    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'QuickBooks' }));
    const dialog = await screen.findByRole('dialog', { name: 'Connect QuickBooks' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }));
    expect(await within(dialog).findByText('QuickBooks did not answer')).toBeTruthy();
    expect(posted).toEqual([{ url: expect.stringContaining('/finance/books/connect') as string, body: { provider: 'quickbooks' } }]);
  });

  it('the books are used, and a source disconnected, each after a confirm @s412 @s452', async () => {
    const posted: Record<string, unknown>[] = [];
    serve((url, method, body) => {
      if (method === 'POST') {
        posted.push(body);
        return ok({ kind: body.kind, status: 'committed' });
      }
      if (url.endsWith('/finance/access')) return ok(access);
      if (url.endsWith('/triggers')) return ok({ triggers: [] });
      if (url.endsWith('/finance/sources'))
        return ok({
          groups: [
            { group: 'banks', name: 'Banks', count: 1, rows: [{ id: 'cb', kind: 'connection', name: 'Bank feed', provider: 'mercury', sync: { status: 'active' } }] },
            { group: 'accounting', name: 'Accounting', count: 1, rows: [{ id: 'qb', kind: 'connection', name: 'Ledger books', provider: 'quickbooks', mirror: 'native', sync: { status: 'active' } }] },
          ],
        });
      return { status: 404, body: {} };
    });
    show('/company/finance?tab=sources');
    const table = await screen.findByRole('table', { name: 'Sources' });
    expect(within(table).getByText('Bank feed').closest('tr')!.textContent).toContain('Cash, Transactions');
    fireEvent.click(screen.getByRole('button', { name: 'More for Ledger books' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Use as books' }));
    const use = await screen.findByRole('dialog', { name: 'Use QuickBooks as the books' });
    fireEvent.click(within(use).getByRole('button', { name: 'Use' }));
    await waitFor(() => expect(posted).toEqual([{ kind: 'mirror_start', payload: { connection_id: 'qb' } }]));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Use QuickBooks as the books' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'More for Bank feed' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Disconnect Bank feed' }));
    const off = await screen.findByRole('dialog', { name: 'Disconnect Bank feed' });
    fireEvent.click(within(off).getByRole('button', { name: 'Disconnect' }));
    await waitFor(() => expect(posted[1]).toEqual({ kind: 'connection_disconnect', payload: { connection_id: 'cb' } }));
  });

  it("money, secrets, sign-ins and who sees what stay the person's own press", async () => {
    const closed: [string, unknown][] = [
      ['finance.transfer', { from: 'a1', to: 'a2', amount: 10 }],
      ['finance.newCard', { holder: 'human:me', budget: 'b1', amount: 10, user: 'u1' }],
      ['finance.connect', { provider: 'brex', token: 'made-up' }],
      ['finance.connectBooks', { provider: 'xero' }],
      ['finance.disconnect', { connection: 'c1' }],
      ['finance.setDefaults', { member: 'own_expenses' }],
      ['budget.addRow', { name: 'Ads', line: 'acc_ads', amount: 10 }],
    ];
    for (const [id, input] of closed) expect(await run(id, input, { via: 'tico' })).toMatchObject({ ok: false, error: 'not_allowed' });
  });
});

