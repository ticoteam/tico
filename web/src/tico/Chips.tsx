import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { useLocation, useRouterState } from '@tanstack/react-router';
import { useState } from 'react';
import { run } from '@/actions';
import { liveQuery, timelineQuery } from '@/conversation';
import { catchUpQuery } from '@/features/threads/routing';
import { Icon, type IconName } from '@/kit/Icon';
import { arrangementFor } from '@/shell';

/* What to ask Tico, over its box (plan/threads-team.html "Catch up", widened): at most a few plain asks that follow
   the open page. A press sends the words through tico.ask, which carries the page as `refs.page`. The row hides while
   the person types (CSS: the box is `is-typing`), fades while the chat is scrolled up (the foot's `data-up`), and
   is gone while Tico answers. A chip pressed on a page goes until that page
   is left; Catch up goes until something else comes in. */
export interface Chip {
  text: string;
  icon: IconName;
}

const CATCH_UP: Chip = { text: 'Catch up', icon: 'archive' };
const NEEDS: Chip = { text: 'What needs me?', icon: 'inbox' };
const SUMMARY: Chip = { text: 'Summarize this', icon: 'list' };
const NEXT: Chip = { text: "What's next?", icon: 'arrow' };
const THREAD: Chip = { text: 'Summarize thread', icon: 'list' };
const CHANGED: Chip = { text: 'What changed?', icon: 'clock' };

/** Threads' own tabs, which share `/threads/<x>` with a thread. */
const THREAD_TABS = new Set(['mail', 'messaging', 'catch-up']);
/** The Company pages that read as numbers over time. */
const NUMBERS = new Set([undefined, 'finance', 'growth']);

/** The asks for a page: a task's, a thread's, the Company numbers', or the default (Catch up only while something is new). */
export function chipsFor(pathname: string, news: boolean): Chip[] {
  if (arrangementFor(pathname) === 'task') return [SUMMARY, NEXT];
  const [root, a] = pathname.replace(/^\/next(?=\/|$)/, '').split('/').filter(Boolean);
  if (root === 'threads' && a && !THREAD_TABS.has(a)) return [THREAD];
  if (root === 'company' && NUMBERS.has(a)) return [CHANGED];
  return news ? [CATCH_UP, NEEDS] : [NEEDS];
}

/** Tico is answering in its chat: a run still going, or the person's message still on its way. */
function useAnswering(room: string): boolean {
  const { data: live = [] } = useQuery(liveQuery(room));
  const { data: sending = false } = useInfiniteQuery({
    ...timelineQuery(room),
    select: (d) => d.pages.some((p) => p.messages?.some((m) => m.pending === 'sending')),
  });
  return sending || live.some((r) => !r.ended);
}

export function TicoChips({ room }: { room: string }) {
  const path = useLocation({ select: (l) => l.pathname });
  // Catch up reads a whole feed: it waits for the first page to open, as the menu's counts do.
  const opened = useRouterState({ select: (s) => s.resolvedLocation !== undefined });
  const { data: news = 0 } = useQuery({ ...catchUpQuery, enabled: opened, select: (d) => d.count });
  const answering = useAnswering(room);
  // Catch up remembers what it caught up on; the page's own asks are remembered for that page only.
  const [caught, setCaught] = useState(0);
  const [asked, setAsked] = useState<{ path: string; texts: string[] }>({ path, texts: [] });
  const done = asked.path === path ? asked.texts : [];
  const chips = chipsFor(path, news > 0 && news !== caught).filter((c) => !done.includes(c.text));
  if (answering || !chips.length) return null;
  return (
    <div className="qchips">
      {chips.map((c) => (
        <button
          key={c.text}
          type="button"
          className="qchip"
          onClick={() => {
            if (c === CATCH_UP) setCaught(news);
            else setAsked({ path, texts: [...done, c.text] });
            void run('tico.ask', { text: c.text }, { via: 'click' });
          }}
        >
          <Icon name={c.icon} />
          {c.text}
        </button>
      ))}
    </div>
  );
}
