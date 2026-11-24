import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRouteWithContext, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import { Route as flowTask } from '@/routes/flow.$task';
import { Route as flowIndex } from '@/routes/flow.index';
import { Route as taskById } from '@/routes/task.$task';
import { Route as taskIndex } from '@/routes/tasks.$pipeline.$task.index';
import { taskDetailQuery, taskFilesQuery, taskRefQuery, type TaskData } from './queries';
import { TaskById } from './TaskPage';

/* Flow is gone: its old links land on Tasks (Needs you on top) and on the task's own address, with what they carried.
   Made-up rows. */

const task = { id: 't1', number: 12, pipeline_id: 'p1', title: 'Ship it', documents: [{ file_id: 'd1', name: 'Spec', kind: 'markdown', version: 1 }], to_leave: [], children: [], relations: {}, conversation_id: null, turn: null } as unknown as TaskData;

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
    taskById.update({ getParentRoute: () => root, id: '/task/$task', path: '/task/$task', component: () => null } as never),
    taskIndex.update({ getParentRoute: () => root, id: '/tasks/$pipeline/$task/', path: '/tasks/$pipeline/$task/', component: () => null } as never),
    page('/needs-you'),
    page('/tasks/'),
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
  it('/flow opens Tasks, where Needs you is on top; /flow?view=all opens All, keeping its view', async () => {
    const router = at('/flow');
    await router.load();
    expect(router.state.location.pathname).toBe('/tasks');
    const all = at('/flow?view=all');
    await all.load();
    expect(all.state.location.pathname).toBe('/needs-you');
    expect(all.state.location.search).toEqual({ view: 'all' });
  });

  it('/flow/<n> opens the task on the tab it named, keeping its ask, version and chat', async () => {
    const router = at('/flow/12?tab=details&ask=r1&v=2&chat=thread%3Ax');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({ ask: 'r1', v: 2, chat: 'thread:x' });
  });
});

describe('/task/<n>', () => {
  it('a link opened while another link to the same task is still preloading lands on its own address', async () => {
    const router = at('/needs-you');
    await router.load();
    const preload = router.preloadRoute({ href: '/task/12?tab=details&ask=r1&v=1&chat=thread%3Aa' } as never);
    await router.navigate({ href: '/task/12?tab=spec&ask=r2&v=2&chat=thread%3Ab' });
    await preload;
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/spec'));
    expect(router.state.location.search).toEqual({ ask: 'r2', v: 2, chat: 'thread:b' });
  });

  it('opens the tab its address names, not the first page', async () => {
    const router = at('/task/12?tab=details');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({});
  });
});

describe('/task/<n> read again after its route could not read it', () => {
  function shown(href: string) {
    const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({ component: Outlet });
    const byId = createRoute({ getParentRoute: () => root, path: '/task/$task', component: () => <TaskById task="12" /> });
    const own = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/$task/$tab', component: () => null });
    const needs = createRoute({ getParentRoute: () => root, path: '/needs-you', component: () => null });
    const router = createRouter({ routeTree: root.addChildren([byId, own, needs]), context: { queryClient }, history: createMemoryHistory({ initialEntries: [href] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it('goes to the same address the route would have: its tab, ask, version, file and thread kept', async () => {
    const router = shown('/task/12?tab=details&ask=r1&v=2&vs=1&file=f3&chat=thread%3Ax&via=thread');
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/details'));
    expect(router.state.location.search).toEqual({ ask: 'r1', v: 2, vs: 1, file: 'f3', chat: 'thread:x', via: 'thread' });
  });

  it('stays where the person went while it was reading', async () => {
    // Details waits for the task's files: they come only after the person has gone to Needs you
    queryClient.removeQueries({ queryKey: taskFilesQuery('t1').queryKey });
    let files!: () => void;
    vi.stubGlobal('fetch', (url: string) =>
      url.includes('/files')
        ? new Promise<Response>((done) => (files = () => done(new Response('{"files":[]}', { status: 200, headers: { 'Content-Type': 'application/json' } }))))
        : Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })),
    );
    const router = shown('/task/12?tab=details');
    await vi.waitFor(() => expect(files).toBeTypeOf('function'));
    await router.navigate({ href: '/needs-you' });
    files();
    await vi.waitFor(() => expect(queryClient.getQueryState(taskFilesQuery('t1').queryKey)?.status).toBe('success'));
    await act(async () => {});
    expect(router.state.location.pathname).toBe('/needs-you');
  });
});

describe('/tasks/<p>/<n>?tab=chat', () => {
  const wide = (w: number) => Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
  afterEach(() => wide(1024));

  // the route opens the first page at any width (as on main): the word stays in the address, where only a phone's
  // shell reads it (it brings the chat card up); a computer's chat is beside the page already
  it('on a computer opens the task’s first page, not the chat as the page', async () => {
    wide(1440);
    const router = at('/tasks/p1/12?tab=chat');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/spec'));
  });

  it('on a phone opens it too, the chat asked for kept in the address', async () => {
    wide(390);
    const router = at('/tasks/p1/12?tab=chat');
    await router.load();
    await vi.waitFor(() => expect(router.state.location.pathname).toBe('/tasks/p1/12/spec'));
    expect(new URLSearchParams(router.state.location.searchStr).get('tab')).toBe('chat');
  });
});
