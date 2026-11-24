// The menu's list itself, not the actions index (that one loads this folder).
import { pages as menuPages } from '@/actions/copy';
import type { IconName } from '@/kit/Icon';
import { defineSearch } from './define';
import { HITS } from './helpers';
import { matches } from './results';

const ICONS: Record<string, IconName> = {
  tasks: 'tasks',
  calendar: 'calendar',
  threads: 'threads',
  updates: 'updates',
  company: 'company',
  library: 'library',
};

/* The main menu's pages by name: "finance" finds Company, Finance. Settings' own sections are their group. */
export const pages = defineSearch({
  group: 'Pages',
  order: 80,
  find: (q) => {
    if (!q.trim()) return [];
    return menuPages
      .filter(([to, title]) => to !== '/settings' && matches(title, q))
      .slice(0, HITS)
      .map(([to, title]) => {
        const icon = ICONS[to.split('/')[1] ?? ''];
        // "Company, Finance" is Finance by name
        const name = title.slice(title.lastIndexOf(', ') + 1).trim();
        return { key: `page:${to}`, title, name, ...(icon ? { lead: { icon } } : {}), run: { id: 'shell.goTo', input: { to } } };
      });
  },
});
