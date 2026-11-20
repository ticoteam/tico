import { describe, expect, it } from 'vitest';
import { axis, budgetGroups, centsOf, count, funnelSteps, loudHealth, pnlRange, stepRange } from './model';
import { readState } from './parts';
import type { Budget } from './queries';
import { panelOf, validateFinanceSearch } from './search';

// Made-up figures only.
const NOW = new Date(2026, 10, 18);

describe('money model', () => {
  it('a month range is six months with this one fourth, and steps a period at a time', () => {
    expect(pnlRange('month', undefined, NOW)).toEqual({ from: '2026-08', start: '2026-08-01', end: '2027-01-31' });
    expect(stepRange('month', '2026-08', 1)).toBe('2026-09');
    expect(stepRange('month', '2026-08', -1)).toBe('2026-07');
  });

  it('reads typed amounts as cents and refuses what is not one', () => {
    expect(centsOf('$1,250.5')).toBe(125050);
    expect(centsOf('12a')).toBeNull();
  });

  it('flags a budget over, or well ahead of where the month is', () => {
    const b = (id: string, amount: number, actual: number): Budget => ({ id, name: id, account_id: null, parent_id: null, period: 'month', state: { amount, actual } });
    const { groups, spent, amount } = budgetGroups([b('a', 100, 120), b('b', 100, 80), b('c', 100, 40)], [], 50);
    expect(groups[0]!.rows.map((r) => r.flag)).toEqual(['Over', 'Ahead of pace', '']);
    expect([spent, amount]).toEqual([240, 300]);
  });

  it('builds the funnel from the headline and leaves out steps with no figure', () => {
    expect(funnelSteps({ signups: { value: 200 }, activated_rate: { value: 0.5 }, new_customers: { value: 20 } })).toEqual([
      { name: 'Sign-ups', count: 200 },
      { name: 'Activated', count: 100 },
      { name: 'New customers', count: 20 },
    ]);
  });

  it('keeps only known address values', () => {
    expect(validateFinanceSearch({ tab: 'pnl', period: 'week', panel: 'line:acc_1', from: '2026-13' })).toEqual({ tab: 'pnl', panel: 'line:acc_1' });
    expect(panelOf('budget:b1')).toEqual(['budget', 'b1']);
  });

  it('a read paused offline with nothing held is a fault with Try again, not a skeleton that never ends', () => {
    const q = { isPending: true, isError: false, error: null };
    expect(readState({ ...q, fetchStatus: 'fetching' })).toBe('loading');
    expect(readState({ ...q, fetchStatus: 'paused' })).toBe('fault');
  });

  it('names the fresh Growth sources quietly and a stale one out loud, by name @s400', () => {
    const h = {
      basis: 'customers' as const,
      unknown_share: 0,
      stale_sources: ['source:2'],
      reconciled: 'not_reconciled' as const,
      sources: [
        { source: 'source:1', name: 'Payments', as_of: '2026-11-18', pushed_at: '2026-11-19T10:00:00Z', stale: false },
        { source: 'source:2', name: 'Ad sheet', as_of: '2026-10-01', pushed_at: null, stale: true },
      ],
    };
    expect(loudHealth(h)).toEqual(['Stale: Ad sheet']);
  });
});

describe('a count chart’s axis', () => {
  const labels = (values: number[]) => axis(values, true).ticks.map((t) => count(t));

  it('never prints the same label twice when every month is 0', () => {
    expect(labels([0, 0, 0])).toEqual(['0', '1']);
  });

  it('steps by whole numbers for small counts', () => {
    for (const values of [[0, 1], [0, 2], [1, 3], [0, 7], [2, 9, 4]]) {
      const ticks = axis(values, true).ticks;
      expect(ticks.every((t) => Number.isInteger(t))).toBe(true);
      expect(new Set(labels(values)).size).toBe(ticks.length);
    }
  });

  it('keeps fine steps for shares, which are not counts', () => {
    expect(axis([0, 0, 0]).ticks).toEqual([0, 0.5, 1]);
  });
});
