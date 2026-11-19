import { createFileRoute } from '@tanstack/react-router';
import { GrowthPage, loadGrowth, validateGrowthSearch } from '@/features/money/exports';

/** Growth: `?segment=` picks the scope, `?month=` the month, `?panel=channel:<slug>` opens a channel beside. */
export const Route = createFileRoute('/company/growth')({
  validateSearch: validateGrowthSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) => loadGrowth(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <GrowthPage search={Route.useSearch()} />;
}
