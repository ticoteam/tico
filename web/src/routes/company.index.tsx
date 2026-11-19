import { createFileRoute } from '@tanstack/react-router';
import { CompanyPage, loadCompany, panelSearch } from '@/features/company/exports';

export const Route = createFileRoute('/company/')({
  validateSearch: panelSearch,
  loader: ({ context: { queryClient } }) => loadCompany(queryClient),
  component: Company,
});

function Company() {
  return <CompanyPage search={Route.useSearch()} />;
}
