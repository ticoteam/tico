import { createFileRoute } from '@tanstack/react-router';
import { loadStrategy, panelSearch, StrategyPage } from '@/features/company/exports';

export const Route = createFileRoute('/company/strategy')({
  validateSearch: panelSearch,
  loader: ({ context: { queryClient } }) => loadStrategy(queryClient, 'strategy'),
  component: Strategy,
});

function Strategy() {
  return <StrategyPage tab="strategy" search={Route.useSearch()} />;
}
