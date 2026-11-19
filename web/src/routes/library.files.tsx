import { createFileRoute } from '@tanstack/react-router';
import { FilesPage, filesSearch, loadFiles } from '@/features/library/exports';

export const Route = createFileRoute('/library/files')({
  validateSearch: filesSearch,
  loaderDeps: ({ search }) => ({ kind: search.kind, file: search.file }),
  loader: ({ deps, context: { queryClient } }) => loadFiles(queryClient, deps),
  component: Files,
});

function Files() {
  return <FilesPage search={Route.useSearch()} />;
}
