import { directoryQuery, meQuery, queryClient } from '@/data';
import { fmtAge } from '@/lib';
import { defineSearch, type SearchHit } from '@/search/define';
import { faceOf, fetchFor, HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { isDm, othersOf, rowsOf, titleOf } from './model';
import { threadListQuery, threadSearchQuery } from './queries';

/** Messages asked of the server for one search: enough that a chatty thread still leaves room for others. */
const MESSAGES = 30;

/* Cmd+K: threads and DMs by the name each shows, from the list already read (newest first). */
export const threadsSearch = defineSearch({
  group: 'Threads',
  order: 20,
  find: async (q) => {
    if (!q.trim()) return [];
    const [list, me, directory] = await Promise.all([
      queryClient.ensureInfiniteQueryData(threadListQuery),
      queryClient.ensureQueryData(meQuery).catch(() => undefined),
      queryClient.ensureQueryData(directoryQuery).catch(() => undefined),
    ]);
    const nameOf = (actor: string) => directory?.byId[actor]?.name;
    const out = [];
    for (const t of rowsOf(list)) {
      const title = titleOf(t, me?.id, nameOf);
      if (!matches(title, q)) continue;
      const at = t.last_message_at || t.created_at;
      const other = isDm(t) ? directory?.byId[othersOf(t, me?.id)[0] ?? ''] : undefined;
      out.push({
        key: `thread:${t.id}`,
        title,
        meta: at ? fmtAge(at) : undefined,
        lead: other ? faceOf(other) : { icon: t.task_id ? ('tasks' as const) : ('threads' as const) },
        run: { id: 'threads.open', input: { thread: t.id } },
      });
      if (out.length === HITS) break;
    }
    return out;
  },
});

/* The rest of the Threads group, from the server: any thread the viewer may read, however old, by what was said in
   it. One row a thread, its newest matching message: the words found (marked), the thread's name at the right and the
   sender's face. A thread the list already found by name stays that row (search's rank drops the second). */
export const threadMessagesSearch = defineSearch({
  group: 'Threads',
  order: 21,
  remote: true,
  find: async (q, signal) => {
    const words = q.trim().slice(0, 200);
    if (!words) return [];
    const [found, directory] = await Promise.all([
      fetchFor(threadSearchQuery(words, MESSAGES), signal),
      queryClient.ensureQueryData(directoryQuery).catch(() => undefined),
    ]);
    const seen = new Set<string>();
    const out: SearchHit[] = [];
    for (const h of found.hits) {
      if (seen.has(h.thread_id)) continue;
      seen.add(h.thread_id);
      const sender = h.sender_id ? directory?.byId[h.sender_id] : undefined;
      const title = h.title?.trim();
      out.push({
        key: `thread:${h.thread_id}`,
        title: h.excerpt.replace(/\s+/g, ' ').trim(),
        ...(title && { name: title, meta: title }),
        lead: sender ? faceOf(sender) : { icon: 'threads' },
        run: { id: 'threads.open', input: { thread: h.thread_id } },
      });
      if (out.length === HITS) break;
    }
    return out;
  },
});
