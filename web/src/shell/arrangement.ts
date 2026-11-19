import { useLocation } from '@tanstack/react-router';
import type { Arrangement } from './layout';

/* Which arrangement the address asks for (architecture.md 3, layout.md 6 "Which memory"):
   a call is `call`; an open task is `task`; an agent's or a person's own page is `agent`;
   a page with a second chat (?chat=) is `chat`; anything else is `page`.
   `/team/<who>` is also a team's page, which the path alone cannot tell apart: `isActor` says whether `<who>` is a
   teammate (the root asks the directory). Without it, every `/team/<who>` is a teammate's. */
export function arrangementFor(pathname: string, search: Record<string, unknown> = {}, isActor?: (who: string) => boolean): Arrangement {
  const parts = pathname.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean);
  const [root, a, b] = parts;
  if (root === 'call' && a) return 'call';
  if ((root === 'tasks' && a && b) || ((root === 'flow' || root === 'task') && a)) return 'task';
  if (root === 'team' && a && (isActor?.(decodeURIComponent(a)) ?? true)) return 'agent';
  if (typeof search.chat === 'string' && search.chat) return 'chat';
  return 'page';
}

/** Whether `/team/<who>` is a teammate's own page, for `arrangementFor`'s `isActor`: not a team's page, and not
    your own (your page has no chat beside it: your assistant is Tico). `find` looks a key up in the directory
    (undefined while it loads); `me` is your actor id once known. Until the directory is here, every `<who>` but
    you is taken for a teammate, so an agent's page does not jump; a person's page waits for `me`. */
export function isTeammate(
  who: string,
  find: ((key: string) => { id: string; kind: 'person' | 'agent' } | undefined) | undefined,
  me: string | undefined,
): boolean {
  const mine = (id: string) => id === me || `human:${id}` === me;
  if (!find) return !mine(who);
  const a = find(who) ?? find(`human:${who}`);
  if (!a) return false;
  if (a.kind === 'agent') return true;
  return !!me && a.id !== me;
}

export function useArrangement(isActor?: (who: string) => boolean): Arrangement {
  return useLocation({ select: l => arrangementFor(l.pathname, l.search as Record<string, unknown>, isActor) });
}
