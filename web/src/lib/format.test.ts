import { describe, expect, it } from 'vitest';
import { fmtAge, fmtDate, fmtMoney, fmtWhen } from './format';

// Local times, so the cases hold in any time zone. "now" is Thu, Nov 19, 2026, 10:00 AM.
const now = new Date(2026, 10, 19, 10, 0, 0);
const at = (...p: [number, number, number, number?, number?, number?]) => new Date(p[0], p[1], p[2], p[3] ?? 0, p[4] ?? 0, p[5] ?? 0);
// The clock's space before AM/PM is a narrow no-break space in current ICU, as it was in the old app.
const plain = (s: string) => s.replace(/\s/g, ' ');

describe('fmtDate (ui/app/format.ts:34 dayWords)', () => {
  it.each([
    ['today', at(2026, 10, 19, 23, 59), 'Today'], // format.ts:38
    ['tomorrow', at(2026, 10, 20, 0, 1), 'Tomorrow'], // format.ts:39
    ['yesterday is a date', at(2026, 10, 18, 9), 'Nov 18'], // format.ts:40
    ['this year: no year', at(2026, 0, 6), 'Jan 6'], // format.ts:40
    ['another year: the year', at(2025, 10, 6), 'Nov 6, 2025'], // format.ts:40
    ['a bare day is local', '2026-11-20', 'Tomorrow'], // format.ts:18-22, 203
    ['unreadable', 'soon', ''], // format.ts:36
  ] as const)('%s', (_, value, want) => {
    expect(fmtDate(value, now)).toBe(want);
  });
});

describe('fmtWhen', () => {
  it.each([
    ['a weekday, the date and the time', at(2026, 10, 10, 10, 15), 'Tue, Nov 10, 10:15 AM'],
    ['today', at(2026, 10, 19, 15), 'Today, 3:00 PM'],
    ['another year', at(2025, 10, 6, 9), 'Thu, Nov 6, 2025, 9:00 AM'],
    ['unreadable', 'soon', ''],
  ] as const)('%s', (_, value, want) => {
    expect(plain(fmtWhen(value, now))).toBe(want);
  });
});

describe('fmtAge (ui/app/format.ts:60 ago)', () => {
  it.each([
    ['under a minute', at(2026, 10, 19, 9, 59, 30), 'now'], // format.ts:66
    ['a clock running fast', at(2026, 10, 19, 10, 0, 50), 'now'], // format.ts:65-66
    ['minutes', at(2026, 10, 19, 9, 25), '35m'], // format.ts:67
    ['59.9 minutes stays minutes', at(2026, 10, 19, 9, 0, 5), '59m'], // format.ts:67 (clamped)
    ['hours', at(2026, 10, 19, 8, 0), '2h'], // format.ts:68
    ['a day or more: the date', at(2026, 10, 17, 10, 0), 'Nov 17'], // format.ts:69
    ['a bare day is its day', '2026-11-19', 'Today'], // format.ts:62
    ['unreadable', 'soon', ''], // format.ts:64
  ] as const)('%s', (_, value, want) => {
    expect(fmtAge(value, now)).toBe(want);
  });

  it('says when, for a time ahead (format.ts:65, 56)', () => {
    expect(plain(fmtAge(at(2026, 10, 19, 15, 0), now))).toBe('Today, 3:00 PM');
    expect(plain(fmtAge(at(2026, 10, 24, 9, 30), now))).toBe('Tue, Nov 24, 9:30 AM');
  });
});

describe('fmtMoney', () => {
  it.each([
    ['whole dollars (ui/app/dashboard.ts:107)', 1240.5, false, '$1,241'],
    ['thousands', 8940, false, '$8,940'],
    ['negative: true minus (ui/app/finance.ts:167)', -12, false, '−$12'],
    ['rounds to nothing: no sign', -0.3, false, '$0'],
    ['cents when there are any (ui/app/strategy-budget.ts:92)', 27.38, true, '$27.38'],
    ['no cents when there are none (strategy-budget.ts:94)', 80, true, '$80'],
    ['a half dollar keeps two digits (finance.ts:167)', 5.7, true, '$5.70'],
    ['negative with cents', -1920.5, true, '−$1,920.50'],
    ['not a number (finance.ts:165)', Number.NaN, false, '—'],
  ] as const)('%s', (_, amount, cents, want) => {
    expect(fmtMoney(amount, { cents })).toBe(want);
  });
});
