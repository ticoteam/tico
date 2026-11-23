import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRouteWithContext, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Route as flowTask } from '@/routes/flow.$task';
import { Route as flowIndex } from '@/routes/flow.index';
import { Route as taskById } from '@/routes/task.$task';
import { taskDetailQuery, taskFilesQuery, taskRefQuery, type TaskData } from './queries';
import { TaskById } from './TaskPage';

/* Flow is gone: its old links land on Needs you and on the task's own address, with what they carried. Made-up rows. */

const task = { id: 't1', number: 12, pipeline_id: 'p1', title: 'Ship it', documents: [{ file_id: 'd1', name: 'Spec', kind: 'markdown', version: 1 }], to_leave: [], children: [], relations: {}, conversation_id: null, turn: null } as unknown as TaskData;

function at(href: string) {
  const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({ component: Outlet });
  const page = (path: string) => createRoute({ getParentRoute: () => root, path, component: () => null });
  const routeTree = root.addChildren([
    flowIndex.update({ getParentRoute: () => root, id: '/flow/', path: '/flow/' } as never),
    flowTask.update({ getParentRoute: () => root, id: '/flow/$task', path: '/flow/$task' } as never),
    taskById.update({ getParentRoute: () => root, id: '/task/$task', path: '/task/$task', component: () => null } as never),
    page('/needs-you'),
    page('/tasks/$pipeline/$task/$tab'),
  ]);
  return createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: [href] }) });
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(taskRefQuery('12').queryKey, 't1');
  queryClient.setQueryData(taskDetailQuery('t1').queryKey, task);
  queryClient.setQueryData(taskFilesQuery('t1').queryKey, []);
  vi.stubGlobal('fetch', async () => new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
});
afterEach(() => vi.unstubAllGlobals());

describe('an old Flow link', () => {
  it('/flow opens Needs you, keeping its view', async () => {
    const router = at('/flow?view=all');
    await router.load();
    expect(router.state.location.pathname).toBe('/needs-you');
    expect(router.state.location.search).toEqual({ view: 'all' });
  });

  it('/flow/<n> opens the task on the tab it named, keeping its ask, version and chat', async () => {
    const router = at('/flow/12?tab=details&ask=r1&v=2&chat=thread%3Ax');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({ ask: 'r1', v: 2, chat: 'thread:x' });
  });
});

describe('/task/<n>', () => {
  it('opens the tab its address names, not the first page', async () => {
    const router = at('/task/12?tab=details');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({});
  });
});

describe('/task/<n> read again after its route could not read it', () => {
  it('goes to the same address the route would have: its tab, ask, version, file and thread kept', async () => {
    const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({ component: Outlet });
    const byId = createRoute({ getParentRoute: () => root, path: '/task/$task', component: () => <TaskById task="12" /> });
    const own = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/$task/$tab', component: () => null });
    const router = createRouter({
      routeTree: root.addChildren([byId, own]),
      context: { queryClient },
      history: createMemoryHistory({ initialEntries: ['/task/12?tab=details&ask=r1&v=2&vs=1&file=f3&chat=thread%3Ax&via=thread'] }),
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({ ask: 'r1', v: 2, vs: 1, file: 'f3', chat: 'thread:x', via: 'thread' });
  });
});
