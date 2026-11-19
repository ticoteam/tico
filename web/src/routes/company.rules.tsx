import { createFileRoute } from '@tanstack/react-router';
import { RulesPage, loadRules, validateRulesSearch } from '@/features/routines/exports';

/** Rules: `?tab=` picks the tab, `?who=` whose rules on Company, `?panel=org:<id>` or `stage:<id>` opens one. */
export const Route = createFileRoute('/company/rules')({
  validateSearch: validateRulesSearch,
  loaderDeps: ({ search }) => search,
  loader: ({ context, deps }) => loadRules(context.queryClient, deps),
  component: Page,
});

function Page() {
  return <RulesPage search={Route.useSearch()} />;
}
