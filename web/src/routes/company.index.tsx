import { createFileRoute } from '@tanstack/react-router';
import { CompanyPage } from '@/features/company/exports';
import { loadCompany, panelSearch } from '@/features/company/routing';
import { pageSearch } from '@/lib';

export const Route = createFileRoute('/company/')({
  validateSearch: panelSearch,
  loader: ({ context: { queryClient } }) => loadCompany(queryClient),
  component: Company,
});

function Company() {
  return <CompanyPage search={Route.useSearch({ select: (s) => pageSearch(s), structuralSharing: true })} />;
}
