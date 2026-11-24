/* The app's own files failing to load. Every page is its own chunk (the router splits them), and a release replaces
   them: a tab opened before it asks for files that are gone. Vite says so with `vite:preloadError`; a script or
   stylesheet of ours failing, or a dynamic import the router did not catch, says the same. Reloading fetches the new
   build. Only this origin's files count: an extension's or the network's script failing says nothing about Tico. */

import type { AnyRouter, RouterHistory } from '@tanstack/react-router';
import { useSyncExternalStore } from 'react';
import { callState, isCallActive } from '@/features/calls/routing';
import { behind, thisBuild } from '@/lib/build';
import { signedIn } from './queries';

const own = (url: string): boolean => {
  try {
    return new URL(url, location.href).origin === location.origin;
  } catch {
    return false;
  }
};

const IMPORT_FAILED = /dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

export function isAssetFailure(e: Event): boolean {
  if (e.type === 'vite:preloadError') return true;
  if (e.type === 'unhandledrejection') {
    const why: unknown = (e as PromiseRejectionEvent).reason;
    return IMPORT_FAILED.test(why instanceof Error ? why.message : String(why ?? ''));
  }
  const t = e.target;
  if (t instanceof HTMLScriptElement) return !!t.src && own(t.src);
  if (t instanceof HTMLLinkElement) return /\b(stylesheet|modulepreload)\b/.test(t.rel) && own(t.href);
  return false;
}

/* The card's one fact, held outside React: the shell that draws it may be the part that failed. */
let failed = false;
const shown = new Set<() => void>();
function setFailed(next: boolean): void {
  failed = next;
  for (const fn of [...shown]) fn();
}
const watch = (fn: () => void) => (shown.add(fn), () => void shown.delete(fn));
export const useAssetsFailed = (): boolean => useSyncExternalStore(watch, () => failed, () => false);

// This page is being left (by us): what fails meanwhile is the page going. For 5 s only: a leave prompt can be cancelled.
let leavingUntil = 0;
const leave = (go: () => void) => void ((leavingUntil = Date.now() + 5_000), go());

/** Not a full load now: a call joining or on would drop; a call page opens in place (the desktop's "Move the call back
    to the app" joins once its page is open); a file picked and not sent would go. Words in the chat box are kept. */
const stay = (to = '') => isCallActive(callState()) || to.startsWith('/call/') || !!document.querySelector('[data-unsent]');

/** Recovery for the whole page, started once by main.tsx outside every React tree, so a part of the shell that fails
    to load cannot take it down. At the first of our files that does not load: a session that ended reloads (the server
    sends the page to sign-in); otherwise the page reloads once on its own, and only when it cannot (it already did, a call or a picked file
    would be lost, no answer from the server) the card shows. After a release, page changes load whole. The stop is
    for tests. */
export function startRecovery(router?: AnyRouter): () => void {
  let asking = false;
  let on = true;
  const fail = (e: Event) => {
    // The router reloads once on its own for a missing page module (lazyRouteComponent), past the checks here: its note
    // that it already did is written for it, under its own key, before the import's rejection reaches it.
    const why: unknown = (e as Event & { payload?: unknown }).payload;
    if (why instanceof Error)
      try {
        sessionStorage.setItem(`tanstack_router_reload:${why.message}`, '1');
      } catch {
        // no storage: the router cannot keep its note either
      }
    if (failed || asking || Date.now() < leavingUntil || !isAssetFailure(e)) return;
    asking = true;
    // decided now (by the answer, the composer holding a picked file may be gone with its page) and again then (a call
    // or a file may have started meanwhile)
    const keep = stay();
    void signedIn().then((answer) => {
      asking = false;
      // gone, or a full load the person started meanwhile
      if (!on || Date.now() < leavingUntil) return;
      if (answer === false) leave(() => location.reload());
      else if (answer === null || keep || stay() || !reloadOnce()) setFailed(true);
    });
  };
  // A file's load error does not bubble: caught on the way down (capture).
  const kinds = [['vite:preloadError', false], ['unhandledrejection', false], ['error', true]] as const;
  for (const [kind, capture] of kinds) addEventListener(kind, fail, capture);
  const unfollow = router ? followBuild(router) : undefined;
  return () => {
    on = false;
    for (const [kind, capture] of kinds) removeEventListener(kind, fail, capture);
    unfollow?.();
    leavingUntil = 0;
    setFailed(false);
  };
}

const RELOADED = 'tico-next:assets-reloaded';
const AGAIN_MS = 5 * 60_000;

/** Reloads the page, once per build in a few minutes: false, and no reload, when this build already did (its files
    still fail) or the tab cannot keep that note. */
function reloadOnce(): boolean {
  const build = thisBuild() ?? '';
  try {
    const last = JSON.parse(sessionStorage.getItem(RELOADED) ?? 'null') as { build?: unknown; at?: unknown } | null;
    if (last?.build === build && typeof last.at === 'number' && Date.now() - last.at < AGAIN_MS) return false;
    sessionStorage.setItem(RELOADED, JSON.stringify({ build, at: Date.now() }));
  } catch {
    return false;
  }
  leave(() => location.reload());
  return true;
}

/** While the server serves another build than this page's: the next page change is a full load of its address (the
    page changes anyway, so nothing is lost), and nothing is preloaded (those files are gone), unless `stay`. */
function followBuild(router: AnyRouter): () => void {
  const preload = router.preloadRoute;
  router.preloadRoute = ((opts) => (behind() ? Promise.resolve(undefined) : preload.call(router, opts as never))) as typeof preload;
  const unblock = (router.history as RouterHistory).block({
    enableBeforeUnload: false,
    blockerFn: ({ currentLocation, nextLocation, action }) => {
      const to = nextLocation.href;
      if (!behind() || nextLocation.pathname === currentLocation.pathname || !own(to) || stay(to)) return false;
      if (action === 'PUSH') leave(() => location.assign(to));
      else if (action === 'REPLACE') leave(() => location.replace(to));
      // Back or Forward: the address is the next page's already
      else leave(() => location.reload());
      return action === 'PUSH' || action === 'REPLACE';
    },
  });
  return () => {
    router.preloadRoute = preload;
    unblock();
  };
}
