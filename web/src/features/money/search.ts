import { isYm, type Period } from './model';

/* What the Finance and Growth addresses hold. Defaults are left out, so a plain /company/finance is the overview. */

export const FINANCE_TABS = [
  ['overview', 'Overview'],
  // A teammate's own budgets: the one page of Finance for whoever sees only those (finance-budgets.ts:122).
  ['mine', 'My budget'],
  ['pnl', 'P&L'],
  ['transactions', 'Transactions'],
  ['budgets', 'Budgets'],
  ['payables', 'Payables'],
  ['receivables', 'Receivables'],
  ['balance', 'Balance sheet'],
  ['cards', 'Cards'],
  ['tax', 'Tax'],
  ['attribution', 'Attribution'],
  ['sources', 'Sources'],
  ['access', 'Access'],
] as const;
export type FinanceTab = (typeof FINANCE_TABS)[number][0];
export const FINANCE_TAB_IDS = FINANCE_TABS.map(([id]) => id) as unknown as readonly [FinanceTab, ...FinanceTab[]];

export interface FinanceSearch {
  tab?: Exclude<FinanceTab, 'overview'>;
  /** `line:<account>` or `budget:<id>`. */
  panel?: string;
  /** The P&L's columns, and the span Budgets reads. */
  period?: Exclude<Period, 'month'>;
  basis?: 'cash';
  /** The statement's first month, YYYY-MM. */
  from?: string;
  /** The owner reads Finance as this teammate (`human:…` or `bot:…`), read only. */
  as?: string;
  /** A QuickBooks or Xero sign-in's return (backend/finance_routes.py); `other` is a value we do not know, only cleaned. */
  books?: 'connected' | 'error' | 'other';
  /** Why the books did not connect: a known code, anything else `unknown`. */
  reason?: BooksReason;
  /** The balance sheet as of the last month end or year end; today when left out. */
  asof?: 'month' | 'year';
  /** Transactions from the bank feeds or the books' own entries only. */
  src?: 'feed' | 'entry';
  /** Transactions in one status. */
  status?: TxStatus;
  /** Transactions on one P&L or balance sheet line (its account id). */
  line?: string;
}

/** A transaction's status, as GET /api/v2/finance/transactions filters it. */
export const TX_STATUSES = ['new', 'matched', 'posted', 'pending', 'ignored', 'failed'] as const;
export type TxStatus = (typeof TX_STATUSES)[number];

/** The codes a books return can carry (backend/finance/books_link.py and HQ's consent answers). */
export const BOOKS_REASONS = [
  'access_denied',
  'already_connected',
  'connected_elsewhere',
  'reconnect_required',
  'not_connected',
  'no_refresh_token',
  'access_expired',
  'vault_write_failed',
  'gateway_refused',
  'bad_answer',
  'superseded',
  'unknown',
] as const;
export type BooksReason = (typeof BOOKS_REASONS)[number];

export interface GrowthSearch {
  segment?: string;
  /** YYYY-MM; the last full month when left out. */
  month?: string;
  /** `channel:<slug>`. */
  panel?: string;
}

const text = (v: unknown, max = 200) => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);
const PANEL = /^(line|budget|channel):\S+$/;
/** A bill, an invoice, a card or a transaction open beside its list. */
const BOOK_PANEL = /^(bill|invoice|card|tx):\S+$/;
/** A source on Sources, a teammate on Access (`mate:human:<id>`). */
const ADMIN_PANEL = /^(source|mate):\S+$/;
const GROWTH_PANEL = /^channel:\S+$/;

/** `?tab=pnl&period=quarter&basis=cash&from=2026-07&panel=line:<account>&as=human:<id>`; anything else is dropped. */
export function validateFinanceSearch(raw: Record<string, unknown>): FinanceSearch {
  const out: FinanceSearch = {};
  const tab = FINANCE_TAB_IDS.find((t) => t === raw.tab);
  if (tab && tab !== 'overview') out.tab = tab;
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
  if (panel && BOOK_PANEL.test(panel)) out.panel = panel;
  if (panel && ADMIN_PANEL.test(panel)) out.panel = panel;
  if (raw.asof === 'month' || raw.asof === 'year') out.asof = raw.asof;
  if (raw.src === 'feed' || raw.src === 'entry') out.src = raw.src;
  const status = TX_STATUSES.find((v) => v === raw.status);
  if (status) out.status = status;
  const line = text(raw.line, 80);
  if (line && /^[\w-]+$/.test(line)) out.line = line;
  if (raw.period === 'quarter' || raw.period === 'year') out.period = raw.period;
  if (raw.basis === 'cash') out.basis = 'cash';
  if (isYm(raw.from)) out.from = raw.from;
  const as = text(raw.as);
  if (as && /^(human|bot):\S+$/.test(as)) out.as = as;
  // Anyone can write these into an address: only known words pass, and anything present is kept so the page cleans it.
  if (raw.books !== undefined) out.books = raw.books === 'connected' || raw.books === 'error' ? raw.books : 'other';
  if (raw.reason !== undefined) out.reason = BOOKS_REASONS.find((r) => r === raw.reason) ?? 'unknown';
  return out;
}

/** `?segment=<slug>&month=2026-10&panel=channel:<slug>`. The Sources tab is gone: its old addresses land on the board. */
export function validateGrowthSearch(raw: Record<string, unknown>): GrowthSearch {
  const out: GrowthSearch = {};
  const segment = text(raw.segment, 80);
  if (segment && /^[\w-]+$/.test(segment)) out.segment = segment;
  if (isYm(raw.month)) out.month = raw.month;
  const panel = text(raw.panel);
  if (panel && GROWTH_PANEL.test(panel)) out.panel = panel;
  return out;
}

/** The panel's kind and id: `line:acc_1` is ['line', 'acc_1']. */
export const panelOf = (panel: string | undefined): [string, string] | null => {
  if (!panel) return null;
  const i = panel.indexOf(':');
  return i > 0 ? [panel.slice(0, i), panel.slice(i + 1)] : null;
};
