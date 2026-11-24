import { useQuery, type QueryClient } from '@tanstack/react-query';
import { timelineQuery } from '@/conversation';
import { threadQuery } from '@/features/threads/routing';
import { meQuery } from '@/data';
import { pipelinesQuery } from '@/features/tasks/routing';
import { CHAT, decisionOf, defaultTab, isPage, tabsOf, targetOf } from './model';
import { pageShownQuery, taskDetailQuery, taskFilesQuery, taskRefQuery, type TaskData } from './queries';
import { questionsFor, waitSlugs, waitsOnYou } from './waits';

// What the routes use before a task's code loads: its first read, its address and the address check. No components
// here: the first load imports this file, and the task page comes through exports.ts with its route.
export { taskRefQuery, taskDetailQuery };
export { tabAddress as taskAddress, CHAT as CHAT_TAB } from './model';

/** The open task as its route loaded it, read from what is already there (never a read of its own, so the shell can ask
    on every page): the phone's Agent icon shows its agent, and comes up for a ❓ to the person in its chat (its waits
    name them, whatever its turn shows first: an approval it shares goes first there). */
export function useLoadedTask(param: string | undefined): { task: TaskData | undefined; asked: string[] } {
  const id = useQuery({ ...taskRefQuery(param ?? ''), enabled: false }).data;
  const task = useQuery({ ...taskDetailQuery(id), enabled: false }).data;
  const me = useQuery({ ...meQuery, enabled: false }).data?.id;
  return { task: param ? task : undefined, asked: param && task?.conversation_id ? questionsFor(task, me) : [] };
}

/** A closed task's board for `leaveTask` when nothing is behind it: its pipeline while that is still one, else none
    (Tasks). Read only when it is needed. */
export const boardOf =
  (client: QueryClient, pipeline: string | null | undefined) =>
  async (): Promise<string | null> => {
    if (!pipeline) return null;
    const live = await client.ensureQueryData(pipelinesQuery).catch(() => []);
    return live.some((p) => p.id === pipeline) ? pipeline : null;
  };

/** Load the task while the route loads its code, and with `openChat` the view its chat opens on: the comments, or the
    agent's session the address names (`?session=`). A refused read leaves the page to show the error. */
export async function loadTask(
  client: QueryClient,
  param: string,
  tab?: string,
  openChat = false,
  session?: unknown,
): Promise<{ task: TaskData; tab: string } | null> {
  try {
    const id = await client.ensureQueryData(taskRefQuery(param));
    const t = await client.ensureQueryData(taskDetailQuery(id));
    if (openChat && t.conversation_id) {
      // the view the chat opens on: the task's own thread shows its comments, or the agent's session the address names;
      // a task in its owner's main chat, that chat
      const cid = t.conversation_id;
      const view = typeof session === 'string' && session.startsWith('bot:') ? { session } : { comments: true as const };
      void client
        .fetchQuery(threadQuery(cid))
        .then((room) => client.prefetchInfiniteQuery(timelineQuery(cid, room.task_id === t.id ? view : null)))
        .catch(() => undefined);
    }
    const fileQuery = taskFilesQuery(id);
    const files = client.prefetchQuery(fileQuery);
    const tabs = tabsOf(t);
    // An email's OK hung on it waits on the person: it opens on the email (TaskPage's first tab).
    if (!tab && (await waitsOnYou(client, t))) {
      await files;
      return { task: t, tab: waitSlugs(1, tabsOf(t, client.getQueryData(fileQuery.queryKey)).map((x) => x.slug))[0]! };
    }
    const selected = tab ? tabs.find((x) => x.slug === tab) : tabs[0];
    // An attached file can outrank the default document when the task is waiting for an answer: the file the ask is
    // about (a PDF to sign) opens over a page it also shows, so its tab waits for the files.
    const target = targetOf(decisionOf(t, null)); // as tabsOf reads it: the ask that is not an email's
    const asked = selected?.mark === 'ask' && (!target || selected.doc?.file_id === target.id);
    const doc = selected?.doc && (tab || asked || !decisionOf(t)) ? selected.doc : undefined;
    if (doc && isPage(doc)) void client.prefetchQuery(pageShownQuery(id, doc.file_id));
    // Details includes the file list in its body, where inserting the section late shifts the content.
    if (selected && (doc || (tab === CHAT && selected.chat))) return { task: t, tab: selected.slug };
    await files;
    // The files did not load: what the ask is about still opens, read by its id (tabsOf's tab of its own).
    const read = client.getQueryData(fileQuery.queryKey) ?? (client.getQueryState(fileQuery.queryKey)?.status === 'error' ? [] : undefined);
    return { task: t, tab: tab ?? defaultTab(tabsOf(t, read)) };
  } catch {
    return null;
  }
}

/** Where `/task/<n>` goes: the task under its pipeline, on the tab its address names (`?tab=`) or the one loadTask
    opens, with the address's other words (an ask, a version, a thread beside it). Null when the task did not load. */
export async function ownAddress(client: QueryClient, param: string, search: Record<string, unknown>, openChat = false) {
  const { tab: named, ...rest } = search;
  const loaded = await loadTask(client, param, typeof named === 'string' && named ? named : undefined, openChat, rest.session);
  if (!loaded) return null;
  const { task, tab } = loaded;
  return {
    to: '/tasks/$pipeline/$task/$tab' as const,
    params: { pipeline: task.pipeline_id ?? 'all', task: String(task.number ?? task.id), tab },
    search: rest,
  };
}

/**
 * What a task's address may carry: a file opened from Details (`full` shows a picture full size), the version of the
 * open page (`v`) with the one its changes are marked against (`vs`), and an email's file opened from its Email tab
 * (`part`).
 */
export interface TaskSearch {
  file?: string;
  full?: string;
  v?: number;
  vs?: number;
  part?: number;
  /** The ask a card opened the task for (its requirement id): its tab, the version it pinned and its Approve. */
  ask?: string;
  /** The agent whose session the task's chat shows (`bot:<id>`); none: the comments. */
  session?: string;
}

const whole = (x: unknown): number => (typeof x === 'number' ? x : typeof x === 'string' && /^\d+$/.test(x) ? Number(x) : NaN);
const n = (x: unknown): number | undefined => {
  const v = whole(x);
  return Number.isInteger(v) && v > 0 ? v : undefined;
};

/** `validateSearch` for the task routes: keeps the words it knows, drops anything else. */
export function taskSearch(raw: Record<string, unknown>): TaskSearch {
  const out: TaskSearch = {};
  if (typeof raw.file === 'string' && raw.file) out.file = raw.file;
  if (typeof raw.full === 'string' && raw.full) out.full = raw.full;
  if (typeof raw.ask === 'string' && raw.ask) out.ask = raw.ask;
  if (typeof raw.session === 'string' && raw.session.startsWith('bot:')) out.session = raw.session;
  const v = n(raw.v);
  const vs = n(raw.vs);
  if (v) out.v = v;
  if (vs) out.vs = vs;
  // An email's files count from 0.
  const part = whole(raw.part);
  if (Number.isInteger(part) && part >= 0) out.part = part;
  return out;
}
