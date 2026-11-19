import { createFileRoute } from '@tanstack/react-router';
import { RiskPage } from '@/features/company/exports';
import { loadRisk, riskSearch } from '@/features/company/routing';

export const Route = createFileRoute('/company/risk')({
  validateSearch: riskSearch,
  loaderDeps: ({ search }) => ({ risk: search.risk }),
  loader: ({ deps: { risk }, context: { queryClient } }) => loadRisk(queryClient, risk),
  component: Risk,
});

function Risk() {
  return <RiskPage search={Route.useSearch()} />;
}
