/* The app's own files failing to load. Every page is its own chunk (the router splits them), and a release replaces
   them: a tab opened before it asks for files that are gone. Vite says so with `vite:preloadError`; a script or
   stylesheet of ours failing, or a dynamic import the router did not catch, says the same. Reloading fetches the new
   build. Only this origin's files count: an extension's or the network's script failing says nothing about Tico. */

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

/** Calls `onFail` at the first of our files that does not load. Returns the unsubscribe. */
export function watchAssets(onFail: () => void): () => void {
  const on = (e: Event) => {
    if (isAssetFailure(e)) onFail();
  };
  addEventListener('vite:preloadError', on);
  addEventListener('unhandledrejection', on);
  // A file's load error does not bubble: caught on the way down.
  addEventListener('error', on, true);
  return () => {
    removeEventListener('vite:preloadError', on);
    removeEventListener('unhandledrejection', on);
    removeEventListener('error', on, true);
  };
}
