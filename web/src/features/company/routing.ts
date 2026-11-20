import type { QueryClient } from '@tanstack/react-query';
import { brandQuery, floorQuery, goalsTreeQuery, groupsQuery, marketBriefingQuery, marketSpendQuery, mattersQuery, productsQuery, revenueQuery, riskQuery, risksQuery, spendQuery, strategyQuery } from './queries';
import type { StrategySearch } from './StrategyPage';

// What the routes use before a page's code loads: the address checks and the first reads. No components here: the
// first load imports this file, and a page comes through exports.ts with its route.

/** Strategy's tabs, in the mock's order; each is its own address under /company/strategy. */
export const STRATEGY_TABS = ['strategy', 'market', 'org', 'goals', 'brand'] as const;
export type StrategyTab = (typeof STRATEGY_TABS)[number];

const text = (v: unknown) => (typeof v === 'string' && v.length > 0 && v.length <= 200 ? v : undefined);

/** `?panel=`: a stage, a goal, a KPI, a form, a product, a matter. */
export const panelSearch = (s: Record<string, unknown>): { panel?: string } => ({ panel: text(s.panel) });
const whole = (v: unknown) => {
  const n = typeof v === 'string' ? Number(v) : v;
  return typeof n === 'number' && Number.isInteger(n) && n > 0 ? n : undefined;
};
const yes = (v: unknown) => (v === true || v === 'true' || v === '1' ? true : undefined);
/** Strategy and its tabs: `?panel=`, and on Strategy and Brand `?v=` a past version, `?edit` the brand draft. */
export const strategySearch = (s: Record<string, unknown>): StrategySearch => ({ panel: text(s.panel), v: whole(s.v), edit: yes(s.edit) });
/** Risk: `?risk=` opens one risk as its own page; `?panel=` a matter or the new-risk form. */
export const riskSearch = (s: Record<string, unknown>): { risk?: string; panel?: string } => ({ risk: text(s.risk), panel: text(s.panel) });
/** A Strategy tab from the address; anything else is the Strategy page itself. */
export const strategyTab = (t: unknown): StrategyTab => (STRATEGY_TABS as readonly unknown[]).includes(t) ? (t as StrategyTab) : 'strategy';

// What each page needs for its first paint, awaited: a read that fails is the router's fault (PageFault), a refusal
// its no-access state. Market reads its many parts itself, each with its own state.
export function loadCompany(qc: QueryClient) {
  const floor = qc.ensureQueryData(floorQuery);
  void qc.prefetchQuery(revenueQuery);
  void qc.prefetchQuery(spendQuery);
  return floor;
}
export function loadStrategy(qc: QueryClient, tab: StrategyTab): Promise<unknown> | undefined {
  if (tab === 'goals') return qc.ensureQueryData(goalsTreeQuery);
  if (tab === 'org') return qc.ensureQueryData(groupsQuery);
  if (tab === 'brand') return qc.ensureQueryData(brandQuery);
  if (tab === 'strategy') return qc.ensureQueryData(strategyQuery);
  void qc.prefetchQuery(marketSpendQuery);
  void qc.prefetchQuery(marketBriefingQuery);
}
export const loadProducts = (qc: QueryClient) => qc.ensureQueryData(productsQuery);
export const loadRisk = (qc: QueryClient, risk?: string) => (risk ? qc.ensureQueryData(riskQuery(risk)) : qc.ensureQueryData(risksQuery));
export const loadDisputes = (qc: QueryClient) => qc.ensureQueryData(mattersQuery('disputes'));
