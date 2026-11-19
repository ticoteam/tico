import { createFileRoute } from '@tanstack/react-router';
import { StrategyPage } from '@/features/company/exports';
import { loadStrategy, strategySearch } from '@/features/company/routing';

export const Route = createFileRoute('/company/strategy')({
  validateSearch: strategySearch,
  loader: ({ context: { queryClient } }) => loadStrategy(queryClient, 'strategy'),
  component: Strategy,
});

function Strategy() {
  return <StrategyPage tab="strategy" search={Route.useSearch()} />;
}
