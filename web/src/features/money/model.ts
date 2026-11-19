import { fmtMoney } from '@/lib';
import type { Budget, GrowthRead, Pnl, PnlCol, PnlRow, SourceGroup } from './queries';

/* The pure parts of Finance and Growth: dates and ranges, the statement, budgets by section, the funnel. Finance
   money is cents; Growth's is dollars. */

// ---------------------------------------------------------------- money and numbers

/** Cents as the page writes them; empty for no figure. */
export const cash = (c: number | null | undefined, cents = false) => (c == null ? '' : fmtMoney(c / 100, { cents }));
export const count = (n: number | null | undefined) => (n == null ? '' : Math.round(n).toLocaleString('en-US'));
export const pct = (v: number | null | undefined, digits = 0) => (v == null ? '' : `${(v * 100).toFixed(digits)}%`);
export const ratio = (v: number | null | undefined) => (v == null ? '' : v.toFixed(1));

// ---------------------------------------------------------------- dates

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTH = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const pad = (n: number) => String(n).padStart(2, '0');
export const ymOf = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
export const dayOf = (d: Date) => `${ymOf(d)}-${pad(d.getDate())}`;
const parts = (ym: string) => ym.split('-').map(Number) as [number, number];
export const addMonths = (ym: string, n: number) => {
  const [y, m] = parts(ym);
  const t = y * 12 + (m - 1) + n;
  return `${Math.floor(t / 12)}-${pad((t % 12) + 1)}`;
};
const lastDay = (ym: string) => {
  const [y, m] = parts(ym);
  return `${ym}-${pad(new Date(y, m, 0).getDate())}`;
};
export const monShort = (ym: string) => MON[parts(ym)[1] - 1] ?? ym;
export const monthName = (ym: string) => `${MONTH[parts(ym)[1] - 1] ?? ''} ${parts(ym)[0]}`.trim();
export const isYm = (v: unknown): v is string => typeof v === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(v);

export type Period = 'month' | 'quarter' | 'year';
const UNIT: Record<Period, number> = { month: 1, quarter: 3, year: 12 };
const SPAN: Record<Period, number> = { month: 6, quarter: 4, year: 3 };

/** The first month of the period holding `ym`. */
const align = (period: Period, ym: string) => {
  const [y, m] = parts(ym);
  if (period === 'year') return `${y}-01`;
  if (period === 'quarter') return `${y}-${pad(m - ((m - 1) % 3))}`;
  return ym;
};

/** The statement's columns: six months with the current one fourth, four quarters, or three years, ending in plans. */
export function pnlRange(period: Period, from: string | undefined, now = new Date()) {
  const here = align(period, ymOf(now));
  const back = period === 'month' ? 3 : period === 'quarter' ? 2 : 1;
  const first = from ? align(period, from) : addMonths(here, -back * UNIT[period]);
  const last = addMonths(first, UNIT[period] * SPAN[period] - 1);
  return { from: first, start: `${first}-01`, end: lastDay(last) };
}

/** One step earlier (-1) or later (+1): the window moves by one period. */
export const stepRange = (period: Period, from: string | undefined, dir: -1 | 1, now = new Date()) =>
  addMonths(pnlRange(period, from, now).from, dir * UNIT[period]);

/** "Aug to Dec 2026", "Aug 2026 to Jan 2027". */
export function rangeLabel(start: string, end: string) {
  const a = start.slice(0, 7);
  const b = end.slice(0, 7);
  return a.slice(0, 4) === b.slice(0, 4) ? `${monShort(a)} to ${monShort(b)} ${b.slice(0, 4)}` : `${monShort(a)} ${a.slice(0, 4)} to ${monShort(b)} ${b.slice(0, 4)}`;
}

/** A column's head: "Oct", "Nov to date", "Dec plan". */
export function colLabel(c: PnlCol, period: string) {
  const base = period === 'month' ? monShort(c.start.slice(0, 7)) : c.label;
  return c.state === 'current' ? `${base} to date` : c.state === 'future' ? `${base} plan` : base;
}

/** The day budgets are read for; the page and every budget change share it, so a change lands in the list shown. */
export const budgetDay = (now = new Date()) => dayOf(now);

/** This month's first and last day. */
export const monthSpan = (ym: string) => ({ start: `${ym}-01`, end: lastDay(ym) });

/** A chart's scale: round steps covering the values, three or four gridlines. `whole` for counts: steps of whole numbers,
    at least 1, so no two gridlines print the same label ("0, 1, 1" from steps of 0.5 when every month is 0). */
export function axis(values: readonly number[], whole = false) {
  const lo = Math.min(...values);
  let hi = Math.max(...values);
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { min: 0, max: 1, ticks: [0, 1] };
  if (hi === lo) hi = lo + 1;
  const raw = (hi - lo) / 3;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const nice = whole ? [1, 2, 5, 10] : [1, 2, 2.5, 5, 10];
  const fit = (nice.find((m) => m * mag >= raw) ?? 10) * mag;
  const step = whole ? Math.max(1, Math.round(fit)) : fit;
  const min = Math.floor(lo / step) * step;
  const max = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  for (let t = min; t <= max + step / 2; t += step) ticks.push(Math.round(t * 1e6) / 1e6);
  return { min, max, ticks };
}

/** Where today is in its month, 0 to 100, and its words ("Day 18 of 30"). */
export function paceOf(now = new Date()) {
  const days = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();
  return { pace: (now.getDate() / days) * 100, words: `Day ${now.getDate()} of ${days}` };
}

// ---------------------------------------------------------------- the statement

export type StatementRow =
  | { kind: 'group'; name: string }
  | { kind: 'line'; id: string; name: string; values: (number | null)[]; depth: number; locked: boolean }
  | { kind: 'total' | 'net'; name: string; values: (number | null)[] }
  | { kind: 'pct'; name: string; values: (number | null)[] };

/** A line's figure in a column: the actual for past and current periods, the projection for future ones. */
export const valueAt = (r: PnlRow, cols: readonly PnlCol[], i: number) =>
  cols[i]?.state === 'future' ? (r.projection?.[i] ?? null) : (r.actual?.[i] ?? null);

/** Sums the lines shown (a private line's figure is left out); null when none has a figure. */
function sum<R>(rows: readonly R[], pick: (r: R) => number | null) {
  let any = false;
  let total = 0;
  for (const r of rows) {
    const v = pick(r);
    if (v != null) {
      any = true;
      total += v;
    }
  }
  return any ? total : null;
}
const side = <R>(rows: readonly R[], pick: (r: R) => number | null) => (rows.length ? sum(rows, pick) : 0);
const less = (a: number | null, b: number | null) => (a == null || b == null ? null : a - b);

interface Sec {
  top: PnlRow;
  cogs: boolean;
  lines: (PnlRow & { depth: number })[];
}

/** Sections from the chart (ui/app/finance-pnl.ts): each top line with the lines under it in code order, income first;
    gross profit and margin after the last cost of revenue section; net income last. */
export function statement(p: Pnl): StatementRow[] {
  const rows = p.rows;
  const cols = p.columns;
  const ids = new Set(rows.map((r) => r.account_id));
  const kids = new Map<string, PnlRow[]>();
  for (const r of rows) if (r.parent_id && ids.has(r.parent_id)) kids.set(r.parent_id, [...(kids.get(r.parent_id) ?? []), r]);
  const byCode = (a: PnlRow, b: PnlRow) => String(a.code).localeCompare(String(b.code));
  const own = (r: PnlRow) => r.private || [r.actual, r.budget, r.projection].some((v) => (v ?? []).some((x) => x));
  const tops = rows
    .filter((r) => !r.parent_id || !ids.has(r.parent_id))
    .sort((a, b) => (a.type === 'income' ? 0 : 1) - (b.type === 'income' ? 0 : 1) || byCode(a, b));
  const secs: Sec[] = tops.map((top) => {
    const sec: Sec = { top, cogs: top.section === 'cost_of_revenue', lines: [] };
    const walk = (r: PnlRow, depth: number): void => {
      sec.lines.push({ ...r, depth });
      for (const k of [...(kids.get(r.account_id) ?? [])].sort(byCode)) walk(k, depth + 1);
    };
    const under = [...(kids.get(top.account_id) ?? [])].sort(byCode);
    if (!under.length || own(top)) sec.lines.push({ ...top, depth: 0 });
    for (const k of under) walk(k, 0);
    return sec;
  });
  const fig = (i: number) => (r: PnlRow) => (r.private ? null : valueAt(r, cols, i));
  const each = (f: (i: number) => number | null) => cols.map((_, i) => f(i));
  const income = secs.filter((s) => s.top.type === 'income').flatMap((s) => s.lines);
  const cogs = secs.filter((s) => s.cogs).flatMap((s) => s.lines);
  const expense = secs.flatMap((s) => s.lines).filter((r) => r.type === 'expense');
  const out: StatementRow[] = [];
  secs.forEach((s, n) => {
    out.push({ kind: 'group', name: s.top.name });
    for (const r of s.lines) out.push({ kind: 'line', id: r.account_id, name: r.name, values: each((i) => valueAt(r, cols, i)), depth: r.depth, locked: r.private });
    out.push({ kind: 'total', name: `Total ${s.top.name.toLowerCase()}`, values: each((i) => sum(s.lines, fig(i))) });
    if (s.cogs && !secs.slice(n + 1).some((x) => x.cogs) && income.length) {
      const gross = each((i) => less(side(income, fig(i)), side(cogs, fig(i))));
      out.push({ kind: 'total', name: 'Gross profit', values: gross });
      out.push({
        kind: 'pct',
        name: 'Margin',
        values: each((i) => {
          const rev = sum(income, fig(i));
          const g = gross[i] ?? null;
          return rev && g != null ? Math.round((g / rev) * 1000) / 10 : null;
        }),
      });
    }
  });
  if (secs.length)
    out.push({
      kind: 'net',
      name: 'Net income',
      values: each((i) => (cols[i]?.state === 'future' ? less(side(income, fig(i)), side(expense, fig(i))) : (p.net[i] ?? null))),
    });
  return out;
}

/** The column for today, or the last one with figures. */
export const nowCol = (cols: readonly PnlCol[]) => {
  const i = cols.findIndex((c) => c.state === 'current');
  return i >= 0 ? i : cols.filter((c) => c.state !== 'future').length - 1;
};

/** Connected means a feed from outside: Tico's own usage and what is entered by hand do not count. */
export const connected = (groups: readonly SourceGroup[] | undefined) =>
  !!groups?.some((g) => g.group !== 'tico' && g.group !== 'manual' && g.rows.length > 0);

// ---------------------------------------------------------------- budgets

const SECTION_NAMES: Record<string, string> = {
  revenue: 'Revenue',
  cost_of_revenue: 'Cost of revenue',
  operating: 'Operating expenses',
  other: 'Other',
};
const SECTION_ORDER = ['revenue', 'cost_of_revenue', 'operating', 'other'];

export interface BudgetLine {
  budget: Budget;
  depth: number;
  spent: number | null;
  amount: number | null;
  /** "Over" or "Ahead of pace", or nothing. */
  flag: string;
}
export interface BudgetGroup {
  name: string;
  rows: BudgetLine[];
}

/** Budgets grouped by the P&L section of their line, a budget's children under it; and the total of the top rows. */
export function budgetGroups(budgets: readonly Budget[], lines: readonly PnlRow[], pace: number) {
  const live = budgets.filter((b) => b.active !== false && !b.catch_all);
  const ids = new Set(live.map((b) => b.id));
  const section = new Map(lines.map((r) => [r.account_id, r.section]));
  const kids = new Map<string, Budget[]>();
  for (const b of live) if (b.parent_id && ids.has(b.parent_id)) kids.set(b.parent_id, [...(kids.get(b.parent_id) ?? []), b]);
  const line = (b: Budget, depth: number): BudgetLine => {
    const spent = b.state.actual ?? null;
    const amount = b.state.amount ?? null;
    const used = spent != null && amount ? (spent / amount) * 100 : null;
    const flag = used == null ? '' : used > 100 ? 'Over' : used > pace + 15 ? 'Ahead of pace' : '';
    return { budget: b, depth, spent, amount, flag };
  };
  const groups = new Map<string, BudgetLine[]>();
  const tops = live.filter((b) => !b.parent_id || !ids.has(b.parent_id)).sort((a, b) => a.name.localeCompare(b.name));
  for (const b of tops) {
    const key = (b.account_id && section.get(b.account_id)) || 'other';
    const list = groups.get(key) ?? [];
    list.push(line(b, 0));
    for (const k of (kids.get(b.id) ?? []).sort((x, y) => x.name.localeCompare(y.name))) list.push(line(k, 1));
    groups.set(key, list);
  }
  const ordered: BudgetGroup[] = SECTION_ORDER.filter((k) => groups.has(k)).map((k) => ({ name: SECTION_NAMES[k] ?? k, rows: groups.get(k) ?? [] }));
  const top = ordered.flatMap((g) => g.rows.filter((r) => r.depth === 0));
  return { groups: ordered, spent: sum(top, (r) => r.spent), amount: sum(top, (r) => r.amount) };
}

/** Whole dollars or dollars and cents typed in a field, as cents; null when it is not an amount. */
export function centsOf(text: string): number | null {
  const t = text.replace(/[$,\s]/g, '');
  if (!/^\d+(\.\d{1,2})?$/.test(t)) return null;
  return Math.round(Number(t) * 100);
}

// ---------------------------------------------------------------- Growth

/** The funnel from the read's headline: sign-ups, then the shares that activated and paid, then new customers. */
export function funnelSteps(h: Partial<GrowthRead['headline']>) {
  const signups = h.signups?.value ?? null;
  const of = (rate: GrowthRead['headline']['activated_rate'] | undefined) =>
    rate?.value == null || signups == null ? null : Math.round(rate.value * (rate.n ?? signups));
  const steps: [string, number | null][] = [
    ['Sign-ups', signups],
    ['Activated', of(h.activated_rate)],
    ['Paid', of(h.paid_rate)],
    ['New customers', h.new_customers?.value ?? null],
  ];
  return steps.filter((s): s is [string, number] => s[1] != null).map(([name, n]) => ({ name, count: n }));
}

/** The month the page opens on: the last full one. */
export const lastFullMonth = (now = new Date()) => addMonths(ymOf(now), -1);

/** The current (partial) month, then twelve full months back, newest first, plus the month picked. */
export function growthMonths(picked: string | undefined, now = new Date()) {
  const list = Array.from({ length: 13 }, (_, k) => addMonths(ymOf(now), -k));
  return [...new Set([...(picked ? [picked] : []), ...list])].sort().reverse();
}

/** Revenue or retention: a segment says; on All the read gives a retention headline (`value`) only when every segment
    is a retention one. */
export const valueKind = (r: Pick<GrowthRead, 'segment' | 'headline'>): 'revenue' | 'retention' =>
  r.segment?.value_kind ?? (r.headline.value ? 'retention' : 'revenue');

/** When a cohort's kept share at a window can first be read: once its whole month is old enough ("Nov 30"). */
export function firstRead(cohort: string, days: number) {
  const [y, m] = parts(cohort);
  const d = new Date(y, m, 0);
  d.setDate(d.getDate() + days);
  return `${MON[d.getMonth()]} ${d.getDate()}`;
}

const FIGURE_WORD = { signups: 'Sign-ups', new_customers: 'New customers', cost: 'Cost' } as const;

/** The trust lines worth saying out loud: a big unknown share, a check that disagrees, stale sources. */
export function loudHealth(h: GrowthRead['health'] | undefined): string[] {
  if (!h) return [];
  const out: string[] = [];
  if (h.unknown_share != null && h.unknown_share >= 0.2)
    out.push(`${pct(h.unknown_share)} of ${h.basis === 'signups' ? 'sign-ups' : 'new customers'} have no known channel`);
  for (const c of h.checks ?? []) {
    if (c.agrees !== false) continue;
    out.push(`${FIGURE_WORD[c.figure]} is off from ${c.kpi}: ${count(c.growth)} here, ${count(c.outside)} there`);
  }
  if (h.stale_sources.length) out.push(`Stale: ${h.stale_sources.join(', ')}`);
  return out;
}

/** "too few", "not supplied": why a figure is missing, in a muted word. */
export const statusWord = (s: string | undefined) =>
  ({ too_few: 'too few', not_supplied: 'not supplied', no_spend: 'no spend', not_yet: 'not yet', nobody: 'nobody', no_customers: 'no customers', not_split: 'not split', partial: 'partial', estimate: 'estimate' })[
    s ?? ''
  ] ?? '';
