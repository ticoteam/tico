import { createRouter } from '@tanstack/react-router';
import { queryClient } from '@/data/query-client';
import { takeHelperCode } from '@/features/library/routing';
import { newAddressFor } from '@/oldAddress/map';
import { PagePending } from '@/pagekinds';
import { routeTree } from '@/routeTree.gen';
import { PageFault } from '@/shell/PageFault';

/** Where the app lived before it moved to /: a link from then (a bookmark, an agent's message, an older desktop app)
    opens the same page. The server sends these on itself (backend/web_routes.py); this covers a page served as is. */
const OLD_BASE = '/next';

/** The address to open at startup in place of this one, or null to keep it. An old app address (`/#/task/12`, from mail,
    a push or a bookmark) opens its page here; one with no page opens home. A skills helper's code is taken out (and held
    for the Skills page), so it never sits in the address or history. */
export function startAddress(path: string, search: string, hash: string): string | null {
  if (hash.startsWith('#/')) return takeHelperCode(newAddressFor(hash) ?? '/');
  const moved = path === OLD_BASE || path.startsWith(`${OLD_BASE}/`);
  const at = (moved ? path.slice(OLD_BASE.length) || '/' : path) + search;
  const clean = takeHelperCode(at);
  return moved || clean !== at ? clean + hash : null;
}

/** An in-app link as the router takes it: an old app address (`/#/task/12`, `#/settings`, in a message or a server's
    answer) is its new page's address; any other is unchanged. */
export function appHref(href: string): string {
  return /^\/?#\//.test(href) ? (newAddressFor(href) ?? '/') : href;
}

// Before the router is made (its history reads the address then), so the first render already has the new one.
const start = startAddress(location.pathname, location.search, location.hash);
if (start !== null) history.replaceState(history.state, '', start);

export const router = createRouter({
  routeTree,
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

// An old address followed inside the app (a plain `#/…` link in a message) opens its page, as one does at startup.
addEventListener('hashchange', () => {
  const to = location.hash.startsWith('#/') ? startAddress(location.pathname, location.search, location.hash) : null;
  if (to !== null) void router.navigate({ href: to, replace: true });
});
