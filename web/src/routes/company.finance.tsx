import { createFileRoute } from '@tanstack/react-router';
import { FinancePage } from '@/features/money/exports';
import { loadFinance, validateFinanceSearch } from '@/features/money/routing';

/** Finance: `?tab=` picks the page, `?panel=line:<account>` or `?panel=budget:<id>` opens a row beside. */
export const Route = createFileRoute('/company/finance')({
  validateSearch: validateFinanceSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) => loadFinance(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <FinancePage search={Route.useSearch()} />;
}
