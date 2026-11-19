// Dates, ages and money, written one way everywhere. Ported from today's app (ui/app/format.ts, ui/app/finance.ts,
// ui/app/strategy-budget.ts): month first, a 12-hour clock, Today and Tomorrow for the date, the year only when it is
// not this year, always en-US whatever the browser's locale. Days are the viewer's own (local time).

export type When = Date | string;

const LOCALE = 'en-US';
const DAY_FORMATS = new Map<string, Intl.DateTimeFormat>();
const clockFormat = new Intl.DateTimeFormat(LOCALE, { hour: 'numeric', minute: '2-digit' });

function dayFormat(weekday: boolean, year: boolean): Intl.DateTimeFormat {
  const key = `${weekday ? 'w' : ''}${year ? 'y' : ''}`;
  let f = DAY_FORMATS.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat(LOCALE, { weekday: weekday ? 'short' : undefined, month: 'short', day: 'numeric', year: year ? 'numeric' : undefined });
    DAY_FORMATS.set(key, f);
  }
  return f;
}

const BARE_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A bare day ("2026-11-15", a due date with no zone) is that day in the viewer's zone, not UTC midnight. */
function dateOf(value: When): Date | null {
  if (typeof value === 'string') {
    const m = BARE_DAY.exec(value.trim());
    if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  }
  const t = new Date(value).getTime();
  return Number.isFinite(t) ? new Date(t) : null;
}

/** Whole calendar days from `now`'s day to `d`'s: 0 today, 1 tomorrow. Counted on the calendar, so clock changes do not matter. */
const dayOffset = (d: Date, now: Date) =>
  Math.round((Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86_400_000);

function dayWords(d: Date, now: Date, weekday: boolean): string {
  const off = dayOffset(d, now);
  if (off === 0) return 'Today';
  if (off === 1) return 'Tomorrow';
  return dayFormat(weekday, d.getFullYear() !== now.getFullYear()).format(d);
}

/** "Today", "Tomorrow", "Nov 6", "Nov 6, 2025"; '' for an unreadable value. */
export function fmtDate(value: When, now: Date = new Date()): string {
  const d = dateOf(value);
  return d ? dayWords(d, now, false) : '';
}

/** "Today, 10:15 AM", "Tue, Nov 11, 10:15 AM". */
function timeWords(d: Date, now: Date): string {
  return `${dayWords(d, now, true)}, ${clockFormat.format(d)}`;
}

/**
 * How long ago, under a day ("now", "35m", "2h"); after that the date ("Nov 6"). More than a minute ahead says when
 * ("Today, 3:00 PM"); a little ahead (a clock running fast) is "now". A bare day has no time to count from: it is its day.
 */
export function fmtAge(value: When, now: Date = new Date()): string {
  const d = dateOf(value);
  if (!d) return '';
  if (typeof value === 'string' && BARE_DAY.test(value.trim())) return dayWords(d, now, false);
  const s = (now.getTime() - d.getTime()) / 1000;
  if (s < -60) return timeWords(d, now);
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.min(59, Math.max(1, Math.round(s / 60)))}m`;
  if (s < 86_400) return `${Math.min(23, Math.max(1, Math.round(s / 3600)))}h`;
  return dayWords(d, now, false);
}

/**
 * Dollars: "$8,940", "−$12". With `cents`, cents show when there are any ("$5.70", "$80"); without, whole dollars.
 * A minus is the true minus sign; an amount that rounds to nothing has none.
 */
export function fmtMoney(amount: number, { cents = false }: { cents?: boolean } = {}): string {
  if (!Number.isFinite(amount)) return '—';
  const v = cents ? Math.round(Math.abs(amount) * 100) / 100 : Math.round(Math.abs(amount));
  const digits = cents && !Number.isInteger(v) ? 2 : 0;
  const text = v.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${amount < 0 && v > 0 ? '−' : ''}$${text}`;
}
