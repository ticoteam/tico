import { createFileRoute } from '@tanstack/react-router';
import { FilesPage } from '@/features/library/exports';
import { filesSearch, loadFiles } from '@/features/library/routing';

export const Route = createFileRoute('/library/files')({
  validateSearch: filesSearch,
  loaderDeps: ({ search }) => ({ kind: search.kind, file: search.file, q: search.file ? undefined : search.q }),
  loader: ({ deps, context: { queryClient } }) => loadFiles(queryClient, deps),
  component: Files,
});

function Files() {
  return <FilesPage search={Route.useSearch()} />;
}
