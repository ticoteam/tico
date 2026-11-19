import type { QueryClient } from '@tanstack/react-query';
import { budgetDay, monthSpan, pnlRange } from './model';
import { budgetsQuery, growthReadQuery, pnlQuery, segmentsQuery, sourcesQuery } from './queries';
import type { FinanceSearch, GrowthSearch } from './search';

/* What each page reads first, before it draws. Finance's pages draw their own states (a refusal there keeps the
   view-as Stop), so its reads are only warmed. */

export function loadFinance(qc: QueryClient, search: FinanceSearch): Promise<unknown> {
  const ym = budgetDay().slice(0, 7);
  const as = search.as;
  const month = pnlQuery({ ...monthSpan(ym), period: 'month', basis: 'accrual' }, as);
  const reads: Promise<unknown>[] = [];
  if (search.tab === 'sources') reads.push(qc.ensureQueryData(sourcesQuery(as)));
  if (!search.tab) reads.push(qc.ensureQueryData(month));
  if (search.tab === 'budgets') reads.push(qc.ensureQueryData(budgetsQuery(budgetDay(), as)), qc.ensureQueryData(month));
  if (search.tab === 'pnl') {
    const period = search.period ?? 'month';
    const r = pnlRange(period, search.from);
    reads.push(qc.ensureQueryData(pnlQuery({ start: r.start, end: r.end, period, basis: search.basis ?? 'accrual' }, as)));
  }
  return Promise.all(reads).catch(() => undefined);
}

/** Growth's read, awaited: a failure is the route's fault, a refusal its no-access state. The scope tabs come as they come. */
export function loadGrowth(qc: QueryClient, search: GrowthSearch): Promise<unknown> {
  void qc.prefetchQuery(segmentsQuery);
  return qc.ensureQueryData(growthReadQuery(search.segment, search.month));
}
