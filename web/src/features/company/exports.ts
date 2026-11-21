// Company, Strategy (Market, Org, Goals, Brand), Products and Risk. The only file other code imports from this feature.
import { amount } from './parts';
import type { TreeGoal } from './queries';

export { CompanyPage, type CompanySearch } from './CompanyPage';
export { StrategyPage, STRATEGY_TABS, strategyTabs, type StrategySearch, type StrategyTab } from './StrategyPage';
export type { GoalsSearch } from './GoalsPage';
export { ProductsPage, type ProductsSearch } from './ProductsPage';
export { dateLine, matterWhen, RiskPage, type RiskSearch, type RiskView } from './RiskPage';
export { floorQuery, goalsTreeQuery, productsQuery, risksQuery, riskQuery, strategyQuery, type TreeGoal } from './queries';
export {
  loadCompany,
  loadDisputes,
  loadProducts,
  loadRisk,
  loadStrategy,
  panelSearch,
  riskSearch,
  strategySearch,
  strategyTab,
} from './routing';

/** Where one goal opens, in Goals' panel: for a link from another page (a teammate's goals). */
export const goalLink = (goal: string) => {
  const panel = `goal:${goal}`;
  return { to: '/company/strategy/goals', search: { panel }, href: `/company/strategy/goals?panel=${encodeURIComponent(panel)}` } as const;
};
/** A goal's reading in a line: its first KPI's latest, of its target ("12 of 20"), or nothing before a reading. */
export function goalFigure(g: TreeGoal): string {
  const k = g.kpis[0];
  if (!k?.latest) return '';
  const now = amount(k.latest.value, k.unit);
  return k.link?.target != null ? `${now} of ${amount(k.link.target, k.unit)}` : now;
}
