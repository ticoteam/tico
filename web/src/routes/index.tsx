import { createFileRoute, redirect } from '@tanstack/react-router';
import { landing } from '@/features/start/routing';
import { firstPipeline, pipelinesQuery } from '@/features/tasks/routing';

// The landing address. Signed in: the Tasks board, with the first run open over it while setup is owed (or when the
// address asks for it). Straight to the pipeline, so the first run's `?setup=` survives (/tasks redirects without it).
// Not signed in, nothing here: the root's gate sends the person to sign in and back to this address. A team the
// address names for the menu (`?team=`, where old team links land) and Add teammate (`?add=`) come along.
export const Route = createFileRoute('/')({
  validateSearch: (search: Record<string, unknown>): { setup?: string } =>
    typeof search.setup === 'string' && search.setup ? { setup: search.setup } : {},
  beforeLoad: async ({ context: { queryClient }, search }) => {
    const land = await landing(queryClient);
    if (!land) return;
    const setup = search.setup ?? (land.setup ? 'path' : undefined);
    const pipeline = firstPipeline(await queryClient.ensureQueryData(pipelinesQuery).catch(() => []));
    throw redirect({
      ...(pipeline ? { to: '/tasks/$pipeline', params: { pipeline } } : { to: '/tasks' }),
      search: { ...(setup ? { setup } : {}), ...(search.team ? { team: search.team } : {}), ...(search.add ? { add: search.add } : {}) },
      replace: true,
    } as never);
  },
  component: () => null,
});
