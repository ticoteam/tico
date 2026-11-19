import type { QueryClient } from '@tanstack/react-query';
import { budgetDay, monthSpan, pnlRange } from './model';
import { budgetsQuery, growthReadQuery, growthSourcesQuery, pnlQuery, segmentsQuery, sourcesQuery } from './queries';
import type { FinanceSearch, GrowthSearch } from './search';

/* What each page reads first, warmed before it draws. A failed read is the page's own fault state, not the router's. */

export function loadFinance(qc: QueryClient, search: FinanceSearch): Promise<unknown> {
  const ym = budgetDay().slice(0, 7);
  const as = search.as;
  const month = pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }, as);
  const reads: Promise<unknown>[] = [qc.ensureQueryData(sourcesQuery(as))];
  if (!search.tab) reads.push(qc.ensureQueryData(month));
  if (search.tab === 'budgets') reads.push(qc.ensureQueryData(budgetsQuery(budgetDay(), as)), qc.ensureQueryData(month));
  if (search.tab === 'pnl') {
    const period = search.period ?? 'month';
    const r = pnlRange(period, search.from);
    reads.push(qc.ensureQueryData(pnlQuery({ start: r.start, end: r.end, period, basis: search.basis ?? 'accrual' }, as)));
  }
  return Promise.all(reads).catch(() => undefined);
}

export function loadGrowth(qc: QueryClient, search: GrowthSearch): Promise<unknown> {
  const reads: Promise<unknown>[] = [qc.ensureQueryData(growthSourcesQuery), qc.ensureQueryData(segmentsQuery)];
  if (search.tab !== 'sources') reads.push(qc.ensureQueryData(growthReadQuery(search.segment, search.month)));
  return Promise.all(reads).catch(() => undefined);
}
