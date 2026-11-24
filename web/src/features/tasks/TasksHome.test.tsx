import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRouteWithContext, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient } from '@/data';
import { Route as flowRoute } from '@/routes/flow.index';
import { Route as needsRoute } from '@/routes/needs-you';
import { Route as tasksRoute } from '@/routes/tasks.index';

vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

/* Tasks opens with Needs you on top and the pipelines below it; Needs you's other views (All, Snoozed, Done) are off its
   head, and it has no page of its own. Made-up rows. */

const ME = 'human:me';
const brief = (id: string, number: number, title: string, pipeline: string) => ({
  id,
  number,
  title,
  pipeline_id: pipeline,
  stage_id: 's1',
  owner_id: 'bot:qa',
  late: false,
  stuck: false,
  stage_since: '2026-11-20T09:00:00Z',
});
const ask = (task: ReturnType<typeof brief>) => ({ id: `v1:${task.id}`, kind: 'ask', task_id: task.id, line: 'Ship it?', since: '2026-11-20T09:00:00Z', task });
const PIPELINES = [
  { id: 'eng', name: 'Engineering', stages: [{ id: 's1', name: 'Inbox' }] },
  { id: 'ops', name: 'Ops', stages: [{ id: 's1', name: 'Inbox' }] },
];
let needs: unknown[] = [];
let pipes: unknown[] = PIPELINES;
// Needs you's views: claimable work (All), the person's snoozed and done tasks, and the asks they snoozed
const row = (id: string, number: number, title: string) => ({ ...brief(id, number, title, 'eng'), status: 'open', labels: [], turn: null, updated_at: '2026-11-20T09:00:00Z', version: 1 });
const SNOOZED_ASK = { ...ask(brief('t9', 9, 'Pay the plumber', 'eng')), line: 'Approve the transfer', snoozed_until: '2099-01-01T00:00:00Z' };
let snoozedAsks: unknown[] = [];
let posts: { url: string; body: unknown }[] = [];
// how Needs you's own read answers: as it is, never (still on its way), or refused
let needsRead: 'ok' | 'hang' | 'fail' = 'ok';

function show(at: string) {
  const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        <Outlet />
      </>
    ),
  });
  const routeTree = root.addChildren([
    tasksRoute.update({ getParentRoute: () => root, id: '/tasks/', path: '/tasks/' } as never),
    needsRoute.update({ getParentRoute: () => root, id: '/needs-you', path: '/needs-you' } as never),
    flowRoute.update({ getParentRoute: () => root, id: '/flow/', path: '/flow/' } as never),
  ]);
  const router = createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

beforeAll(() => queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } }));
beforeEach(() => {
  needs = [ask(brief('t7', 7, 'Ship the export', 'eng')), ask(brief('t12', 12, 'Renew the lease', 'ops'))];
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  snoozedAsks = [SNOOZED_ASK];
  posts = [];
  needsRead = 'ok';
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const u = new URL(String(url), 'http://x');
    const q = u.searchParams;
    if (init?.method === 'POST') {
      posts.push({ url: u.pathname, body: JSON.parse(String(init.body)) });
      if (u.pathname === '/api/v2/needs-you/unsnooze') snoozedAsks = [];
    }
    const tasks = (list: unknown[]) => ({ tasks: list, cursor: 'c1', next_after: null });
    if (u.pathname === '/api/v2/needs-you' && q.has('limit') && needsRead !== 'ok') {
      if (needsRead === 'hang') return new Promise<Response>(() => {});
      return new Response(JSON.stringify({ error: { code: 'forbidden', detail: 'No.' } }), { status: 403, headers: { 'Content-Type': 'application/json' } });
    }
    const body =
      u.pathname === '/api/v2/needs-you' && q.has('limit')
        ? { actor: ME, items: needs, next: null }
        : u.pathname === '/api/v2/needs-you/snoozed'
          ? { items: snoozedAsks }
          : u.pathname === '/api/v2/pipelines'
            ? { pipelines: pipes }
            : u.pathname === '/api/v2/tasks'
              ? q.get('claimable')
                ? tasks([row('t20', 20, 'Sort the inbox')])
                : q.get('owner') === ME && q.get('snoozed') === 'only'
                  ? tasks([row('t21', 21, 'Call the plumber')])
                  : q.get('owner') === ME && q.get('status') === 'done'
                    ? tasks([{ ...row('t22', 22, 'Pay the invoice'), status: 'done' }])
                    : tasks([])
              : {};
    return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  pipes = PIPELINES;
  cleanup();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.layout;
});

describe('Tasks', () => {
  const opens = async () => {
    expect(await screen.findByRole('heading', { name: 'Tasks', level: 1 })).toBeTruthy();
    const top = await screen.findByRole('region', { name: 'Needs you' });
    const pipelines = screen.getByRole('region', { name: 'Pipelines' });
    // Needs you first, then the pipelines
    expect(top.compareDocumentPosition(pipelines) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(top.querySelector('.stage-n')?.textContent).toBe('2');
    expect(within(top).getByRole('link', { name: /Ship the export/ }).getAttribute('href')).toBe('/task/7');
    expect(within(top).getByRole('link', { name: /Renew the lease/ }).getAttribute('href')).toBe('/task/12');
    expect(within(pipelines).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Engineering', '/tasks/eng'],
      ['Ops', '/tasks/ops'],
    ]);
  };

  it('opens with Needs you on top and the pipelines below it, on its own address', async () => {
    const router = show('/tasks');
    await opens();
    expect(router.state.location.pathname).toBe('/tasks');
  });

  it('on a phone, Needs you is the top of the same stack', async () => {
    document.documentElement.dataset.layout = 'cards';
    show('/tasks');
    await opens();
  });

  it('with nothing waiting on you, Needs you is its head alone, its views still a press away', async () => {
    needs = [];
    show('/tasks');
    expect(await screen.findByRole('region', { name: 'Pipelines' })).toBeTruthy();
    const top = screen.getByRole('region', { name: 'Needs you' });
    expect(top.querySelector('.stage-n')).toBeNull();
    expect(within(top).getByText('Nothing needs you')).toBeTruthy();
    expect(within(top).getAllByRole('link').map((a) => a.textContent)).toEqual(['All', 'Snoozed', 'Done']);
  });

  it('with no pipelines and nothing waiting, Needs you still links its views, Snoozed and Done keeping tasks on no pipeline', async () => {
    needs = [];
    pipes = [];
    show('/tasks');
    const top = await screen.findByRole('region', { name: 'Needs you' });
    expect(within(top).getAllByRole('link').map((a) => a.textContent)).toEqual(['All', 'Snoozed', 'Done']);
    expect(screen.getByRole('heading', { name: 'No pipelines yet' })).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Pipelines' })).toBeNull();
  });

  it('its head links All, Snoozed and Done, each its own view; Unsnooze brings a snoozed ask back', async () => {
    const views = [
      ['All', 'all', 'Sort the inbox'],
      ['Snoozed', 'snoozed', 'Call the plumber'],
      ['Done', 'done', 'Pay the invoice'],
    ] as const;
    for (const [name, view, title] of views) {
      const router = show('/tasks');
      const top = await screen.findByRole('region', { name: 'Needs you' });
      fireEvent.click(within(within(top).getByRole('navigation', { name: 'Needs you views' })).getByRole('link', { name }));
      await waitFor(() => expect(router.state.location.pathname).toBe('/needs-you'));
      expect(router.state.location.search).toEqual({ view });
      expect(await screen.findByTitle(title)).toBeTruthy();
      if (view === 'snoozed') {
        const asks = await screen.findByRole('region', { name: 'Asks' });
        expect(within(asks).getByTitle('Approve the transfer')).toBeTruthy();
        fireEvent.click(within(asks).getByRole('button', { name: 'Unsnooze' }));
        await waitFor(() => expect(screen.queryByTitle('Approve the transfer')).toBeNull());
        expect(posts.filter((p) => p.url !== '/api/v2/needs-you/sort')).toEqual([{ url: '/api/v2/needs-you/unsnooze', body: { id: SNOOZED_ASK.id } }]);
      }
      cleanup();
    }
  }, 20_000);

  it('a plain old Needs you or Flow address opens Tasks; one that names a view opens that view', async () => {
    for (const at of ['/needs-you', '/flow', '/needs-you?view=mine']) {
      const router = show(at);
      await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
      expect(await screen.findByRole('region', { name: 'Needs you' })).toBeTruthy();
      cleanup();
    }
    for (const at of ['/needs-you?view=all', '/flow?view=all']) {
      const router = show(at);
      await waitFor(() => expect(router.state.location.pathname).toBe('/needs-you'));
      expect(router.state.location.search).toEqual({ view: 'all' });
      expect(await screen.findByTitle('Sort the inbox')).toBeTruthy();
      cleanup();
    }
    // an open chat in the old address stays open
    for (const [at, to, search] of [
      ['/needs-you?chat=agent%3Alo', '/tasks', { chat: 'agent:lo' }],
      ['/flow?chat=agent%3Alo', '/tasks', { chat: 'agent:lo' }],
      ['/flow?view=all&chat=agent%3Alo', '/needs-you', { view: 'all', chat: 'agent:lo' }],
    ] as const) {
      const router = show(at);
      await waitFor(() => expect(router.state.location.pathname).toBe(to));
      expect(router.state.location.search).toEqual(search);
      cleanup();
    }
  });

  it('shows the pipelines at once, Needs you filling on its own; a failed Needs you read says so there alone', async () => {
    needsRead = 'hang';
    show('/tasks');
    const pipelines = await screen.findByRole('region', { name: 'Pipelines' });
    expect(within(pipelines).getAllByRole('link').map((a) => a.textContent)).toEqual(['Engineering', 'Ops']);
    expect(within(screen.getByRole('region', { name: 'Needs you' })).getAllByRole('status').map((el) => el.textContent)).toContain('Loading');
    cleanup();
    queryClient.clear();
    queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
    queryClient.setQueryData(['directory'], { actors: [], byId: {} });
    needsRead = 'fail';
    show('/tasks');
    const top = await screen.findByRole('region', { name: 'Needs you' });
    expect(await within(top).findByRole('button', { name: 'Try again' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Pipelines' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Tasks', level: 1 })).toBeTruthy();
    needsRead = 'ok';
    fireEvent.click(within(top).getByRole('button', { name: 'Try again' }));
    expect(await within(top).findByRole('link', { name: /Ship the export/ })).toBeTruthy();
  });
});
