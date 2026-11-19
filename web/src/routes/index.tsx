import { createFileRoute, redirect } from '@tanstack/react-router';
import { landing } from '@/features/start/exports';

// The landing address. Signed in: the start page, with the first run open over it while setup is owed. Not signed in,
// nothing here: the root's gate sends the person to sign in and back to this address.
export const Route = createFileRoute('/')({
  beforeLoad: async ({ context }) => {
    const land = await landing(context.queryClient);
    if (land) throw redirect({ to: '/start', search: land.setup ? { setup: 'path' } : {}, replace: true });
  },
  component: () => null,
});
