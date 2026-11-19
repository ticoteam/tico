import { createFileRoute } from '@tanstack/react-router';
import { loadDisputes, RiskPage, riskSearch } from '@/features/company/exports';

/* Risk's Disputes tab: every dispute in one table; a row opens its matter in the panel. */
export const Route = createFileRoute('/company/risk_/disputes')({
  validateSearch: riskSearch,
  loader: ({ context: { queryClient } }) => loadDisputes(queryClient),
  component: Disputes,
});

function Disputes() {
  return <RiskPage view="disputes" search={Route.useSearch()} />;
}
