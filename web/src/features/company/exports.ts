// Company, Strategy (Market, Org, Goals, Brand), Products and Risk. The only file other code imports from this feature.
import type { QueryClient } from '@tanstack/react-query';
import { floorQuery, goalsTreeQuery, mattersQuery, productsQuery, riskQuery, risksQuery, strategyQuery } from './queries';
import { STRATEGY_TABS, type StrategyTab } from './StrategyPage';

export { CompanyPage, type CompanySearch } from './CompanyPage';
export { StrategyPage, STRATEGY_TABS, strategyTabs, type StrategyTab } from './StrategyPage';
export type { GoalsSearch } from './GoalsPage';
export { ProductsPage, type ProductsSearch } from './ProductsPage';
export { RiskPage, riskTabs, type RiskSearch, type RiskView } from './RiskPage';
export { floorQuery, goalsTreeQuery, productsQuery, risksQuery, riskQuery, strategyQuery } from './queries';

const text = (v: unknown) => (typeof v === 'string' && v.length > 0 && v.length <= 200 ? v : undefined);

/** `?panel=`: a stage, a goal, a KPI, a form, a product, a matter. */
export const panelSearch = (s: Record<string, unknown>): { panel?: string } => ({ panel: text(s.panel) });
/** Risk: `?risk=` opens one risk as its own page; `?panel=` a matter or the new-risk form. */
export const riskSearch = (s: Record<string, unknown>): { risk?: string; panel?: string } => ({ risk: text(s.risk), panel: text(s.panel) });
/** A Strategy tab from the address; anything else is the Strategy page itself. */
export const strategyTab = (t: unknown): StrategyTab => (STRATEGY_TABS as readonly unknown[]).includes(t) ? (t as StrategyTab) : 'strategy';

// What each page needs for its first paint. A read that fails shows the page's own fault state, so the loader never throws.
const quietly = (read: Promise<unknown>) => read.then(
  () => undefined,
  () => undefined,
);
export const loadCompany = (qc: QueryClient) => quietly(qc.ensureQueryData(floorQuery));
export const loadStrategy = (qc: QueryClient, tab: StrategyTab) => quietly(tab === 'goals' ? qc.ensureQueryData(goalsTreeQuery) : qc.ensureQueryData(strategyQuery));
export const loadProducts = (qc: QueryClient) => quietly(qc.ensureQueryData(productsQuery));
export const loadRisk = (qc: QueryClient, risk?: string) => quietly(risk ? qc.ensureQueryData(riskQuery(risk)) : qc.ensureQueryData(risksQuery));
export const loadDisputes = (qc: QueryClient) => quietly(qc.ensureQueryData(mattersQuery('disputes')));
