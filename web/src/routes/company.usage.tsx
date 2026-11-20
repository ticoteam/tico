import { createFileRoute } from '@tanstack/react-router';
import { UsagePage } from '@/features/settings/exports';
import { loadUsage, type UsageSearch } from '@/features/settings/routing';

const RANGES = ['today', '7d', '30d', 'month'] as const;
type Range = (typeof RANGES)[number];

/* `/company/usage`: spend (the default) or `?view=limits`; `?range=`, `?by=model`, and `?open=<agent>` for the agent
   opened in either view. */
export const Route = createFileRoute('/company/usage')({
  validateSearch: (raw: Record<string, unknown>): UsageSearch => ({
    ...(raw.view === 'limits' ? { view: 'limits' as const } : {}),
    ...(RANGES.includes(raw.range as Range) ? { range: raw.range as Range } : {}),
    ...(raw.by === 'model' ? { by: 'model' as const } : {}),
    ...(typeof raw.open === 'string' && raw.open ? { open: raw.open } : {}),
  }),
  loaderDeps: ({ search }) => ({ view: search.view, range: search.range, by: search.by }),
  loader: ({ deps, cause, context: { queryClient } }) => {
    // On the open Spend page, the component knows which local filters its queries need.
    if (cause === 'stay' && deps.view !== 'limits') return;
    return loadUsage(queryClient, deps);
  },
  component: UsageRoute,
});

function UsageRoute() {
  return <UsagePage search={Route.useSearch()} />;
}
