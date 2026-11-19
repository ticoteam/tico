import { createRouter } from '@tanstack/react-router';
import { queryClient } from '@/data/query-client';
import { newAddressFor } from '@/oldAddress/map';
import { routeTree } from '@/routeTree.gen';

const BASE = '/next';

// An old app address (`/#/task/12`, from mail, a push or a bookmark) opens its page here; one with no page opens home.
// Before the router is made: its history reads the address then.
if (location.hash.startsWith('#/')) history.replaceState(history.state, '', BASE + (newAddressFor(location.hash) ?? '/'));

export const router = createRouter({
  routeTree,
  basepath: BASE,
  context: { queryClient },
  defaultPreload: 'intent',
  // the cache already holds what a hover preloaded; the router need not keep its own copy
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
