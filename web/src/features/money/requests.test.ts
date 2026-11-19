import type { QueryFunctionContext } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as Q from './queries';

/* Every read the Money pages make, checked against what its server route takes (backend/finance_routes.py and
   backend/growth_core.py): known parameters only, allowed words, limits in range. A request outside these 422s. */
type Rule = { params: Record<string, (v: string) => boolean> };
const upTo = (max: number) => (v: string) => /^\d+$/.test(v) && +v >= 1 && +v <= max;
const oneOf =
  (...words: string[]) =>
  (v: string) =>
    words.includes(v);
const list = (...words: string[]) => (v: string) => v.split(',').every((w) => words.includes(w.trim()));
const day = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v);
const any = () => true;
const PERIOD = oneOf('day', 'week', 'month', 'quarter', 'year');
const teammate = (v: string) => /^(human|bot):\S+$/.test(v);
/** The owner reading as a teammate: only routes that take `view_as` get it (backend as_viewer). */
const viewed = (params: Record<string, (v: string) => boolean>) => ({ params: { ...params, view_as: teammate } });

const RULES: Record<string, Rule> = {
  '/api/v2/finance/bills': viewed({ status: list('draft', 'open', 'scheduled', 'paid', 'void', 'unpaid'), limit: upTo(200) }),
  '/api/v2/finance/invoices': viewed({ status: list('draft', 'sent', 'partial', 'paid', 'void', 'unpaid'), limit: upTo(200) }),
  '/api/v2/finance/transactions': viewed({ limit: upTo(200), cursor: (v) => v.length > 0 && v.length <= 600 }),
  '/api/v2/finance/cards': viewed({ limit: upTo(200) }),
  '/api/v2/finance/inbox': viewed({ limit: upTo(200) }),
  '/api/v2/finance/budgets': viewed({ at: day, limit: upTo(500) }),
  '/api/v2/finance/reports/budget_vs_actual': viewed({ period: PERIOD, start: day, end: day, top_n: upTo(200) }),
  '/api/v2/finance/pnl': viewed({ start: any, end: any, period: PERIOD, basis: oneOf('cash', 'accrual') }),
  '/api/v2/finance/lines/:id': viewed({ start: any, end: any, top_n: upTo(200), limit: upTo(200) }),
  '/api/v2/finance/lines/:id/attribution': viewed({}),
  '/api/v2/finance/metrics/cash': viewed({}),
  '/api/v2/finance/balance-sheet': viewed({ as_of: any }),
  '/api/v2/finance/sources': viewed({}),
  '/api/v2/finance/access': { params: {} },
  '/api/v2/finance/brief': viewed({}),
  '/api/v2/finance/tax/sales-tax': viewed({}),
  '/api/v2/finance/attribution-rules': { params: {} },
  '/api/v2/finance/pay-from': { params: {} },
  '/api/v2/finance/contacts': { params: { kind: oneOf('vendor', 'customer', 'both'), limit: upTo(200) } },
  '/api/v2/finance/chart': { params: { for: oneOf('bill', 'invoice', 'journal') } },
  '/api/v2/finance/mercury-users': { params: {} },
  '/api/v2/growth/read': { params: { scope: any, period: (v) => /^\d{4}-(0[1-9]|1[0-2])$/.test(v), detail: list('campaigns', 'retention', 'series') } },
  '/api/v2/growth/segments': { params: {} },
  '/api/v2/growth/sources': { params: {} },
};

type Opts = { queryKey: readonly unknown[]; queryFn?: unknown };
const AS = 'human:sam';
const READS: [string, Opts][] = [
  ['bills', Q.billsQuery()],
  ['invoices', Q.invoicesQuery()],
  ['transactions', Q.transactionsQuery()],
  ['cards', Q.cardsQuery()],
  ['inbox', Q.inboxQuery()],
  ['budgets', Q.budgetsQuery('2026-03-15')],
  ['budget history', Q.budgetHistoryQuery('2025-04-01', '2026-03-31')],
  ['P&L', Q.pnlQuery({ start: '2026-01-01', end: '2026-03-31', period: 'month', basis: 'cash' })],
  ['line', Q.lineQuery('acct-1', { start: '2026-03-01', end: '2026-03-31' })],
  ['cash', Q.metricQuery('cash')],
  ['balance sheet', Q.balanceQuery('2026-03-31')],
  ['sources', Q.sourcesQuery()],
  ['access', Q.accessQuery],
  ['brief', Q.briefQuery()],
  ['sales tax', Q.taxQuery()],
  ['rules', Q.rulesQuery],
  ['pay from', Q.payFromQuery],
  ['Mercury users', Q.mercuryUsersQuery],
  ['line attribution', Q.lineShareQuery('line-1')],
  ['vendors', Q.vendorsQuery],
  ['chart', Q.journalChartQuery],
  // Each read again as the owner sees it through a teammate.
  ['bills, as', Q.billsQuery(AS)],
  ['transactions, as', Q.transactionsQuery(AS)],
  ['P&L, as', Q.pnlQuery({ start: '2026-01-01', end: '2026-03-31', period: 'month', basis: 'cash' }, AS)],
  ['line, as', Q.lineQuery('acct-1', { start: '2026-03-01', end: '2026-03-31' }, AS)],
  ['balance sheet, as', Q.balanceQuery(undefined, AS)],
  ['budgets, as', Q.budgetsQuery('2026-03-15', AS)],
  ['budget history, as', Q.budgetHistoryQuery('2025-04-01', '2026-03-31', AS)],
  ['sources, as', Q.sourcesQuery(AS)],
  ['cash, as', Q.metricQuery('cash', AS)],
  ['line attribution, as', Q.lineShareQuery('line-1', AS)],
  ['growth', Q.growthReadQuery('seg-a', '2026-02')],
  ['growth, all', Q.growthReadQuery(undefined, undefined, false)],
  ['segments', Q.segmentsQuery],
  ['growth sources', Q.growthSourcesQuery],
];

async function urlOf(opts: Opts): Promise<URL> {
  let seen = '';
  vi.stubGlobal('fetch', async (url: string) => {
    seen = String(url);
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  const fn = opts.queryFn as (c: QueryFunctionContext) => Promise<unknown>;
  await fn({ queryKey: opts.queryKey, signal: new AbortController().signal, meta: undefined } as unknown as QueryFunctionContext);
  return new URL(seen, 'http://x');
}

describe('Money request shapes', () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each(READS)('%s asks only for what its route takes', async (_name, opts) => {
    const url = await urlOf(opts);
    const path = url.pathname.replace(/^\/api\/v2\/finance\/lines\/[^/]+/, '/api/v2/finance/lines/:id');
    const rule = RULES[path];
    expect(rule, path).toBeDefined();
    for (const [k, v] of url.searchParams) {
      const ok = rule?.params[k];
      expect(ok, `${path} has no ${k}`).toBeDefined();
      expect(ok?.(v), `${path} refuses ${k}=${v}`).toBe(true);
    }
  });

  it('open bills and invoices ask for unpaid, which invoices take and open does not', async () => {
    expect((await urlOf(Q.invoicesQuery())).searchParams.get('status')).toBe('unpaid');
    expect((await urlOf(Q.billsQuery())).searchParams.get('status')).toBe('unpaid');
    expect(RULES['/api/v2/finance/invoices']?.params.status?.('open')).toBe(false);
  });

  it('reading as a teammate sends view_as and keeps its own cache', async () => {
    expect((await urlOf(Q.transactionsQuery(AS))).searchParams.get('view_as')).toBe(AS);
    expect((await urlOf(Q.transactionsQuery())).searchParams.has('view_as')).toBe(false);
    expect(Q.transactionsQuery(AS).queryKey).not.toEqual(Q.transactionsQuery().queryKey);
  });
});
