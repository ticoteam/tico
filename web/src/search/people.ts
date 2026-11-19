import { directoryQuery, queryClient } from '@/data';
import { defineSearch } from './define';
import { faceOf, HITS } from './helpers';
import { matches } from './results';

/* People and agents from the one directory: a hit opens their page. */
export const people = defineSearch({
  group: 'People',
  order: 50,
  find: async (q) => {
    if (!q.trim()) return [];
    const d = await queryClient.ensureQueryData(directoryQuery);
    return d.actors
      .filter((a) => matches(a.name, q))
      .slice(0, HITS)
      .map((a) => ({
        key: a.id,
        title: a.name,
        meta: a.team ?? a.title,
        lead: faceOf(a),
        run: { id: 'shell.goTo', input: { to: `/team/${encodeURIComponent(a.slug ?? a.id.slice(a.id.indexOf(':') + 1))}` } },
      }));
  },
});
