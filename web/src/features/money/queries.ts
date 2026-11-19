import { queryOptions } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type {
  FinanceAction,
  FinanceAttributionRules,
  FinanceBalanceSheet,
  FinanceBills,
  FinanceBillsAddress,
  FinanceChart,
  FinanceContacts,
  FinanceLineAttribution,
  FinanceStatementReview,
  FinanceBrief,
  FinanceCards,
  FinanceInbox,
  FinanceConnectionRequest,
  FinanceConnectionResult,
  FinanceInvoices,
  FinanceMercuryUsers,
  FinanceMetric,
  FinancePayFrom,
  FinancePnl,
  FinanceSalesTax,
  FinanceTransactions,
} from '@/api/schema';
import { QUIET_STALE_MS, queryClient, quietRoot, write } from '@/data';
import { newId } from '@/lib';

/* Finance, budgets and Growth. None has a live topic: each root is read again after the person's own writes, on
   reconnect and on open when older than a minute. Another builder draws Company widgets under ['finance', ...]. */
quietRoot('finance');
quietRoot('budget');
quietRoot('growth');

const quiet = { staleTime: QUIET_STALE_MS } as const;
const enc = encodeURIComponent;
const qs = (o: Record<string, string | number | undefined>) =>
  new URLSearchParams(Object.entries(o).filter((e): e is [string, string | number] => e[1] !== undefined).map(([k, v]) => [k, String(v)])).toString();

/** A read the owner makes as a teammate (`view_as`, backend as_viewer); it has its own key, so the two views never mix.
    Every read below that takes `as` is one whose route takes `view_as`. */
type Path = `/api/${string}`;
const viewAs = (url: Path, as: string | undefined): Path => (as ? `${url}${url.includes('?') ? '&' : '?'}view_as=${enc(as)}` : url);
const asKey = (as: string | undefined) => (as ? [{ as }] : []);

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

export const pnlQuery = (p: PnlParams, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'pnl', p, ...asKey(as)],
    queryFn: ({ signal }) => api.get<Pnl>(viewAs(`/api/v2/finance/pnl?${qs({ ...p })}`, as), { signal }),
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
export const lineQuery = (account: string, span: { start: string; end: string } | undefined, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'line', account, { start: span?.start, end: span?.end }, ...asKey(as)],
    queryFn: ({ signal }) =>
      api.get<LineDetail>(viewAs(`/api/v2/finance/lines/${enc(account)}?${qs({ start: span?.start, end: span?.end, top_n: 8, limit: 12 })}`, as), { signal }),
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
  /** A books connection: `native`, `mirror` (it is the books), `exporting` or `reconnect` (tab.py _books_mirror). */
  mirror?: string | null;
}
export interface SourceGroup {
  group: string;
  name: string;
  count: number;
  rows: SourceRow[];
}
/** GET /api/v2/finance/sources: each feed by group (banks, cards, payments, payroll, bills, Tico, books, manual). */
export const sourcesQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'sources', ...asKey(as)],
    queryFn: ({ signal }) => orNull(api.get<{ groups: SourceGroup[] }>(viewAs('/api/v2/finance/sources', as), { signal })),
    ...quiet,
  });

/** After a books sign-in's return: Finance read again, and the sources as the server has them now. */
export async function rereadSources(as?: string): Promise<{ groups: SourceGroup[] } | null> {
  await queryClient.invalidateQueries({ queryKey: ['finance'] });
  return queryClient.fetchQuery(sourcesQuery(as));
}

export interface AccessTeammate {
  teammate: string;
  kind: string;
  default: 'everything' | 'team_expenses' | 'own_expenses' | 'none';
  rights?: string[];
  grants?: { lines?: string[]; teams?: string[] }[] | unknown[];
  team?: string | null;
}
export interface AccessDefaults {
  manager: string;
  manager_payroll: string;
  member: string;
}
export interface Access {
  can_edit_defaults?: boolean;
  /** The owner may read Finance as any teammate (`view_as`). */
  can_view_as?: boolean;
  defaults?: AccessDefaults;
  teammates: AccessTeammate[];
  teams?: { id: string; name: string }[];
}
export const accessQuery = queryOptions({
  queryKey: ['finance', 'access'],
  queryFn: ({ signal }) => orNull(api.get<Access>('/api/v2/finance/access', { signal })),
  ...quiet,
});

export const metricQuery = (name: 'cash', as?: string) =>
  queryOptions({
    queryKey: ['finance', 'metric', name, ...asKey(as)],
    queryFn: ({ signal }) => orNull(api.get<FinanceMetric>(viewAs(`/api/v2/finance/metrics/${name}`, as), { signal })),
    ...quiet,
  });

/* Request shapes follow backend/finance_routes.py: list limits stop at 200, and `unpaid` is the server's word for every
   status still owed (bills: open, scheduled; invoices: sent, partial). Invoices have no `open` status, so it 422s. */
export const BILLS_URL = '/api/v2/finance/bills?status=unpaid&limit=200';
export const INVOICES_URL = '/api/v2/finance/invoices?status=unpaid&limit=200';
export const TRANSACTIONS_URL = '/api/v2/finance/transactions?limit=200';

export const billsQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'bills', 'unpaid', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceBills>(viewAs(BILLS_URL, as), { signal }),
    ...quiet,
  });

export const invoicesQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'invoices', 'unpaid', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceInvoices>(viewAs(INVOICES_URL, as), { signal }),
    ...quiet,
  });

export const inboxQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'inbox', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceInbox>(viewAs('/api/v2/finance/inbox?limit=20', as), { signal }),
    ...quiet,
  });

export const briefQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'brief', ...asKey(as)],
    queryFn: ({ signal }) => orNull(api.get<FinanceBrief>(viewAs('/api/v2/finance/brief', as), { signal })),
    ...quiet,
  });

export const transactionsQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'transactions', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceTransactions>(viewAs(TRANSACTIONS_URL, as), { signal }),
    ...quiet,
  });

const loadingTx = new Set<string>();
/** The next 200 transactions, after the ones already read (the answer's `next` cursor), added to the same list. */
export async function loadMoreTransactions(as?: string): Promise<void> {
  const key = transactionsQuery(as).queryKey;
  const have = queryClient.getQueryData<FinanceTransactions>(key);
  if (!have?.next || loadingTx.has(as ?? '')) return;
  loadingTx.add(as ?? '');
  try {
    const page = await api.get<FinanceTransactions>(viewAs(`${TRANSACTIONS_URL}&cursor=${enc(have.next)}`, as));
    queryClient.setQueryData<FinanceTransactions>(key, (cur) => {
      if (!cur) return cur;
      const seen = new Set(cur.rows.map((r) => r.id));
      return { ...cur, rows: [...cur.rows, ...page.rows.filter((r) => !seen.has(r.id))], next: page.next ?? null };
    });
  } finally {
    loadingTx.delete(as ?? '');
  }
}

export const cardsQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'cards', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceCards>(viewAs('/api/v2/finance/cards?limit=200', as), { signal }),
    ...quiet,
  });

export const balanceQuery = (asOf?: string, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'balance', asOf ?? 'today', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceBalanceSheet>(viewAs(`/api/v2/finance/balance-sheet${asOf ? `?as_of=${asOf}` : ''}`, as), { signal }),
    ...quiet,
  });

export const taxQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'tax', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceSalesTax>(viewAs('/api/v2/finance/tax/sales-tax', as), { signal }),
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
export const budgetsQuery = (at: string, as?: string) =>
  queryOptions({
    queryKey: [...budgetsKey(at), ...asKey(as)],
    queryFn: ({ signal }) => api.get<Budgets>(viewAs(`/api/v2/finance/budgets?at=${at}&limit=500`, as), { signal }),
    ...quiet,
  });

export interface BudgetHistory {
  columns: { start: string }[];
  budgets: { id: string; budget: (number | null)[]; actual?: (number | null)[] }[];
}
export const budgetHistoryQuery = (start: string, end: string, as?: string) =>
  queryOptions({
    queryKey: ['budget', 'history', { start, end }, ...asKey(as)],
    queryFn: ({ signal }) =>
      api.get<BudgetHistory>(viewAs(`/api/v2/finance/reports/budget_vs_actual?${qs({ period: 'month', start, end, top_n: 200 })}`, as), { signal }),
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

// ---------------------------------------------------------------- decisions, sources and access (the old app's dialogs)

/** What a decision waiting for approval would leave in each budget it charges (cents). */
export interface Impact {
  budgets?: { name: string; remaining: number | null; remaining_after: number | null }[];
}
export type Decided = FinanceAction & { approver?: string | null; impact?: Impact };

/** A decision, tried first: the rules say whether it goes through or whom it waits for. Nothing is written. */
export const tryDecision = (kind: string, payload: object) => api.post<Decided>('/api/v2/finance/actions', { kind, payload, dry_run: true });

/** A decision sent: done, or waiting for its approver (said by the caller). Anything else is refused and throws. */
export async function decide(kind: string, payload: object): Promise<Decided> {
  // One key for this press, so a request sent twice (a retry on a dropped answer) moves money once.
  const out = await api.post<Decided>('/api/v2/finance/actions', { kind, payload }, { idempotencyKey: newId() });
  void queryClient.invalidateQueries({ queryKey: ['finance'] });
  if (out.status === 'committed' || out.status === 'needs_approval') return out;
  throw Object.assign(new Error(out.reason || out.status), { code: out.code || out.status });
}

export const payFromQuery = queryOptions({
  queryKey: ['finance', 'pay-from'],
  queryFn: ({ signal }) => api.get<FinancePayFrom>('/api/v2/finance/pay-from', { signal }),
  ...quiet,
});

export const mercuryUsersQuery = queryOptions({
  queryKey: ['finance', 'mercury-users'],
  queryFn: ({ signal }) => orNull(api.get<FinanceMercuryUsers>('/api/v2/finance/mercury-users', { signal })),
  ...quiet,
});

export interface Hook {
  id: string;
  preset?: string | null;
  last_received?: string | null;
}
/** The company's webhooks; Finance looks for its Stripe one (preset stripe_finance). */
export const hooksQuery = queryOptions({
  queryKey: ['finance', 'hooks'],
  queryFn: ({ signal }) => orNull(api.get<{ triggers?: Hook[] }>('/api/v2/triggers', { signal })),
  ...quiet,
});

export type KeyProvider = 'mercury' | 'brex' | 'stripe';
export const KEY_SOURCES: Record<KeyProvider, string> = { mercury: 'Mercury', brex: 'Brex', stripe: 'Stripe' };
export const BOOKS: Record<'quickbooks' | 'xero', string> = { quickbooks: 'QuickBooks', xero: 'Xero' };
export interface ToolKey {
  id: string;
  access_level?: string;
  stored?: boolean;
  preview?: string | null;
}
export interface ServiceKey {
  tool: { id: string; name: string };
  key: ToolKey;
}

/** The service's keys on Tools (GET /api/v2/tool-index, then each API tool). A failed read throws: guessing "none"
    would store a second copy of a key already there. */
export async function serviceKeys(service: KeyProvider): Promise<{ tools: { id: string; name: string; keys: ToolKey[] }[] }> {
  const idx = await api.get<{ rows?: { id: string; kind: string; name: string; service?: string | null }[] }>('/api/v2/tool-index');
  const rows = (idx.rows ?? []).filter((r) => r.kind === 'api' && String(r.service ?? '').trim().toLowerCase().replace(/_/g, '-') === service);
  const tools = await Promise.all(
    rows.map(async (r) => {
      const id = String(r.id).replace(/^api:/, '');
      const t = await api.get<{ keys?: ToolKey[] }>(`/api/v2/api-tools/${enc(id)}`);
      return { id, name: r.name, keys: t.keys ?? [] };
    }),
  );
  return { tools };
}
export const serviceKeysQuery = (service: KeyProvider) =>
  queryOptions({ queryKey: ['finance', 'keys', service], queryFn: () => serviceKeys(service), staleTime: 0 });

/** A pasted key lands on Tools at its level: on the service's first tool with no key at that level, else on a new
    tool. Someone who may not change Tools keeps it in the vault for Finance alone. `undo` takes it off Tools again. */
async function storeKey(service: KeyProvider, name: string, token: string, level: string): Promise<{ id: string; undo: () => Promise<void> }> {
  const { tools } = await serviceKeys(service);
  let tool = tools.find((t) => !t.keys.some((k) => k.access_level === level))?.id;
  let made = false;
  const drop = async (key?: string) => {
    if (!tool) return;
    // Best effort: a key left behind shows on Tools.
    if (key) await api.delete(`/api/v2/api-tools/${enc(tool)}/keys/${enc(key)}`).catch(() => undefined);
    if (made) await api.delete(`/api/v2/api-tools/${enc(tool)}`).catch(() => undefined);
  };
  try {
    if (!tool) {
      tool = (await api.post<{ id: string }>('/api/v2/api-tools', { name, service })).id;
      made = true;
    }
    const key = await api.post<{ id: string }>(`/api/v2/api-tools/${enc(tool)}/keys`, { name, value: token, access_level: level });
    return { id: key.id, undo: () => drop(key.id) };
  } catch (err) {
    await drop();
    if (!(err instanceof ApiError && err.status === 403)) throw err;
  }
  const cred = await api.post<{ id: string }>('/api/v2/credentials', { name, kind: 'token', secret: token, username: '', env: '', source: 'finance' });
  return { id: cred.id, undo: async () => undefined };
}

/** Connects a bank, card or Stripe account with a key from Tools or one pasted now. A pasted key whose connect fails
    is taken off Tools again, so a retry starts clean. The key never comes back. */
export async function connectSource(
  provider: KeyProvider,
  name: string,
  key: { id: string } | { token: string; level: string },
  extra: Pick<FinanceConnectionRequest, 'approver_email' | 'history_from'>,
): Promise<FinanceConnectionResult> {
  const stored = 'id' in key ? { id: key.id, undo: async () => undefined } : await storeKey(provider, name, key.token, key.level);
  try {
    const out = await api.post<FinanceConnectionResult>('/api/v2/finance/connections', { provider, credential_id: stored.id, name, ...extra });
    if (out.status !== 'committed' && out.status !== 'needs_approval') throw Object.assign(new Error(out.reason || 'Not connected'), { code: out.code || out.status });
    void queryClient.invalidateQueries({ queryKey: ['finance'] });
    return out;
  } catch (err) {
    await stored.undo();
    throw err;
  }
}

/** QuickBooks or Xero: the address of its sign-in page. The server takes the browser's return. */
export async function booksSignIn(provider: 'quickbooks' | 'xero'): Promise<string> {
  const r = await api.post<{ url?: string }>('/api/v2/finance/books/connect', { provider });
  if (!String(r.url ?? '').startsWith('https://')) throw Object.assign(new Error('did not answer'), { code: 'no_answer' });
  return r.url!;
}

export async function disconnectBooks(connection: string): Promise<void> {
  await api.post(`/api/v2/finance/books/${enc(connection)}/disconnect`, {});
  void queryClient.invalidateQueries({ queryKey: ['finance'] });
}

/** Stripe's webhook: a Finance trigger. Its address and a made signing secret show once. */
export async function addStripeHook(secret: string | undefined): Promise<[string, string][]> {
  const made = (await api.post<{ trigger: { id: string; url: string; replayed?: boolean } }>('/api/v2/triggers', { name: 'Stripe (Finance)' })).trigger;
  const signed = (
    await api.post<{ trigger: { signing_secret?: string; replayed?: boolean } }>(`/api/v2/triggers/${enc(made.id)}/signing`, {
      preset: 'stripe_finance',
      ...(secret ? { secret } : {}),
    })
  ).trigger;
  void queryClient.invalidateQueries({ queryKey: ['finance', 'hooks'] });
  const shown: [string, string | undefined][] = [
    ['Address', made.replayed ? undefined : made.url],
    ['Signing secret', secret || signed.replayed ? undefined : signed.signing_secret],
  ];
  return shown.filter((p): p is [string, string] => !!p[1]);
}

// ---------------------------------------------------------------- the bills address, a statement, attribution, history

/** Sets the address bills are emailed to, or clears it (null). One that is no Tico inbox is kept, with no inbox id. */
export async function setBillsAddress(address: string | null): Promise<FinanceBillsAddress> {
  const out = await api.put<FinanceBillsAddress>('/api/v2/finance/bills-address', { address });
  void queryClient.invalidateQueries({ queryKey: ['finance', 'bills'] });
  return out;
}

export type StatementReview = FinanceStatementReview;
export type Signs = 'auto' | 'as_shown' | 'flipped';
/** A statement file as the server reads it: its bytes in base64 (it keeps none), at most 1 MB. */
export const STATEMENT_MAX = 1_000_000;

/** What posting the file would add, the tie-out and a token that posts exactly this (statement_upload.py preview). */
export const previewStatement = (money: string, file: string, name: string, signs: Signs) =>
  api.post<StatementReview>('/api/v2/finance/statements/preview', { money_account_id: money, file, file_name: name, signs });

/** The file's bytes as base64, built in slices so a large file does not overflow the stack. */
export async function base64Of(file: Blob): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

export type LineShare = FinanceLineAttribution;
/** Who a line is attributed to, where that came from, and its history (newest first). */
export const lineShareQuery = (line: string, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'attribution', line, ...asKey(as)],
    queryFn: ({ signal }) => api.get<LineShare>(viewAs(`/api/v2/finance/lines/${enc(line)}/attribution`, as), { signal }),
    ...quiet,
  });

/** What a rule can match, by kind: vendors, P&L accounts, or the bank and card accounts. */
export const vendorsQuery = queryOptions({
  queryKey: ['finance', 'contacts', 'vendor'],
  queryFn: ({ signal }) => api.get<FinanceContacts>('/api/v2/finance/contacts?kind=vendor&limit=200', { signal }),
  ...quiet,
});
export const journalChartQuery = queryOptions({
  queryKey: ['finance', 'chart', 'journal'],
  queryFn: ({ signal }) => api.get<FinanceChart>('/api/v2/finance/chart?for=journal', { signal }),
  ...quiet,
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
