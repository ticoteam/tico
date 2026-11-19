import { createFileRoute } from '@tanstack/react-router';
import { directoryQuery } from '@/data';
import { StartPage } from '@/features/start/exports';
import { needsQuery, todayQuery } from '@/features/start/routing';

// `setup` is the first run's step, open over this page (or any other); the root's FirstRun reads it.
export const Route = createFileRoute('/start')({
  validateSearch: (search: Record<string, unknown>): { setup?: string } =>
    typeof search.setup === 'string' && search.setup ? { setup: search.setup } : {},
  // A part that fails shows its own fault on the page; the page still opens.
  loader: async ({ context: { queryClient: qc } }) => {
    const shown = () => undefined;
    await Promise.all([
      qc.ensureQueryData(needsQuery).catch(shown),
      qc.ensureQueryData(todayQuery()).catch(shown),
      qc.ensureQueryData(directoryQuery).catch(shown),
    ]);
  },
  component: StartPage,
});
