import { createMemoryHistory, createRootRouteWithContext, createRoute, createRouter, Outlet } from '@tanstack/react-router';
import { describe, expect, it } from 'vitest';
import { queryClient } from '@/data';
import { Route as flowTask } from '@/routes/flow.$task';
import { Route as flowIndex } from '@/routes/flow.index';
import { Route as taskById } from '@/routes/task.$task';
import { Route as taskTab } from '@/routes/tasks.$pipeline.$task.$tab';
import { Route as taskIndex } from '@/routes/tasks.$pipeline.$task.index';

/* A task's old addresses (a link in a message, a notice, a bookmark, a new tab) open the task over a page (plan: the
   task modal): over its board when the address names its pipeline, else over Tasks, with Needs you as its list. */

function at(href: string) {
  // as the app's root does: it reads only its own words, and a page's pass through
  const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({
    component: Outlet,
    validateSearch: (q: Record<string, unknown>): { chat?: string } => (typeof q.chat === 'string' ? { chat: q.chat } : {}),
  });
  const page = (path: string) => createRoute({ getParentRoute: () => root, path, component: () => null });
  const routeTree = root.addChildren([
    flowIndex.update({ getParentRoute: () => root, id: '/flow/', path: '/flow/' } as never),
    flowTask.update({ getParentRoute: () => root, id: '/flow/$task', path: '/flow/$task' } as never),
    taskById.update({ getParentRoute: () => root, id: '/task/$task', path: '/task/$task' } as never),
    taskIndex.update({ getParentRoute: () => root, id: '/tasks/$pipeline/$task/', path: '/tasks/$pipeline/$task/' } as never),
    taskTab.update({ getParentRoute: () => root, id: '/tasks/$pipeline/$task/$tab', path: '/tasks/$pipeline/$task/$tab' } as never),
    page('/needs-you'),
    page('/tasks/'),
    page('/tasks/$pipeline/'),
  ]);
  return createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: [href] }) });
}

describe('an old Flow link', () => {
  it('/flow opens Tasks, where Needs you is on top; /flow?view=all opens All, keeping its view', async () => {
    const router = at('/flow');
    await router.load();
    expect(router.state.location.pathname).toBe('/tasks');
    const all = at('/flow?view=all');
    await all.load();
    expect(all.state.location.pathname).toBe('/needs-you');
    expect(all.state.location.search).toEqual({ view: 'all' });
  });
});

describe("a task's old address", () => {
  it.each([
    ['/task/12', '/tasks', { task: 12 }],
    ['/task/12?tab=details&ask=r1&v=2', '/tasks', { task: 12, 'task.tab': 'details', 'task.ask': 'r1', 'task.v': 2 }],
    ['/flow/12?tab=details&ask=r1&v=2&chat=thread%3Ax', '/tasks', { task: 12, 'task.tab': 'details', 'task.ask': 'r1', 'task.v': 2, chat: 'thread:x' }],
    ['/tasks/p1/12', '/tasks/p1', { task: 12, list: 'p1' }],
    ['/tasks/p1/12?tab=chat', '/tasks/p1', { task: 12, list: 'p1', 'task.tab': 'chat' }],
    ['/tasks/p1/12/details?file=f3&v=2&vs=1', '/tasks/p1', { task: 12, list: 'p1', 'task.tab': 'details', 'task.file': 'f3', 'task.v': 2, 'task.vs': 1 }],
    ['/tasks/all/12/chat?session=bot%3Aqa', '/tasks', { task: 12, 'task.tab': 'chat', 'task.session': 'bot:qa' }],
  ])('%s opens the task over its page, its words kept', async (href, path, search) => {
    const router = at(href);
    await router.load();
    expect(router.state.location.pathname).toBe(path);
    expect(router.state.location.search).toEqual(search);
  });

  it('replaces itself: Back from the task goes where the person was before the link', async () => {
    const router = at('/needs-you');
    await router.load();
    await router.navigate({ href: '/task/12' });
    expect(router.state.location.pathname).toBe('/tasks');
    router.history.back();
    await router.load();
    expect(router.state.location.pathname).toBe('/needs-you');
  });
});
