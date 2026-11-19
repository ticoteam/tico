import { describe, expect, it } from 'vitest';
import { budgetGroups, centsOf, funnelSteps, pnlRange, stepRange } from './model';
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
    expect(panelOf('budget:new')).toEqual(['budget', 'new']);
  });
});
