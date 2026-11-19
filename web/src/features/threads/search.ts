import { directoryQuery, meQuery, queryClient } from '@/data';
import { fmtAge } from '@/lib';
import { defineSearch } from '@/search/define';
import { faceOf, HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { isDm, othersOf, rowsOf, titleOf } from './model';
import { threadListQuery } from './queries';

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
