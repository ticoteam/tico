import { useQuery } from '@tanstack/react-query';
import { fileQuery } from '@/features/library/exports';
import type { TaskRequirement } from '@/api/schema';
import { decisionOf, personOf, sendTitle, tabLabel, tabsOf, type TaskTab } from './model';
import { taskFilesQuery, type TaskData } from './queries';
import { useWaiting, type Waiting } from './Waiting';
import { waitSlugs } from './waits';

/* A task's tabs, as its page shows them: the one resolver the page, its dock, the chat card beside it and the thread
   cards that open it read, so each names the same tab for the same email. */

/** A tab of the open task, or an email's OK hung on it (`wait`, its OK task). */
export type Tab = TaskTab & { wait?: TaskData };

/** One tab per email's OK that waits on the person, first and marked (one the person asked changes on is not marked): "Email", or "Email to <who>" when there are two. */
function waitTabs(items: Waiting[], own: TaskTab[]): Tab[] {
  const mails = items.flatMap((x) => (x.send ? [{ ...x, send: x.send }] : []));
  const slugs = waitSlugs(mails.length, own.map((x) => x.slug));
  return mails.map(({ sub, send }, i) => {
    const to = sendTitle(sub.title)?.to;
    // Changes asked: the tab stays, not marked as waiting on the person.
    const mark = decisionOf(sub) ? ('ask' as const) : ('none' as const);
    return { slug: slugs[i]!, label: mails.length > 1 && to ? `Email to ${personOf(to)}` : 'Email', mark, email: send, wait: sub };
  });
}

/** Every tab of a task, the one list the page, its dock and the chat card beside it read: its emails' OKs filed as
    tasks of their own (a "Send:" sub-task) that wait on the person first, then its own. `settled` once both are read.
    No task (not read yet): no tabs, nothing read, never settled. */
export function useTabs(t: TaskData | undefined, chosen?: TaskRequirement | null) {
  const files = useQuery({ ...taskFilesQuery(t?.id ?? ''), enabled: !!t });
  const waiting = useWaiting(t);
  // The files did not load: what the ask is about still opens, read by its id, never another page it shows.
  const own = t ? tabsOf(t, files.data ?? (files.isError ? [] : undefined), chosen) : [];
  // The file the ask is about when it is another task's: its tab is named once its record is read.
  const away = own.find((x) => x.other);
  const other = useQuery({ ...fileQuery(away?.other ?? ''), enabled: !!away });
  if (away && other.data) away.label = tabLabel(other.data.title || other.data.name || 'File');
  const tabs: Tab[] = [...waitTabs(waiting.items, own), ...own];
  return { tabs, files, waiting, settled: !!t && files.isFetched && waiting.settled };
}

