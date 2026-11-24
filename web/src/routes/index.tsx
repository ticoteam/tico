import { createFileRoute, redirect } from '@tanstack/react-router';
import { landing } from '@/features/start/routing';

// The landing address. Signed in: Tasks (Needs you on top); while setup is owed, Tico's sheet holds it. Not signed in,
// nothing here: the root's gate sends the person to sign in and back to this address. A team the address names for the
// menu (`?team=`, where old team links land) and Add teammate (`?add=`) come along.
export const Route = createFileRoute('/')({
  beforeLoad: async ({ context: { queryClient }, search }) => {
    const land = await landing(queryClient);
    if (!land) return;
    const { team, add } = search as { team?: unknown; add?: unknown };
    throw redirect({ to: '/tasks', search: { ...(team ? { team } : {}), ...(add ? { add } : {}) }, replace: true } as never);
  },
  component: () => null,
});
