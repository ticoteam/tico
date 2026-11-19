import { createRouter } from '@tanstack/react-router';
import { queryClient } from '@/data/query-client';
import { takeHelperCode } from '@/features/library/routing';
import { newAddressFor } from '@/oldAddress/map';
import { PagePending } from '@/pagekinds';
import { routeTree } from '@/routeTree.gen';
import { PageFault } from '@/shell/PageFault';

const BASE = '/next';

/** The address to open at startup in place of this one, or null to keep it. An old app address (`/#/task/12`, from mail,
    a push or a bookmark) opens its page here; one with no page opens home. A skills helper's code is taken out (and held
    for the Skills page), so it never sits in the address or history. */
export function startAddress(path: string, search: string, hash: string): string | null {
  const old = hash.startsWith('#/');
  if (!old && !(path === BASE || path.startsWith(`${BASE}/`))) return null;
  const at = old ? (newAddressFor(hash) ?? '/') : (path.slice(BASE.length) || '/') + search;
  const clean = takeHelperCode(at);
  if (old) return BASE + clean;
  return clean === at ? null : BASE + clean + hash;
}

// Before the router is made (its history reads the address then), so the first render already has the new one.
const start = startAddress(location.pathname, location.search, location.hash);
if (start !== null) history.replaceState(history.state, '', start);

export const router = createRouter({
  routeTree,
  basepath: BASE,
  context: { queryClient },
  defaultPreload: 'intent',
  // the cache already holds what a hover preloaded; the router need not keep its own copy
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
  /* Places are kept per page, not per history step: opening a panel, a chat or a filter (the search) never moves the
     page, a page opens where it was left (Back included), and a page body never takes over the last page's place. */
  getScrollRestorationKey: (l) => l.pathname,
  scrollToTopSelectors: ['[data-scroll-restoration-id="page-body"]'],
  // a page that breaks while drawing shows its fault in its own place; the menu and Tico stay
  defaultErrorComponent: PageFault,
  // a page not in the cache: the old one stays 150 ms, then the page kind's bars, held at least 250 ms so they never flash
  defaultPendingComponent: PagePending,
  defaultPendingMs: 150,
  defaultPendingMinMs: 250,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
