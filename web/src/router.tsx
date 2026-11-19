import { createRouter } from '@tanstack/react-router';
import { queryClient } from '@/data/query-client';
import { routeTree } from '@/routeTree.gen';

export const router = createRouter({
  routeTree,
  basepath: '/next',
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
