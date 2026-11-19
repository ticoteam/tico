import type { AnyRouter } from '@tanstack/react-router';
import { openSearch } from '@/search';
import { bound } from './bind';
import * as copy from './copy';
import { s } from './schema';
import { defineAction } from './types';

/* The base set every page shares. Navigation pushes (Back undoes Tico's moves); filters replace (architecture.md 3). */

const path = s.string({ min: 1, max: 300, pattern: /^\/[A-Za-z0-9\-._~%/]*$/, what: 'a path inside the app, like /tasks/engineering' });

/** The address without the app's base, so targets and areas read the same before and after the switch. */
const here = (router: AnyRouter) => router.state.location.pathname.replace(/^\/next(?=\/|$)/, '') || '/';
const nameOf = (to: string) => copy.pages.find(([p]) => p === to)?.[1] ?? to;

export const goTo = defineAction({
  id: 'shell.goTo',
  title: copy.goTo,
  kind: 'view',
  input: s.object({ to: path }),
  run: ({ router }, { to }) => router.navigate({ to }),
  show: ({ router }, { to }) => router.navigate({ to }),
  did: ({ to }) => copy.didGoTo(nameOf(to)),
  // motion.md 5, case 5: the flight goes to the new page's title.
  target: ({ to }) => ({ kind: 'page', id: to }),
  offer: () => copy.pages.map(([to, title]) => ({ input: { to }, title })),
});

const chat = s.string({
  max: 200,
  pattern: /^(agent|person|thread):[^\s]+$/,
  what: 'agent:<slug>, person:<id> or thread:<id>',
});

export const openChat = defineAction({
  id: 'shell.openChat',
  title: copy.openChat,
  kind: 'view',
  input: s.object({ chat }),
  run: ({ router }, { chat }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, chat }) }),
  show: ({ router }, { chat }) =>
    router.navigate({ to: '.', search: (prev: Record<string, unknown>) => ({ ...prev, chat }) }),
  did: () => copy.didOpenChat,
  target: ({ chat }) => ({ kind: 'chat', id: chat }),
});

export const closeChat = defineAction({
  id: 'shell.closeChat',
  title: copy.closeChat,
  kind: 'view',
  input: s.object({}),
  can: ({ router }) => !!(router.state.location.search as Record<string, unknown>).chat,
  run: ({ router }) =>
    router.navigate({ to: '.', search: ({ chat: _, ...rest }: Record<string, unknown>) => rest }),
  did: () => copy.didCloseChat,
});

const sheet = s.oneOf(['tico', 'menu', 'list', 'widgets', 'side'] as const);

/** `folded` left out flips it, which is what the keys do. Never touches history. */
export const fold = defineAction({
  id: 'shell.fold',
  title: copy.fold,
  kind: 'view',
  input: s.object({ sheet, folded: s.optional(s.boolean()) }),
  run: (_ctx, { sheet, folded }) => {
    if (!bound.fold) throw new Error('the sheets are not drawn yet');
    bound.fold(sheet, folded);
  },
  did: ({ sheet, folded }) => copy.didFold(copy.sheetName[sheet] ?? sheet, folded),
  target: ({ sheet }) => ({ kind: 'sheet', id: sheet }),
  keys: { '[': { sheet: 'menu' }, ']': { sheet: 'widgets' }, 'mod+j': { sheet: 'tico' } },
});

export const openTask = defineAction({
  id: 'task.open',
  title: copy.openTask,
  kind: 'view',
  input: s.object({ task: s.number({ int: true, min: 1 }), pipeline: s.optional(s.string({ min: 1, max: 100 })) }),
  run: ({ router }, { task, pipeline }) =>
    // Without a pipeline the deep-link route finds it and redirects with replace (architecture.md 3).
    router.navigate({ to: pipeline ? `/tasks/${encodeURIComponent(pipeline)}/${task}` : `/task/${task}` }),
  show: ({ router }, { task, pipeline }) =>
    router.navigate({ to: pipeline ? `/tasks/${encodeURIComponent(pipeline)}/${task}` : `/task/${task}` }),
  did: ({ task }) => copy.didOpenTask(task),
  target: ({ task }) => ({ kind: 'task', id: task }),
});

/** Merges into the route's search; null removes a key. The route's `validateSearch` has the last word. */
export const filter = defineAction({
  id: 'page.filter',
  title: copy.filter,
  kind: 'view',
  input: s.object({ search: s.record(s.nullable(s.string({ max: 200 }))) }),
  run: async ({ router }, { search }) => {
    const area = here(router).split('/')[1] || 'page';
    await router.navigate({
      to: '.',
      replace: true,
      search: (prev: Record<string, unknown>) => {
        const next: Record<string, unknown> = { ...prev };
        for (const [k, v] of Object.entries(search)) {
          if (v === null) delete next[k];
          else next[k] = v;
        }
        return next;
      },
    });
    return area;
  },
  did: () => copy.didFilter,
  target: (_input, area) => (area ? { kind: area, id: 'filter' } : null),
});

export const search = defineAction({
  id: 'search.open',
  title: copy.search,
  kind: 'view',
  input: s.object({ q: s.optional(s.string({ max: 200 })) }),
  run: (_ctx, { q }) => openSearch(q),
  did: ({ q }) => copy.didSearch(q),
  target: () => ({ kind: 'search', id: 'field' }),
  keys: { 'mod+k': {} },
});
