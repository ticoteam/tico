import { createFileRoute } from '@tanstack/react-router';
import { loadStrategy, strategySearch, StrategyPage } from '@/features/company/exports';

export const Route = createFileRoute('/company/strategy')({
  validateSearch: strategySearch,
  loader: ({ context: { queryClient } }) => loadStrategy(queryClient, 'strategy'),
  component: Strategy,
});

function Strategy() {
  return <StrategyPage tab="strategy" search={Route.useSearch()} />;
}
