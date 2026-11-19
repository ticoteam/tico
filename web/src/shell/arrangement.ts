import { useLocation } from '@tanstack/react-router';
import type { Arrangement } from './layout';

/* Which arrangement the address asks for (architecture.md 3, layout.md 6 "Which memory"):
   a call is `call`; an open task is `task`; an agent's or a person's own page is `agent`;
   a page with a second chat (?chat=) is `chat`; anything else is `page`. */
export function arrangementFor(pathname: string, search: Record<string, unknown> = {}): Arrangement {
  const parts = pathname.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean);
  const [root, a, b] = parts;
  if (root === 'call' && a) return 'call';
  if ((root === 'tasks' && a && b) || ((root === 'flow' || root === 'task') && a)) return 'task';
  if (root === 'team' && a) return 'agent';
  if (typeof search.chat === 'string' && search.chat) return 'chat';
  return 'page';
}

export function useArrangement(): Arrangement {
  return useLocation({ select: l => arrangementFor(l.pathname, l.search as Record<string, unknown>) });
}
