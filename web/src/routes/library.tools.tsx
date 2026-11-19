import { createFileRoute } from '@tanstack/react-router';
import { ToolsPage } from '@/features/library/exports';
import { loadTools, toolsSearch } from '@/features/library/routing';

export const Route = createFileRoute('/library/tools')({
  validateSearch: toolsSearch,
  loaderDeps: ({ search }) => ({ tool: search.tool }),
  loader: ({ deps, context: { queryClient } }) => loadTools(queryClient, deps),
  component: Tools,
});

function Tools() {
  return <ToolsPage search={Route.useSearch()} />;
}
