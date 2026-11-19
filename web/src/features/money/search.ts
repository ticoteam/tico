import { isYm, type Period } from './model';

/* What the Finance and Growth addresses hold. Defaults are left out, so a plain /company/finance is the overview. */

export const FINANCE_TABS = [
  ['overview', 'Overview'],
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
  /** `line:<account>`, `budget:<id>` or `budget:new`. */
  panel?: string;
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
}

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
  tab?: 'sources';
  segment?: string;
  /** YYYY-MM; the last full month when left out. */
  month?: string;
  /** `channel:<slug>`. */
  panel?: string;
}

const text = (v: unknown, max = 200) => (typeof v === 'string' && v.trim() ? v.trim().slice(0, max) : undefined);
const PANEL = /^(line|budget|channel):\S+$/;

/** `?tab=pnl&period=quarter&basis=cash&from=2026-07&panel=line:<account>&as=human:<id>`; anything else is dropped. */
export function validateFinanceSearch(raw: Record<string, unknown>): FinanceSearch {
  const out: FinanceSearch = {};
  const tab = FINANCE_TAB_IDS.find((t) => t === raw.tab);
  if (tab && tab !== 'overview') out.tab = tab;
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
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

/** `?segment=<slug>&month=2026-10&panel=channel:<slug>`, or `?tab=sources`. */
export function validateGrowthSearch(raw: Record<string, unknown>): GrowthSearch {
  const out: GrowthSearch = {};
  if (raw.tab === 'sources') out.tab = 'sources';
  const segment = text(raw.segment, 80);
  if (segment && /^[\w-]+$/.test(segment)) out.segment = segment;
  if (isYm(raw.month)) out.month = raw.month;
  const panel = text(raw.panel);
  if (panel && PANEL.test(panel)) out.panel = panel;
  return out;
}

/** The panel's kind and id: `line:acc_1` is ['line', 'acc_1']. */
export const panelOf = (panel: string | undefined): [string, string] | null => {
  if (!panel) return null;
  const i = panel.indexOf(':');
  return i > 0 ? [panel.slice(0, i), panel.slice(i + 1)] : null;
};
