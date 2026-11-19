import { fmtDate } from '@/lib';
import { defineSearch } from '@/search/define';
import { fetchFor, HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { meetingsQuery } from './queries';

/* Cmd+K: shared meetings from the server's search (the same read Calendar's List makes for its words), those whose
   title holds the words first. A hit opens it beside List. */
export const meetingsSearch = defineSearch({
  group: 'Meetings',
  order: 30,
  remote: true,
  find: async (q, signal) => {
    const words = q.trim().slice(0, 200);
    if (!words) return [];
    const { meetings } = await fetchFor(meetingsQuery('live', words), signal);
    const byTitle = meetings.filter((m) => matches(m.title, words));
    return [...byTitle, ...meetings.filter((m) => !byTitle.includes(m))].slice(0, HITS).map((m) => {
      const at = m.started || m.created;
      return {
        key: `meeting:${m.id}`,
        title: m.title,
        meta: at ? fmtDate(at) : undefined,
        lead: { icon: 'video' as const },
        run: { id: 'calls.openMeeting', input: { id: m.id } },
      };
    });
  },
});
