import { defineSearch } from '@/search/define';
import { HITS } from '@/search/helpers';
import { matches } from '@/search/results';
import { NAV } from './sections';

/* Cmd+K: each Settings section by name ("billing", or "settings" for all of them), shown as "Settings, Billing". */
export const settingsSearch = defineSearch({
  group: 'Settings',
  order: 85,
  find: (q) => {
    if (!q.trim()) return [];
    return NAV.filter((n) => n !== null)
      .map(([section, name]) => ({ section, title: `Settings, ${name}` }))
      .filter((s) => matches(s.title, q))
      .slice(0, HITS)
      .map(({ section, title }) => ({
        key: `settings:${section}`,
        title,
        name: title.slice(title.indexOf(', ') + 2),
        lead: { icon: 'gear' as const },
        run: { id: 'settings.open', input: { section } },
      }));
  },
});
