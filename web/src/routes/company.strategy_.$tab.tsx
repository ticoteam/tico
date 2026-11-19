import { createFileRoute, notFound } from '@tanstack/react-router';
import { StrategyPage } from '@/features/company/exports';
import { loadStrategy, strategySearch, STRATEGY_TABS, strategyTab } from '@/features/company/routing';

/* Strategy's other tabs: Market, Org, Goals, Budget, Brand. Strategy itself is /company/strategy. */
export const Route = createFileRoute('/company/strategy_/$tab')({
  validateSearch: strategySearch,
  beforeLoad: ({ params }) => {
    if (params.tab === 'strategy' || !(STRATEGY_TABS as readonly string[]).includes(params.tab)) throw notFound();
  },
  loader: ({ params, context: { queryClient } }) => loadStrategy(queryClient, strategyTab(params.tab)),
  component: StrategyTabRoute,
});

function StrategyTabRoute() {
  const { tab } = Route.useParams();
  return <StrategyPage tab={strategyTab(tab)} search={Route.useSearch()} />;
}
