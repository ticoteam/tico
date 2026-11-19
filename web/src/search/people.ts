import { directoryQuery, queryClient } from '@/data';
import { defineSearch } from './define';
import { matches } from './results';

const initials = (name: string) => {
  const w = name.trim().split(/\s+/).filter(Boolean);
  return (w.length > 1 ? `${w[0]![0]}${w[1]![0]}` : (w[0] ?? '').slice(0, 2)).toUpperCase();
};

/* People and agents from the one directory: a hit opens their page. */
export const people = defineSearch({
  group: 'People',
  order: 50,
  find: async (q) => {
    if (!q.trim()) return [];
    const d = await queryClient.ensureQueryData(directoryQuery);
    return d.actors
      .filter((a) => matches(a.name, q))
      .slice(0, 8)
      .map((a) => ({
        key: a.id,
        title: a.name,
        meta: a.team ?? a.title,
        lead: { face: { initials: initials(a.name), hue: a.hue ?? 0, bot: a.kind === 'agent', name: a.name } },
        run: { id: 'shell.goTo', input: { to: `/team/${encodeURIComponent(a.slug ?? a.id.slice(a.id.indexOf(':') + 1))}` } },
      }));
  },
});
