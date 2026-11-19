import { describe, expect, it } from 'vitest';
import { readSheet } from './history';

// Made-up lines and round amounts only.
describe('Import history sheet @s465', () => {
  it('reads dollars as cents and lists each line once', () => {
    const s = readSheet('Month,Line,Amount,Team\n2026-01,Rent,"$2,000.00",Ops\n2026-02,rent,(500),Ops\n2026-02,Tools,48,\n', true);
    expect(s.csv).toBe('month,line,amount,team\n2026-01,Rent,200000,Ops\n2026-02,rent,-50000,Ops\n2026-02,Tools,4800,\n');
    expect(s.names).toEqual(['Rent', 'Tools']);
    expect(s.rows).toBe(3);
  });

  it('refuses an amount that is not whole cents, naming the row', () => {
    expect(() => readSheet('month,line,amount\n2026-01,Rent,20.5\n', false)).toThrow('Row 2: amount is whole cents');
    expect(() => readSheet('month,amount\n2026-01,100\n', false)).toThrow('Columns: month, line, amount (and team)');
  });
});
