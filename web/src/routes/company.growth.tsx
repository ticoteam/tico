import { createFileRoute } from '@tanstack/react-router';
import { GrowthPage } from '@/features/money/exports';
import { loadGrowth, validateGrowthSearch } from '@/features/money/routing';

/** Growth: `?segment=` picks the scope, `?month=` the month, `?panel=channel:<slug>` opens a channel beside. */
export const Route = createFileRoute('/company/growth')({
  validateSearch: validateGrowthSearch,
  // Only what the first reads use: an open channel or chat does not read again.
  loaderDeps: ({ search: { segment, month } }) => ({ segment, month }),
  loader: ({ context, deps }) => loadGrowth(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <GrowthPage search={Route.useSearch()} />;
}
