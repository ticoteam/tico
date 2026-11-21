import { infiniteQueryOptions, keepPreviousData, queryOptions, type InfiniteData, type QueryClient } from '@tanstack/react-query';
import { api, ApiError } from '@/api/client';
import type {
  FinanceAction,
  FinanceAttributionRules,
  FinanceBalanceSheet,
  FinanceBills,
  FinanceBillsAddress,
  FinanceChart,
  FinanceContacts,
  FinanceDocument,
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
import { directoryQuery, isNetworkError, pageOwnersQuery, QUIET_STALE_MS, queryClient, quietRoot, write, type Directory, type PageOwnerRow } from '@/data';
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
  /** A bank row's own words: an entry from a feed often has no vendor or description. */
  memo?: string;
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
}

/** One line over one period; no period (the column before the first) reads nothing. */
export const lineQuery = (account: string, span: { start: string; end: string } | undefined, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'line', account, { start: span?.start, end: span?.end }, ...asKey(as)],
    queryFn: ({ signal }) =>
      api.get<LineDetail>(viewAs(`/api/v2/finance/lines/${enc(account)}?${qs({ start: span?.start, end: span?.end, top_n: 8, limit: 12 })}`, as), { signal }),
    enabled: !!span,
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
});

export const metricQuery = (name: 'cash', as?: string) =>
  queryOptions({
    queryKey: ['finance', 'metric', name, ...asKey(as)],
    queryFn: ({ signal }) => orNull(api.get<FinanceMetric>(viewAs(`/api/v2/finance/metrics/${name}`, as), { signal })),
  });

/* The open totals cover every visible unpaid document regardless of the row limit. The overview only uses those totals. */
export const billsQuery = (as?: string, limit = 200) =>
  queryOptions({
    queryKey: ['finance', 'bills', 'unpaid', limit, ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceBills>(viewAs(`/api/v2/finance/bills?status=unpaid&limit=${limit}`, as), { signal }),
  });

export const invoicesQuery = (as?: string, limit = 200) =>
  queryOptions({
    queryKey: ['finance', 'invoices', 'unpaid', limit, ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceInvoices>(viewAs(`/api/v2/finance/invoices?status=unpaid&limit=${limit}`, as), { signal }),
  });

export const inboxQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'inbox', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceInbox>(viewAs('/api/v2/finance/inbox?limit=20', as), { signal }),
  });

export const briefQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'brief', ...asKey(as)],
    queryFn: ({ signal }) => orNull(api.get<FinanceBrief>(viewAs('/api/v2/finance/brief', as), { signal })),
  });

/** What the Transactions filter holds: the source, a status, a line. */
interface TxFilter {
  src?: 'feed' | 'entry';
  status?: string;
  line?: string;
}

/** The server filters and pages the list together, keeping the same filter and viewer through each cursor. */
export const transactionsQuery = (as?: string, f: TxFilter = {}) =>
  infiniteQueryOptions({
    queryKey: ['finance', 'transactions', { src: f.src, status: f.status, line: f.line }, ...asKey(as)],
    initialPageParam: null as string | null,
    getNextPageParam: (page: FinanceTransactions) => page.next ?? undefined,
    queryFn: ({ signal, pageParam }) => {
      const params = qs({ limit: 200, cursor: pageParam ?? undefined, source: f.src, status: f.status, account_id: f.line });
      return api.get<FinanceTransactions>(viewAs(`/api/v2/finance/transactions?${params}`, as), { signal });
    },
    // another filter keeps the last rows on screen until the answer comes, rather than the list's loading state
    placeholderData: keepPreviousData,
  });

export const cardsQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'cards', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceCards>(viewAs('/api/v2/finance/cards?limit=200', as), { signal }),
  });

export const balanceQuery = (asOf?: string, as?: string) =>
  queryOptions({
    queryKey: ['finance', 'balance', asOf ?? 'today', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceBalanceSheet>(viewAs(`/api/v2/finance/balance-sheet${asOf ? `?as_of=${asOf}` : ''}`, as), { signal }),
  });

export const taxQuery = (as?: string) =>
  queryOptions({
    queryKey: ['finance', 'tax', ...asKey(as)],
    queryFn: ({ signal }) => api.get<FinanceSalesTax>(viewAs('/api/v2/finance/tax/sales-tax', as), { signal }),
  });

export const rulesQuery = queryOptions({
  queryKey: ['finance', 'rules'],
  queryFn: ({ signal }) => api.get<FinanceAttributionRules>('/api/v2/finance/attribution-rules', { signal }),
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
  columns: { start: string; end?: string }[];
  budgets: { id: string; budget: (number | null)[]; actual?: (number | null)[] }[];
}
/** Each budget's amount and spend by month, quarter or year over a window (the buckets the window touches, whole). */
export const budgetHistoryQuery = (start: string, end: string, as?: string, period: 'month' | 'quarter' | 'year' = 'month') =>
  queryOptions({
    queryKey: ['budget', 'history', { start, end, period }, ...asKey(as)],
    queryFn: ({ signal }) =>
      api.get<BudgetHistory>(viewAs(`/api/v2/finance/reports/budget_vs_actual?${qs({ period, start, end, top_n: 200 })}`, as), { signal }),
  });

/** A Finance change. Anything but committed is refused here, so `write` takes the change back: an approval is not
    the change yet. It goes through `post`, so a press again after a lost answer reuses the key and acts once. */
async function act(kind: string, payload: object): Promise<FinanceAction> {
  const out = await post<FinanceAction>(kind, payload);
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
    send: () => act('budget_update', { budget_id: id, amount: cents, effective_from: `${at.slice(0, 7)}-01` }),
  });

/** Stops a budget from this month on (the route keeps its history). */
export const removeBudget = (at: string, id: string) =>
  write({
    key: budgetsKey(at),
    wait: true,
    optimistic: mapBudgets((l) => l.filter((b) => b.id !== id)),
    send: () => act('budget_update', { budget_id: id, active: false, effective_from: `${at.slice(0, 7)}-01` }),
  });

/** A new budget: on a P&L line, under another budget, for a teammate or a vendor, by month, quarter or year. */
export interface NewBudgetRow {
  name: string;
  line: string;
  cents: number;
  period?: 'month' | 'quarter' | 'year';
  parent?: string;
  teammate?: string;
  vendor?: string;
  /** Warn at this share used, in percent; the server's default is 80 (core/budgets.py). */
  warn?: number;
  /** Its first month, YYYY-MM; the month `at` is in when left out. */
  from?: string;
}
/** A new budget's pending id, until the list is read again. */
export const tmpBudgetId = (row: { line: string; teammate?: string; vendor?: string }) => `tmp:${[row.line, row.teammate ?? row.vendor].filter(Boolean).join(':')}`;

/** A budget from its first month on, shown at once under a pending id when that month has begun. Only what was chosen
    is sent, so the server's defaults hold for the rest (finance-budgets.ts finBudgetAdd). */
export const addBudget = (at: string, row: NewBudgetRow) => {
  const from = row.from ?? at.slice(0, 7);
  const pending: Budget = {
    id: tmpBudgetId(row),
    name: row.name,
    account_id: row.line,
    parent_id: row.parent ?? null,
    teammate: row.teammate ?? null,
    vendor: row.vendor ?? null,
    period: row.period ?? 'month',
    active: true,
    state: { amount: row.cents, actual: 0 },
  };
  return write({
    key: budgetsKey(at),
    optimistic: mapBudgets((l) => (from > at.slice(0, 7) ? l : [...l, pending])),
    send: () =>
      act('budget_create', {
        name: row.name,
        period: row.period ?? 'month',
        account_id: row.line,
        amount: row.cents,
        effective_from: `${from}-01`,
        ...(row.parent ? { parent_id: row.parent } : {}),
        ...(row.teammate ? { teammate: row.teammate } : {}),
        ...(row.vendor ? { vendor: row.vendor } : {}),
        ...(row.warn != null && row.warn !== 80 ? { warn_pct: row.warn } : {}),
      }),
  });
};

/** A budget as the list for `at` last read it, in any view. */
export function cachedBudget(qc: QueryClient, at: string, id: string): Budget | undefined {
  for (const [, d] of qc.getQueriesData<Budgets>({ queryKey: budgetsKey(at) })) {
    const b = d?.budgets.find((x) => x.id === id);
    if (b) return b;
  }
  return undefined;
}

// ---------------------------------------------------------------- decisions, sources and access (the old app's dialogs)

/** What a decision waiting for approval would leave in each budget it charges (cents). */
export interface Impact {
  budgets?: { name: string; remaining: number | null; remaining_after: number | null }[];
}
export type Decided = FinanceAction & { approver?: string | null; impact?: Impact };

/** A decision, tried first: the rules say whether it goes through or whom it waits for. Nothing is written. */
export const tryDecision = (kind: string, payload: object) => api.post<Decided>('/api/v2/finance/actions', { kind, payload, dry_run: true });

/* The Idempotency-Key of each decision whose answer has not come back: sent and waiting, or lost to the network. The
   same decision sent again within KEY_WINDOW_MS reuses it, so the server acts once (store.py keeps keys 48 hours). Any
   answer from the server, done or refused, ends the key: the next press is a new request. */
const KEY_WINDOW_MS = 10 * 60_000;
const unanswered = new Map<string, { key: string; at: number }>();

/** A Finance action sent under the key of its unanswered twin, if any (see above). */
async function post<T>(kind: string, payload: object): Promise<T> {
  const sig = JSON.stringify([kind, payload]);
  const now = Date.now();
  for (const [s, e] of unanswered) if (now - e.at > KEY_WINDOW_MS) unanswered.delete(s);
  const key = unanswered.get(sig)?.key ?? newId();
  unanswered.set(sig, { key, at: now });
  try {
    const out = await api.post<T>('/api/v2/finance/actions', { kind, payload }, { idempotencyKey: key });
    if (unanswered.get(sig)?.key === key) unanswered.delete(sig);
    return out;
  } catch (err) {
    if (!lostAnswer(err) && unanswered.get(sig)?.key === key) unanswered.delete(sig);
    throw err;
  }
}

/** No answer: the network dropped it, or a gateway's 502, 503 or 504. The server may have acted and its reply been lost. */
const lostAnswer = (err: unknown) => isNetworkError(err) || (err instanceof ApiError && err.status >= 502 && err.status <= 504);

/** A decision sent: done, or waiting for its approver (said by the caller). Anything else is refused and throws. */
export async function decide(kind: string, payload: object): Promise<Decided> {
  const out = await post<Decided>(kind, payload);
  void queryClient.invalidateQueries({ queryKey: ['finance'] });
  if (out.status === 'committed' || out.status === 'needs_approval') return out;
  throw Object.assign(new Error(out.reason || out.status), { code: out.code || out.status });
}

export const payFromQuery = queryOptions({
  queryKey: ['finance', 'pay-from'],
  queryFn: ({ signal }) => api.get<FinancePayFrom>('/api/v2/finance/pay-from', { signal }),
});

export const mercuryUsersQuery = queryOptions({
  queryKey: ['finance', 'mercury-users'],
  queryFn: ({ signal }) => orNull(api.get<FinanceMercuryUsers>('/api/v2/finance/mercury-users', { signal })),
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
  const idx = await api.get<{ rows?: { id: string; kind: string; name: string; service?: string | null; requestable?: boolean }[] }>('/api/v2/tool-index');
  // A tool listed by name alone (`requestable`) is not the reader's: its page is a 404.
  const rows = (idx.rows ?? []).filter((r) => r.kind === 'api' && !r.requestable && String(r.service ?? '').trim().toLowerCase().replace(/_/g, '-') === service);
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
    // A lost answer may have connected with the key: it stays on Tools rather than leave the source keyless.
    if (!lostAnswer(err)) await stored.undo();
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
  });

/** What a rule can match, by kind: vendors, P&L accounts, or the bank and card accounts. */
export const vendorsQuery = queryOptions({
  queryKey: ['finance', 'contacts', 'vendor'],
  queryFn: ({ signal }) => api.get<FinanceContacts>('/api/v2/finance/contacts?kind=vendor&limit=200', { signal }),
});
export const journalChartQuery = queryOptions({
  queryKey: ['finance', 'chart', 'journal'],
  queryFn: ({ signal }) => api.get<FinanceChart>('/api/v2/finance/chart?for=journal', { signal }),
});

// ---------------------------------------------------------------- Growth (dollars; rates are 0 to 1)

/* GET /api/v2/growth/read, typed from backend/growth_core.py `read` and its helpers (the schema leaves it untyped). A
   figure the read cannot give carries a status and a null value, never 0. */

/** Why a figure has no value, or (`estimate`) how far to trust it. */
export type GrowthStatus = 'ok' | 'too_few' | 'not_yet' | 'nobody' | 'not_supplied' | 'no_spend' | 'no_customers' | 'not_split' | 'estimate';
/** Against the trailing three months' average (flat within 10%); `too_few` under the minimum cohort. Null in a partial month. */
export type GrowthVerdict = 'up' | 'down' | 'flat' | 'too_few';

/** One headline figure. Which keys come depends on the figure: counts carry `vs_3mo`, rates `n`, `cohort` and `window`. */
export interface GrowthFig {
  value: number | null;
  status?: GrowthStatus;
  verdict?: GrowthVerdict | null;
  /** The people the figure rests on. */
  n?: number;
  /** The cohort month (YYYY-MM) a rate comes from. */
  cohort?: string;
  /** Kept: d30, d90 or d180; LTV: d90, d180 or d365. */
  window?: string;
  /** Counts: the trailing three months' average. */
  vs_3mo?: number | null;
  /** Cost and CAC with agent cost and hours added. */
  with_effort?: number | null;
  /** Cost only: spend on keeping customers, never in CAC. */
  retention?: number | null;
}

export interface GrowthHeadline {
  signups: GrowthFig;
  activated_rate: GrowthFig;
  paid_rate: GrowthFig;
  new_customers: GrowthFig;
  cost: GrowthFig;
  cac: GrowthFig;
  /** A segment's scope only: its own cost plus company-wide cost by its share of new customers. */
  cac_loaded?: GrowthFig;
  kept: GrowthFig;
  /** `value_kind: revenue` only. */
  ltv?: GrowthFig;
  ltv_cac?: GrowthFig;
  payback_months?: GrowthFig;
  /** `value_kind: retention` only, in place of LTV, LTV to CAC and payback. */
  value?: { kept_d30: GrowthFig; cost_per_retained: GrowthFig };
}

/** One channel in the month, ranked by the read (new customers, then sign-ups), Unknown last. Unknown (`slug:
    'unknown'`) carries only its counts and share. */
export interface GrowthChannel {
  slug: string;
  signups: number;
  /** Null when the scope's customers are not supplied for the month. */
  new_customers: number | null;
  new_share: number | null;
  name?: string;
  /** `bot:<id>` or `human:<id>`. */
  owner?: string | null;
  /** The channel's monthly budget, dollars. */
  budget?: number | null;
  over_budget?: number | null;
  cost?: number | null;
  cac?: number | null;
  cost_share?: number | null;
  status?: 'no_spend' | 'no_customers' | 'not_split';
  verdict?: GrowthVerdict | null;
  with_effort?: number | null;
}

/** An outside total (a KPI with a check role) against Growth's own company-wide figure; `agrees` null when the KPI has
    no reading for the month. */
export interface GrowthCheck {
  figure: 'signups' | 'new_customers' | 'cost';
  growth: number | null;
  outside: number | null;
  /** The KPI's name: the outside source in words ("Finance"). */
  kpi: string;
  agrees: boolean | null;
}

export interface GrowthHealth {
  /** What the shares count: customers when the company has any, else sign-ups. */
  basis: 'customers' | 'signups';
  /** The share with no known channel, 0 to 1. */
  unknown_share: number | null;
  /** With segments only. */
  no_segment?: number;
  mismatch?: number;
  stale_sources: string[];
  /** When each source last pushed (typed-by-hand rows have none), newest data date `as_of` and push time `pushed_at`. */
  sources?: { source: string; name: string; as_of: string; pushed_at?: string | null; stale: boolean }[];
  reconciled: 'reconciled' | 'off' | 'not_reconciled';
  /** Only when a check KPI is set up. */
  checks?: GrowthCheck[];
}

export interface GrowthPoint {
  period: string;
  signups: number | null;
  new_customers: number | null;
  cost: number | null;
  cac: number | null;
  kept: number | null;
}
/** A cohort's kept share at one window and how many were old enough to be judged; `not_yet` when none were. */
export type GrowthWin = { value: number | null; n: number; status: 'ok' | 'too_few' | 'not_yet' };
export interface GrowthCohort {
  cohort: string;
  entered: number | null;
  kept_d30?: GrowthWin;
  kept_d90?: GrowthWin;
  kept_d180?: GrowthWin;
}
/** A campaign's month: `none` is the channel's rows with no campaign, `other` the rest past ten. */
export interface GrowthCampaign {
  campaign: string;
  new_customers: number;
  cost: number | null;
  cac: number | null;
}
/** A KPI that belongs to Growth, with its latest reading up to the month's end and the one before it. */
export interface GrowthMetric {
  id: string;
  name: string;
  unit: string;
  owner: string | null;
  channel: string | null;
  value: number | null;
  period_end: string | null;
  estimate: boolean;
  vs_prior: number | null;
}

export interface GrowthRead {
  /** `all`, `mine`, `segment:<slug>` or `segment:none`, `channel:<slug>` or `channel:unknown`. */
  scope: string;
  period: string;
  as_of: string;
  /** A segment's scope only. */
  segment?: { slug: string; name: string; rule: string | null; value_kind: 'revenue' | 'retention' };
  headline: GrowthHeadline;
  channels: GrowthChannel[];
  /** The current, unfinished month: every verdict is null. */
  partial?: boolean;
  health: GrowthHealth;
  metrics: GrowthMetric[];
  /** `detail=campaigns`: by channel slug (`unknown`, or `company-wide` for cost with no channel). */
  campaigns?: Record<string, GrowthCampaign[]>;
  retention?: GrowthCohort[];
  series?: GrowthPoint[];
  /** The details not asked for. */
  more: string[];
}

export const growthScope = (segment?: string) => (segment ? `segment:${segment}` : 'all');

export const growthReadQuery = (segment: string | undefined, month: string | undefined, detail = true) =>
  queryOptions({
    queryKey: ['growth', 'read', growthScope(segment), month ?? 'last', detail],
    queryFn: ({ signal }) =>
      api.get<GrowthRead>(`/api/v2/growth/read?${qs({ scope: growthScope(segment), detail: detail ? 'series,retention' : undefined, period: month })}`, { signal }),
  });

/** One channel's own read for its side panel: its budget and spend, its months and its campaigns. */
export const growthChannelQuery = (channel: string, month: string | undefined) =>
  queryOptions({
    queryKey: ['growth', 'read', `channel:${channel}`, month ?? 'last', 'campaigns,series'],
    queryFn: ({ signal }) =>
      api.get<GrowthRead>(`/api/v2/growth/read?${qs({ scope: `channel:${channel}`, detail: 'campaigns,series', period: month })}`, { signal }),
  });

/** A message to the Growth Manager in its chat (POST /api/v2/chat/{bot}, backend/thread_api.py), sent from the page. */
export const askGrowthManager = (bot: string, text: string) =>
  api.post<unknown>(`/api/v2/chat/${enc(bot.replace(/^bot:/, ''))}`, { text, refs: { from_page: { page: 'growth', label: 'Growth' } } });

export interface Segment {
  slug: string;
  name: string;
  customer_rule?: string | null;
  channels?: string[];
}
export const segmentsQuery = queryOptions({
  queryKey: ['growth', 'segments'],
  queryFn: ({ signal }) => api.get<{ segments: Segment[] }>('/api/v2/growth/segments', { signal }),
});

export { pageOwnersQuery };

const botId = (ref: string) => (ref.includes(':') ? ref : `bot:${ref}`);

/** The Growth Manager: the agent that owns the Growth page, never the General Manager standing in for none
    (backend/growth_setup.py manager). `same` says whether two agent refs (id or slug) are one agent. */
export function growthManagerOf(row: PageOwnerRow | undefined, same: (a: string, b: string) => boolean): string | null {
  if (!row?.bot || row.choice === 'none') return null;
  const bot = botId(row.bot);
  if (row.choice && row.choice !== 'default' && same(botId(row.choice), bot)) return bot;
  return same(bot, 'bot:general-manager') ? null : bot;
}

/** Two refs to one agent, by the directory (an agent answers to its id and its slug). */
export const sameAgent = (dir: Directory | undefined) => (a: string, b: string) => a === b || (!!dir?.byId[a] && dir.byId[a] === dir.byId[b]);

/** The Growth Manager as the directory names it (`bot:<id>`, not a slug), or null when the company has none. */
export function growthManager(owners: Record<string, PageOwnerRow> | undefined, dir: Directory | undefined): string | null {
  const bot = growthManagerOf(owners?.growth, sameAgent(dir));
  return bot && (dir?.byId[bot]?.id ?? bot);
}

/** The Growth Manager, read from the server if not held yet. */
export async function findGrowthManager(qc: QueryClient): Promise<string | null> {
  const [owners, dir] = await Promise.all([qc.ensureQueryData(pageOwnersQuery), qc.ensureQueryData(directoryQuery).catch(() => undefined)]);
  return growthManager(owners, dir);
}

// ---------------------------------------------------------------- one bill, invoice, card or transaction (the books tabs' panels and forms)

export type DocKind = 'bill' | 'invoice';
export type Doc = FinanceDocument;
export type ChartLine = FinanceChart['accounts'][number];
const DOCS = { bill: 'bills', invoice: 'invoices' } as const;

/** Drafts: a new bill waits to be approved, a new invoice to be sent; the unpaid list leaves them out. */
export const draftsQuery = (kind: DocKind, as?: string) =>
  queryOptions({
    queryKey: ['finance', DOCS[kind], 'draft', ...asKey(as)],
    queryFn: async ({ signal }) => {
      const d = await api.get<{ bills?: DocRow[]; invoices?: DocRow[] }>(viewAs(`/api/v2/finance/${DOCS[kind]}?status=draft&limit=200`, as), { signal });
      return (kind === 'bill' ? d.bills : d.invoices) ?? [];
    },
  });

/** One bill or invoice with its lines, payments and approvals (GET /api/v2/finance/bills/{id}, /invoices/{id}). */
export const docQuery = (kind: DocKind, id: string, as?: string) =>
  queryOptions({
    queryKey: ['finance', DOCS[kind], 'one', id, ...asKey(as)],
    queryFn: ({ signal }) => api.get<Doc>(viewAs(`/api/v2/finance/${DOCS[kind]}/${enc(id)}`, as), { signal }),
  });

/** Customers, as vendorsQuery reads vendors. */
export const customersQuery = queryOptions({
  queryKey: ['finance', 'contacts', 'customer'],
  queryFn: ({ signal }) => api.get<FinanceContacts>('/api/v2/finance/contacts?kind=customer&limit=200', { signal }),
});

/** The lines the person may put a bill or an invoice on (GET /api/v2/finance/chart?for=bill|invoice). */
export const docChartQuery = (kind: DocKind) =>
  queryOptions({
    queryKey: ['finance', 'chart', kind],
    queryFn: ({ signal }) => api.get<FinanceChart>(`/api/v2/finance/chart?for=${kind}`, { signal }),
  });

/** A card as the cards list last read it, in any view. */
export function cachedCard(qc: QueryClient, id: string): Card | undefined {
  for (const [, d] of qc.getQueriesData<FinanceCards>({ queryKey: ['finance', 'cards'] })) {
    const c = d?.cards?.find((x) => x.id === id);
    if (c) return c;
  }
  return undefined;
}

/** A transaction as any transactions list last read it. */
export function cachedTx(qc: QueryClient, id: string): Tx | undefined {
  for (const [, d] of qc.getQueriesData<InfiniteData<FinanceTransactions>>({ queryKey: ['finance', 'transactions'] })) {
    for (const page of d?.pages ?? []) {
      const t = page.rows.find((x) => x.id === id);
      if (t) return t;
    }
  }
  return undefined;
}
