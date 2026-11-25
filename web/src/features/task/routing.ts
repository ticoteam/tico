import type { QueryClient } from '@tanstack/react-query';
import { redirect, useRouterState } from '@tanstack/react-router';
import { useMemo } from 'react';
import { timelineQuery } from '@/conversation';
import { threadQuery } from '@/features/threads/routing';
import { coldOf, coldPage, openOf, type TaskWords } from '@/lib';
import { decisionOf, isPage, tabsOf, targetOf } from './model';
import { pageShownQuery, taskDetailQuery, taskFilesQuery, taskRefQuery } from './queries';
import { waitsOnYou } from './waits';

// What the routes use before a task's code loads: its first read, its address and the address check. No components
// here: the first load imports this file, and the task page comes through exports.ts with its route.
export { taskRefQuery, taskDetailQuery };
export { tabAddress as taskAddress, CHAT as CHAT_TAB } from './model';

/** Warm what an opening task reads first (TaskModal): the task, its chat's view (the comments, or the agent's session
    the address names, `?session=`), its files, and the page its tab shows. Nothing waits on it: the task's page picks
    its own tab from what it reads. A refused read leaves the page to show the error. */
export async function loadTask(client: QueryClient, param: string, tab?: string, session?: unknown): Promise<void> {
  try {
    const id = await client.ensureQueryData(taskRefQuery(param));
    const t = await client.ensureQueryData(taskDetailQuery(id));
    if (t.conversation_id) {
      // the task's own thread shows its comments, or the agent's session the address names; a task in its owner's main
      // chat, that chat
      const cid = t.conversation_id;
      const view = typeof session === 'string' && session.startsWith('bot:') ? { session } : { comments: true as const };
      void client
        .fetchQuery(threadQuery(cid))
        .then((room) => client.prefetchInfiniteQuery(timelineQuery(cid, room.task_id === t.id ? view : null)))
        .catch(() => undefined);
    }
    void client.prefetchQuery(taskFilesQuery(id));
    // An email's OK hung on it waits on the person: it opens on the email, no page to read.
    if (!tab && (await waitsOnYou(client, t))) return;
    const selected = tab ? tabsOf(t).find((x) => x.slug === tab) : tabsOf(t)[0];
    // the default document opens unless the task waits on an answer about another thing (a file to sign)
    const target = targetOf(decisionOf(t, null)); // as tabsOf reads it: the ask that is not an email's
    const asked = selected?.mark === 'ask' && (!target || selected.doc?.file_id === target.id);
    const doc = selected?.doc && (tab || asked || !decisionOf(t)) ? selected.doc : undefined;
    if (doc && isPage(doc)) void client.prefetchQuery(pageShownQuery(id, doc.file_id));
  } catch {
    // the page shows what went wrong
  }
}

const NO_WORDS: TaskWords & { list?: string } = Object.freeze({});

/** The open task's own words (lib openOf: its tab, an ask, a version, a session) and the list it was opened from. */
export function useTaskWords(): TaskWords & { list?: string } {
  const search = useRouterState({ select: (s) => s.location.search as Record<string, unknown> });
  return useMemo(() => {
    const open = openOf(search);
    return open ? { ...open.words, ...(open.list && { list: open.list }) } : NO_WORDS;
  }, [search]);
}

/** A task's own address loaded with nothing on screen (a link, a bookmark, a reload, a new tab): the task over the page
    it belongs on, its board or Tasks (lib coldPage). In the app its links open over the page they are on (router.tsx). */
export function openCold({ location }: { location: { pathname: string; search: unknown } }): never {
  const cold = coldOf(location.pathname.replace(/^\/next(?=\/|$)/, ''), location.search as Record<string, unknown>);
  throw redirect({ ...(cold ? coldPage(cold, location.search as Record<string, unknown>) : { to: '/tasks' }), replace: true } as never);
}
