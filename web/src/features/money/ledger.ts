import type { FinanceBalanceSheet } from '@/api/schema';
import { dayOf } from './model';

/* What the books tabs' actions and forms share: the balance sheet's days and its CSV, and the payload each decision
   sends (backend/finance/documents.py, handlers.py, cards.py), so the dry run and the send are the same request. */

/** Dollars as the server's cents. */
export const toCents = (dollars: number) => Math.round(dollars * 100);

/** Who a line not company-wide is for: `{teammate: 'human:…'}` or `{team: '…'}`. */
export type Tags = Record<string, string>;

// ---------------------------------------------------------------- the balance sheet's days

export type AsOf = 'today' | 'month' | 'year';

/** The day the balance sheet is read at: the viewer's today, the last month end or year end. Today is sent as the
    viewer's own day: the server's default is its own (UTC) day, which in the evening west of UTC is tomorrow. */
export function asOfDay(asof: AsOf, now = new Date()): string {
  if (asof === 'month') return dayOf(new Date(now.getFullYear(), now.getMonth(), 0));
  if (asof === 'year') return `${now.getFullYear() - 1}-12-31`;
  return dayOf(now);
}

/** The day it is set beside: the month end or year end before the one shown (finance.js `bal`: Oct 31 beside today). */
export function compareDay(asof: AsOf, now = new Date()): string {
  if (asof === 'month') return dayOf(new Date(now.getFullYear(), now.getMonth() - 1, 0));
  if (asof === 'year') return `${now.getFullYear() - 2}-12-31`;
  return dayOf(new Date(now.getFullYear(), now.getMonth(), 0));
}

type Acct = FinanceBalanceSheet['assets'][number];
/** One account across two reads: by its id, else its code and name. */
export const acctKey = (a: Pick<Acct, 'account_id' | 'code' | 'name'>) => a.account_id ?? `${a.code}:${a.name}`;

const cell = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
const dollars = (c: number | undefined) => (c == null ? '' : (c / 100).toFixed(2));

/** The balance sheet as a CSV: each account under its side, the earlier day's balance beside this one's. */
export function balanceCsv(now: FinanceBalanceSheet, was?: FinanceBalanceSheet): string {
  const before = new Map<string, number>();
  for (const a of was ? [...was.assets, ...was.liabilities, ...was.equity] : []) before.set(acctKey(a), a.balance);
  const head = ['Section', 'Code', 'Account', ...(was ? [was.as_of] : []), now.as_of];
  const rows = (
    [
      ['Assets', now.assets],
      ['Liabilities', now.liabilities],
      ['Equity', now.equity],
    ] as const
  ).flatMap(([side, list]) => list.map((a) => [side, a.code, a.name, ...(was ? [dollars(before.get(acctKey(a)))] : []), dollars(a.balance)]));
  return [head, ...rows].map((r) => r.map((v) => cell(String(v))).join(',')).join('\n') + '\n';
}

/** Gives the person a file, from their browser. */
export function download(name: string, text: string, type = 'text/csv') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------- what each decision sends

const opt = (key: string, v: string | undefined) => (v?.trim() ? { [key]: v.trim() } : {});

export interface BillIn {
  vendor: string;
  date: string;
  due?: string;
  number?: string;
  memo?: string;
  line: string;
  amount: number;
  tags?: Tags;
}
/** A new bill is a draft (bill_create); `bill_approve` enters it. */
export const billPayload = (b: BillIn) => ({
  vendor_id: b.vendor,
  bill_date: b.date,
  lines: [{ account_id: b.line, amount: toCents(b.amount), ...(b.tags ? { tags: b.tags } : {}) }],
  ...opt('number', b.number),
  ...opt('due_date', b.due),
  ...opt('memo', b.memo),
  origin: 'manual',
});

export interface InvoiceLineIn {
  line: string;
  amount: number;
  taxable?: boolean;
  tags?: Tags;
}
export interface InvoiceIn {
  customer: string;
  date: string;
  due?: string;
  number?: string;
  memo?: string;
  lines: InvoiceLineIn[];
}
/** A new invoice is a draft (invoice_create); `invoice_send` sends it. */
export const invoicePayload = (v: InvoiceIn) => ({
  customer_id: v.customer,
  invoice_date: v.date,
  lines: v.lines.map((l) => ({ account_id: l.line, amount: toCents(l.amount), ...(l.taxable != null ? { taxable: l.taxable } : {}), ...(l.tags ? { tags: l.tags } : {}) })),
  ...opt('number', v.number),
  ...opt('due_date', v.due),
  ...opt('memo', v.memo),
});

export interface EntryLineIn {
  line: string;
  /** Dollars: a debit is more than 0, a credit less. */
  amount: number;
  tags?: Tags;
}
/** A journal entry (handlers.py `journal`): two lines or more whose debits and credits are equal. */
export const entryPayload = (e: { date: string; memo?: string; lines: EntryLineIn[] }) => ({
  date: e.date,
  ...opt('memo', e.memo),
  lines: e.lines.map((l) => ({ account_id: l.line, amount: toCents(l.amount), ...(l.tags ? { tags: l.tags } : {}) })),
});
export const balanced = (lines: readonly EntryLineIn[]) => lines.length >= 2 && lines.every((l) => l.amount !== 0) && lines.reduce((a, l) => a + toCents(l.amount), 0) === 0;

/** Money paid on a bill or received on an invoice: from (to) a bank account, or elsewhere when none is named. */
export const paidPayload = (p: { kind: 'bill' | 'invoice'; doc: string; amount: number; date?: string; account?: string }) => ({
  [`${p.kind}_id`]: p.doc,
  amount: toCents(p.amount),
  ...opt('date', p.date),
  ...opt('money_account_id', p.account),
});
