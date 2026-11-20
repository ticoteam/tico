import { useRouter } from '@tanstack/react-router';
import { useEffect, useSyncExternalStore } from 'react';

/* The open page's name, as the person reads it: the h1 in the page's sheet (a record's name, "Tasks", a thread's
   title). It is what Tico's chip and the tab's title show, and what a message to Tico says the person was looking at.
   Read when a route has drawn, and when a page's head says its name changed (`showPageName`): never by watching the
   page, whose chats change on every streamed word. */
const H1 = '.pane.page h1';

export const pageName = (): string => document.querySelector(H1)?.textContent?.trim() ?? '';

let name = '';
const heard = new Set<() => void>();
const listen = (on: () => void) => {
  heard.add(on);
  return () => heard.delete(on);
};

/** A page's head names the page: its data arrived after the route drew, or it was renamed while open. */
export function showPageName(next: string): void {
  if (next === name) return;
  name = next;
  for (const on of heard) on();
}
const reread = () => showPageName(pageName());

/** The page's name, kept current: read again each time a route has drawn. */
export function usePageName(): string {
  // a part drawn alone (a test, a call window) has no router: the page's head still names the page
  const router = useRouter({ warn: false }) as ReturnType<typeof useRouter> | undefined;
  useEffect(() => {
    reread();
    return router?.subscribe('onRendered', reread);
  }, [router]);
  return useSyncExternalStore(listen, () => name, () => '');
}

/**
 * Where the person asked Tico from, as a message's `refs.page`. The server hands Tico's turn only a path that starts
 * "#/" (backend/assistant.py post_message_sync, from the old app's hash addresses), so the app's own address (its
 * search too, which names an open item) goes after a "#", cut to what the server takes.
 */
export const askedFrom = (address: string, title: string) => ({ path: `#${address}`.slice(0, 300), title: title.slice(0, 200) });

