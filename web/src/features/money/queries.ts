import { queryOptions } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type {
  FinanceAction,
  FinanceAttributionRules,
  FinanceBalanceSheet,
  FinanceBills,
  FinanceBrief,
  FinanceCards,
  FinanceInbox,
  FinanceInvoices,
  FinanceMetric,
  FinancePnl,
  FinanceSalesTax,
  FinanceTransactions,
} from '@/api/schema';
import { QUIET_STALE_MS, quietRoot, write } from '@/data';

/* Finance, budgets and Growth. None has a live topic: each root is read again after the person's own writes, on
   reconnect and on open when older than a minute. Another builder draws Company widgets under ['finance', ...]. */
quietRoot('finance');
quietRoot('budget');
quietRoot('growth');

const quiet = { staleTime: QUIET_STALE_MS } as const;
const enc = encodeURIComponent;
const qs = (o: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(o).filter((e): e is [string, string | number] => e[1] !== undefined).map(([k, v]) => [k, String(v)])).toString();

/** A read that 404s (Finance not set up) or is refused is null, so the page can say so instead of failing. */
async function orNull<T>(p: Promise<T>): Promise<T | null> {
  try {
    return await p;
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 403)) return null;
    throw err;
  }
}

// ---------------------------------------------------------------- Finance reads (money is in cents)

export type Pnl = FinancePnl;
export type PnlRow = FinancePnl['rows'][number];
export type PnlCol = FinancePnl['columns'][number];
export type DocRow = FinanceBills['bills'][number];
export type Card = FinanceCards['cards'][number];
export type Tx = FinanceTransactions['rows'][number];

export interface PnlParams {
  start: string;
  end: string;
  period: 'week' | 'month' | 'quarter' | 'year';
  basis: 'cash' | 'accrual';
}

export const pnlQuery = (p: PnlParams) =>
  queryOptions({
    queryKey: ['finance', 'pnl', p],
    queryFn: ({ signal }) => api.get<Pnl>(`/api/v2/finance/pnl?${qs({ ...p })}`, { signal }),
    ...quiet,
  });

export interface LineGroup {
  key: string;
  label: string;
  amount: number;
}
export interface LineEntry {
  line_id: string;
  entry_id: string;
  date: string;
  amount: number;
  vendor_name?: string;
  description?: string;
  source?: string;
}
/** GET /api/v2/finance/lines/{account} (backend/finance/reads.py); the schema leaves it untyped. */
export interface LineDetail {
  account: { id: string; code: string; name: string; type: string };
  total: number;
  by_vendor?: LineGroup[];
  by_team?: LineGroup[];
  by_teammate?: LineGroup[];
  entries: LineEntry[];
  paid_by: { kind: 'card' | 'bank' | 'entry'; name: string; amount: number }[] | null;
}

/** One line over one period; no period (the column before the first) reads nothing. */
export const lineQuery = (account: string, span: { start: string; end: string } | undefined) =>
  queryOptions({
    queryKey: ['finance', 'line', account, { start: span?.start, end: span?.end }],
    queryFn: ({ signal }) =>
      api.get<LineDetail>(`/api/v2/finance/lines/${enc(account)}?${qs({ start: span?.start, end: span?.end, top_n: 8, limit: 12 })}`, { signal }),
    enabled: !!span,
    ...quiet,
  });

export interface SourceRow {
  id: string;
  kind: string;
  name: string;
  provider?: string | null;
  last4?: string | null;
  connected?: boolean;
  entries?: number | null;
  bills?: number | null;
  invoices?: number | null;
  manual?: boolean;
  sync?: { status?: string | null; last_ok_at?: string | null; last_sync_at?: string | null; last_error?: string | null } | null;
}
export interface SourceGroup {
  group: string;
  name: string;
  count: number;
  rows: SourceRow[];
}
/** GET /api/v2/finance/sources: each feed by group (banks, cards, payments, payroll, bills, Tico, books, manual). */
export const sourcesQuery = queryOptions({
  queryKey: ['finance', 'sources'],
  queryFn: ({ signal }) => orNull(api.get<{ groups: SourceGroup[] }>('/api/v2/finance/sources', { signal })),
  ...quiet,
});

export interface AccessTeammate {
  teammate: string;
  kind: string;
  default: 'everything' | 'team_expenses' | 'own_expenses' | 'none';
  rights?: string[];
  grants?: { lines?: string[]; teams?: string[] }[] | unknown[];
  team?: string | null;
}
export interface Access {
  can_edit_defaults?: boolean;
  teammates: AccessTeammate[];
}
export const accessQuery = queryOptions({
  queryKey: ['finance', 'access'],
  queryFn: ({ signal }) => orNull(api.get<Access>('/api/v2/finance/access', { signal })),
  ...quiet,
});

export const metricQuery = (name: 'cash') =>
  queryOptions({
    queryKey: ['finance', 'metric', name],
    queryFn: ({ signal }) => orNull(api.get<FinanceMetric>(`/api/v2/finance/metrics/${name}`, { signal })),
    ...quiet,
  });

export const billsQuery = queryOptions({
  queryKey: ['finance', 'bills', 'open'],
  queryFn: ({ signal }) => api.get<FinanceBills>('/api/v2/finance/bills?status=open&limit=200', { signal }),
  ...quiet,
});

export const invoicesQuery = queryOptions({
  queryKey: ['finance', 'invoices', 'open'],
  queryFn: ({ signal }) => api.get<FinanceInvoices>('/api/v2/finance/invoices?status=open&limit=200', { signal }),
  ...quiet,
});

export const inboxQuery = queryOptions({
  queryKey: ['finance', 'inbox'],
  queryFn: ({ signal }) => api.get<FinanceInbox>('/api/v2/finance/inbox?limit=20', { signal }),
  ...quiet,
});

export const briefQuery = queryOptions({
  queryKey: ['finance', 'brief'],
  queryFn: ({ signal }) => orNull(api.get<FinanceBrief>('/api/v2/finance/brief', { signal })),
  ...quiet,
});

export const transactionsQuery = queryOptions({
  queryKey: ['finance', 'transactions'],
  queryFn: ({ signal }) => api.get<FinanceTransactions>('/api/v2/finance/transactions?limit=500', { signal }),
  ...quiet,
});

export const cardsQuery = queryOptions({
  queryKey: ['finance', 'cards'],
  queryFn: ({ signal }) => api.get<FinanceCards>('/api/v2/finance/cards?limit=200', { signal }),
  ...quiet,
});

export const balanceQuery = (asOf?: string) =>
  queryOptions({
    queryKey: ['finance', 'balance', asOf ?? 'today'],
    queryFn: ({ signal }) => api.get<FinanceBalanceSheet>(`/api/v2/finance/balance-sheet${asOf ? `?as_of=${asOf}` : ''}`, { signal }),
    ...quiet,
  });

export const taxQuery = queryOptions({
  queryKey: ['finance', 'tax'],
  queryFn: ({ signal }) => api.get<FinanceSalesTax>('/api/v2/finance/tax/sales-tax', { signal }),
  ...quiet,
});

export const rulesQuery = queryOptions({
  queryKey: ['finance', 'rules'],
  queryFn: ({ signal }) => api.get<FinanceAttributionRules>('/api/v2/finance/attribution-rules', { signal }),
  ...quiet,
});

// ---------------------------------------------------------------- budgets

/** One budget as GET /api/v2/finance/budgets lists it (the schema's row type is narrower than the route's answer). */
export interface Budget {
  id: string;
  name: string;
  account_id: string | null;
  parent_id: string | null;
  catch_all?: boolean;
  team?: string | null;
  teammate?: string | null;
  vendor?: string | null;
  period: string;
  active?: boolean;
  state: { amount: number | null; actual: number | null; pace_end?: number | null; remaining?: number | null; as_of?: string | null };
}
export interface Budgets {
  budgets: Budget[];
  more?: boolean;
}

export const budgetsKey = (at: string) => ['budget', 'list', at] as const;
export const budgetsQuery = (at: string) =>
  queryOptions({
    queryKey: budgetsKey(at),
    queryFn: ({ signal }) => api.get<Budgets>(`/api/v2/finance/budgets?at=${at}&limit=500`, { signal }),
    ...quiet,
  });

export interface BudgetHistory {
  columns: { start: string }[];
  budgets: { id: string; budget: (number | null)[]; actual?: (number | null)[] }[];
}
export const budgetHistoryQuery = (start: string, end: string) =>
  queryOptions({
    queryKey: ['budget', 'history', { start, end }],
    queryFn: ({ signal }) => api.get<BudgetHistory>(`/api/v2/finance/reports/budget_vs_actual?${qs({ period: 'month', start, end, top_n: 200 })}`, { signal }),
    ...quiet,
  });

/** A Finance change. Anything but committed is refused here, so `write` takes the change back: an approval is not
    the change yet. */
async function act(kind: string, payload: object, idempotencyKey: string): Promise<FinanceAction> {
  const out = await api.post<FinanceAction>('/api/v2/finance/actions', { kind, payload }, { idempotencyKey });
  if (out.status === 'committed') return out;
  throw Object.assign(new Error(out.reason || out.status), { code: out.status === 'needs_approval' ? 'needs_approval' : out.code || out.status });
}

type BudgetsData = Budgets | undefined;
const mapBudgets = (f: (list: Budget[]) => Budget[]) => (cur: BudgetsData) => (cur ? { ...cur, budgets: f(cur.budgets) } : cur);

/** The budget's amount from this month on. */
export const setBudgetAmount = (at: string, id: string, cents: number) =>
  write({
    key: budgetsKey(at),
    optimistic: mapBudgets((l) => l.map((b) => (b.id === id ? { ...b, state: { ...b.state, amount: cents } } : b))),
    send: (idem) => act('budget_update', { budget_id: id, amount: cents, effective_from: `${at.slice(0, 7)}-01` }, idem),
  });

/** Stops a budget from this month on (the route keeps its history). */
export const removeBudget = (at: string, id: string) =>
  write({
    key: budgetsKey(at),
    optimistic: mapBudgets((l) => l.filter((b) => b.id !== id)),
    send: (idem) => act('budget_update', { budget_id: id, active: false, effective_from: `${at.slice(0, 7)}-01` }, idem),
  });

/** A monthly budget for one P&L line, shown at once under a pending id. */
export const addBudget = (at: string, row: { name: string; line: string; cents: number }) =>
  write({
    key: budgetsKey(at),
    optimistic: mapBudgets((l) => [
      ...l,
      { id: `tmp:${row.line}`, name: row.name, account_id: row.line, parent_id: null, period: 'month', active: true, state: { amount: row.cents, actual: 0 } },
    ]),
    send: (idem) =>
      act('budget_create', { name: row.name, period: 'month', account_id: row.line, amount: row.cents, effective_from: `${at.slice(0, 7)}-01` }, idem),
  });

// ---------------------------------------------------------------- Growth (dollars; rates are 0 to 1)

export interface GrowthFig {
  value?: number | null;
  status?: string;
  verdict?: string | null;
  n?: number;
}
export interface GrowthChannel {
  slug: string;
  name?: string;
  signups?: number;
  new_customers: number | null;
  owner?: string | null;
  cost?: number | null;
  cac?: number | null;
  status?: string;
}
export interface GrowthPoint {
  period: string;
  signups: number | null;
  new_customers: number | null;
  cost: number | null;
  cac: number | null;
  kept: number | null;
}
export type GrowthWin = { value: number | null; n: number; status: string };
export interface GrowthCohort {
  cohort: string;
  entered: number;
  kept_d30?: GrowthWin;
  kept_d90?: GrowthWin;
  kept_d180?: GrowthWin;
}
export interface GrowthMetric {
  id: string;
  name: string;
  unit: string;
  value: number | null;
  estimate?: boolean;
}
/** GET /api/v2/growth/read (backend/growth_core.py `read`); the schema leaves it untyped. */
export interface GrowthRead {
  scope: string;
  period: string;
  partial?: boolean;
  headline: {
    signups?: GrowthFig;
    activated_rate?: GrowthFig;
    paid_rate?: GrowthFig;
    new_customers?: GrowthFig;
    cost?: GrowthFig;
    cac?: GrowthFig;
    ltv?: GrowthFig;
    ltv_cac?: GrowthFig;
    payback_months?: GrowthFig;
  };
  channels: GrowthChannel[];
  series?: GrowthPoint[];
  retention?: GrowthCohort[];
  metrics: GrowthMetric[];
}

export const growthScope = (segment?: string) => (segment ? `segment:${segment}` : 'all');

export const growthReadQuery = (segment: string | undefined, month: string | undefined, detail = true) =>
  queryOptions({
    queryKey: ['growth', 'read', growthScope(segment), month ?? 'last', detail],
    queryFn: ({ signal }) =>
      api.get<GrowthRead>(`/api/v2/growth/read?${qs({ scope: growthScope(segment), detail: detail ? 'series,retention' : undefined, period: month })}`, { signal }),
    ...quiet,
  });

export interface Segment {
  slug: string;
  name: string;
  customer_rule?: string | null;
  channels?: string[];
}
export const segmentsQuery = queryOptions({
  queryKey: ['growth', 'segments'],
  queryFn: ({ signal }) => api.get<{ segments: Segment[] }>('/api/v2/growth/segments', { signal }),
  ...quiet,
});

export interface GrowthSource {
  id: string;
  name?: string;
  system?: string;
  kind?: string;
  truth_for?: string[] | string | null;
  fills?: string[];
  reach_ref?: string | null;
  health: 'fresh' | 'stale' | 'failing' | 'waiting' | 'not_synced';
  last_run_at?: string | null;
  last_good_run?: string | null;
  retired_at?: string | null;
}
export const growthSourcesQuery = queryOptions({
  queryKey: ['growth', 'sources'],
  queryFn: ({ signal }) => api.get<{ sources: GrowthSource[] }>('/api/v2/growth/sources', { signal }),
  ...quiet,
});
