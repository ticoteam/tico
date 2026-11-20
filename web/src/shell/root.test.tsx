import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Link, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient } from '@/data/query-client';
import { Route as root } from '@/routes/__root';
import { Route as landingRoute } from '@/routes/index';
import { resetDesktopReports, type TicoDesktop } from '@/desktop';
import { run } from '@/actions/run';
import type * as TasksRouting from '@/features/tasks/routing';
import { pipelineTasksQuery } from '@/features/tasks/routing';
import { forgetPlaces } from '@/lib';
import { router as appRouter } from '@/router';
import { showPageName } from '@/tico/pageName';
import { tabTitle, useTabTitle } from './title';

/* The root as the app draws it, with the features around the shell stood in: the second chat for Tico support, the
   desktop app's small call window, and what the page tells the desktop app. */

const { gate, q, catchUpRead, lists } = vi.hoisted(() => ({
  gate: { screen: null as string | null, setup: false },
  /** Each pipeline's open tasks, as its list read gives them. */
  lists: {} as Record<string, unknown[]>,
  q: <T,>(key: string, data: T) => ({ queryKey: [key], queryFn: async () => data, staleTime: Infinity }),
  catchUpRead: vi.fn(async () => ({ count: 0, items: [] })),
}));

// the router module only: the app's pages are not needed to know what it draws for a page that breaks
vi.mock('@/routeTree.gen', () => ({ routeTree: createRootRoute() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => undefined }));
vi.mock('@/tico', async (orig) => ({
  ...(await orig<object>()),
   TicoSheet: () => <aside aria-label="Tico">Tico</aside> }));
vi.mock('@/conversation', async (orig) => ({
  ...(await orig<object>()),
   ticoRoomQuery: { queryKey: ['tico-room'], queryFn: async () => 'room', staleTime: Infinity } }));
vi.mock('@/live', async (orig) => ({
  ...(await orig<object>()),
   startLive: vi.fn(), useLiveStatus: () => 'live' }));
vi.mock('@/data', async (orig) => ({
  ...(await orig<object>()),
  meQuery: {
    queryKey: ['me'],
    queryFn: async () => {
      if (gate.screen) throw new Error('signed out');
      return { id: 'human:maya', email: 'maya@example.com' };
    },
    staleTime: Infinity,
    retry: false,
  },
  configQuery: { queryKey: ['config'], queryFn: async () => ({ company_name: 'Fernhill', app_name: 'Tico', visual_origin: 'https://files.example.com' }), staleTime: Infinity },
  directoryQuery: { queryKey: ['directory'], queryFn: async () => ({ actors: [], byId: {} }), staleTime: Infinity },
  recordVisit: vi.fn(),
  useHeldCount: () => 0,
}));
// A feature's routing.ts is what the first load carries (the gate, counts, reads); its exports.ts the parts the root
// loads when they first have something to show.
vi.mock('@/features/start/exports', () => ({ FirstRun: () => null }));
vi.mock('@/features/start/routing', () => ({
  StartScreen: ({ screen }: { screen: string }) => <p>start: {screen}</p>,
  useStartGate: () => ({ screen: gate.screen, error: null, retry: () => {}, busy: false, firstRun: false }),
  landing: async () => (gate.screen ? null : { setup: gate.setup }),
}));
const call = vi.hoisted(() => ({ phase: 'idle' }));
vi.mock('@/features/calls/exports', () => ({
  CallBar: () => <div>call bar</div>,
  CallSide: () => null,
  RingCard: () => null,
  StartCall: ({ open }: { open: boolean }) => (open ? <p>start call</p> : null),
}));
vi.mock('@/features/calls/routing', () => ({
  ringingQuery: q('ringing', { calls: [] }),
  TOGGLE_MUTE: 'calls.toggleMute',
  useCall: () => ({ muted: false, phase: call.phase }),
  isCallActive: (c: { phase: string }) => c.phase === 'live',
}));
vi.mock('@/features/library/routing', () => ({ useFileOpen: () => false, takeHelperCode: (at: string) => at }));
vi.mock('@/features/tasks/routing', async () => {
  const real = await vi.importActual<typeof TasksRouting>('@/features/tasks/routing');
  return {
  // the real grouping: the open task's list draws Flow's sections from it
  flowGroups: real.flowGroups,
  // the real address checks: the list beside a task reads its pipeline with the filters its board had
  filtersOf: real.filtersOf,
  validateTasksSearch: real.validateTasksSearch,
  pipelineTasksQuery: vi.fn((pipeline: string, f: object) => ({
    queryKey: ['tasks', 'list', pipeline, JSON.stringify(f)],
    queryFn: async () => ({ tasks: lists[pipeline] ?? [], cursor: '' }),
    staleTime: Infinity,
  })),
  flowQuery: q('flow', { count: 4, items: [] }),
  pipelinesQuery: q('pipelines', []),
  roadmapPipeline: () => null,
  firstPipeline: (p: { id: string }[]) => p[0]?.id ?? null,
  };
});
vi.mock('@/features/team/exports', () => ({ TeammateChat: () => null }));
vi.mock('@/features/task/exports', () => ({ TaskChat: () => <p>task chat</p> }));
vi.mock('@/features/team/routing', () => ({ teamSlug: (t: string) => t, whoHref: () => '/', groupsQuery: q('groups', []) }));
vi.mock('@/features/threads/exports', () => ({
  ChatSheet: () => <p>a teammate chat</p>,
  MenuThreads: () => <p>the threads</p>,
  MessagePick: () => null,
  TeamThread: () => <p>the team thread</p>,
}));
vi.mock('@/features/threads/routing', async () => ({
  asksOf: (await vi.importActual<{ asksOf: unknown }>('@/features/threads/routing')).asksOf,
  catchUpQuery: { ...q('catch-up', { count: 0, items: [] }), queryFn: catchUpRead },
  threadListQuery: { queryKey: ['threads-list'], queryFn: async () => ({ rows: [], next: null }), initialPageParam: null, getNextPageParam: () => undefined, staleTime: Infinity },
}));
vi.mock('@/features/support/exports', () => ({ SupportChat: () => <p>support chat</p> }));
vi.mock('@/features/support/routing', () => ({ SUPPORT_CHAT: 'support', supportUnreadQuery: q('support-unread', 2) }));
vi.mock('@/pagekinds', async (orig) => ({
  ...(await orig<object>()),
   PanelHost: () => null, usePagePanelOpen: () => false }));
vi.mock('@/search', async (orig) => ({ ...(await orig<object>()), openSearch: vi.fn() }));
// Every press goes through the action layer as in the app; signing out stops there (it would leave the page).
vi.mock('@/actions/run', async (orig) => {
  const actual = await orig<{ run: typeof run }>();
  return { ...actual, run: vi.fn((id: string, input: unknown, opts: { via: 'click' }) => (id === 'settings.signOut' ? Promise.resolve({ ok: true }) : actual.run(id, input, opts))) };
});

const MAYA = { id: 'human:maya', kind: 'person', name: 'Maya Okafor' };
const QA = { id: 'bot:qa', kind: 'agent', name: 'QA Owner', slug: 'qa', team: 'Engineering' };
// the directory knows each teammate by their full id and their bare one, as buildDirectory does
const people = (...actors: { id: string }[]) =>
  queryClient.setQueryData(['directory'], { actors, byId: Object.fromEntries(actors.flatMap((a) => [[a.id, a], [a.id.slice(a.id.indexOf(':') + 1), a]])) });

function show(at: string) {
  const page = createRoute({ getParentRoute: () => root, path: '/', component: () => <main className="pane page"><h1>Home</h1></main> });
  const call = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <main className="pane cw">call page</main> });
  const task = createRoute({ getParentRoute: () => root, path: '/flow/$task', component: () => <main className="pane page"><h1>A task</h1></main> });
  const flow = createRoute({ getParentRoute: () => root, path: '/flow', component: () => <main className="pane page"><h1>Flow page</h1></main> });
  const editor = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/edit', component: () => <main className="pane page"><h1>Edit pipeline</h1></main> });
  // a board whose card opens task 7 as the board's own card does: under its pipeline
  const board = createRoute({
    getParentRoute: () => root,
    path: '/tasks/$pipeline',
    component: () => (
      <main className="pane page">
        <h1>Board</h1>
        <Link to="/tasks/$pipeline/$task" params={{ pipeline: 'eng', task: '7' }}>Bulk edit stock</Link>
      </main>
    ),
  });
  const open = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/$task', component: () => <main className="pane page"><h1>An open task</h1></main> });
  const router = createRouter({ routeTree: root.addChildren([page, call, task, flow, editor, board, open]), context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

function fakeDesktop() {
  const t = {
    version: '1.0.0',
    platform: 'mac',
    companies: {
      list: vi.fn(async () => [
        { id: 'c1', origin: 'http://x', name: 'Fernhill', email: 'maya@example.com', active: true, self: true, signedOut: false, needs: 0 },
        { id: 'c2', origin: 'http://y', name: 'Okafor Garden Tours', email: 'maya@example.com', active: false, self: false, signedOut: false, needs: 3 },
      ]),
      switchTo: vi.fn(),
      add: vi.fn(),
      remove: vi.fn(),
      reportSelf: vi.fn(),
      onChange: vi.fn(() => () => {}),
    },
    setBadge: vi.fn(),
    callWindow: { open: vi.fn(async () => {}), close: vi.fn(), setOnTop: vi.fn(), onState: vi.fn(() => () => {}) },
    app: { updateState: vi.fn(), restartToUpdate: vi.fn(), checkForUpdates: vi.fn(), showLog: vi.fn(), logTail: vi.fn(), openAtLogin: vi.fn() },
    setFilesOrigin: vi.fn(),
    reportCall: vi.fn(),
  } satisfies TicoDesktop;
  (window as { tico?: TicoDesktop }).tico = t;
  return t;
}

beforeEach(() => {
  gate.screen = null;
  gate.setup = false;
  catchUpRead.mockClear();
  queryClient.clear();
  forgetPlaces();
  for (const k of Object.keys(lists)) delete lists[k];
  resetDesktopReports();
});
afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
});

describe('the root', () => {
  it('defers the catch-up badge read while the first page is loading', async () => {
    let open!: () => void;
    const loading = new Promise<void>((resolve) => { open = resolve; });
    const page = createRoute({
      getParentRoute: () => root,
      path: '/slow',
      loader: () => loading,
      pendingComponent: () => <p>Opening page</p>,
      component: () => <p>Page opened</p>,
    });
    const router = createRouter({
      routeTree: root.addChildren([page]),
      context: { queryClient },
      history: createMemoryHistory({ initialEntries: ['/slow'] }),
      defaultPendingMs: 0,
      defaultPendingMinMs: 0,
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText('Opening page')).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeTruthy();
    expect(catchUpRead).not.toHaveBeenCalled();
    await act(async () => open());
    expect(await screen.findByText('Page opened')).toBeTruthy();
    await waitFor(() => expect(catchUpRead).toHaveBeenCalledTimes(1));
  });

  it('draws Tico support as the second chat for ?chat=support, and the menu shows its unread count', async () => {
    show('/?chat=support');
    const sheet = await screen.findByRole('complementary', { name: 'Tico support' });
    expect(await within(sheet).findByText('support chat')).toBeTruthy();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('chat');
    expect(await screen.findByRole('button', { name: 'Tico support, 2 unread' })).toBeTruthy();
  });

  it("draws the desktop's small call window as the call page alone: no Tico, menu or call bar", async () => {
    show('/call/abc?window=1');
    expect(await screen.findByText('call page')).toBeTruthy();
    expect(document.querySelector('.shell-callwin>.cw')).toBeTruthy();
    expect(document.querySelector('.shell-frame')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Main' })).toBeNull();
    expect(screen.queryByRole('complementary', { name: 'Tico' })).toBeNull();
    expect(screen.queryByText('call bar')).toBeNull();
  });

  it('draws a teammate’s chat as the second chat beside the page, from ?chat= @s1192', async () => {
    people(MAYA, QA);
    show('/?chat=bot:qa');
    const sheet = await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    expect(await within(sheet).findByText('a teammate chat')).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Home' })).toBeTruthy();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('chat');
  });

  it('your name at the menu’s foot opens the account menu: you, My page, Settings, the theme, Sign out @s1189 @s1206', async () => {
    people(MAYA, QA);
    show('/');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(await within(nav).findByRole('button', { name: 'Maya Okafor' }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getByText('maya@example.com')).toBeTruthy();
    expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['My page', 'Settings', 'Sign out']);
    expect(within(menu).getByRole('group', { name: 'Theme' })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Sign out' }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('settings.signOut', {}, { via: 'click' });
  });

  it('draws the call in the frame without ?window=1', async () => {
    show('/call/abc');
    expect(await screen.findByText('call page')).toBeTruthy();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('call');
  });
});

describe('the landing address', () => {
  function land(at: string) {
    const home = landingRoute.update({ getParentRoute: () => root, id: '/', path: '/' } as never);
    const board = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline', component: () => <main className="pane page"><h1>Engineering</h1></main> });
    const router = createRouter({ routeTree: root.addChildren([home as never, board]), context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it('lands on the Tasks board, with no Start page between', async () => {
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [] }]);
    const router = land('/');
    expect(await screen.findByRole('heading', { name: 'Engineering' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng');
    expect(router.state.location.search).toEqual({});
  });

  it('keeps the first run open over the board while setup is owed, and for the old welcome address @s1154', async () => {
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [] }]);
    gate.setup = true;
    const router = land('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng'));
    expect(router.state.location.search).toEqual({ setup: 'path' });
    gate.setup = false;
    const again = land('/?setup=path');
    await waitFor(() => expect(again.state.location.pathname).toBe('/tasks/eng'));
    expect(again.state.location.search).toEqual({ setup: 'path' });
  });
});

describe('an open task', () => {
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  const item = (n: number, title: string, stage: string, late = false) => ({ id: `t${n}`, number: n, title, pipeline_id: 'eng', stage_id: stage, owner_id: 'bot:qa', late, stuck: false, since: '2026-11-18T09:00:00Z' });
  afterEach(() => at(1024));

  it('in Flow has what waits on you in the menu’s place, by pipeline and stage, the open task chosen @s135', async () => {
    at(1600);
    people(MAYA, QA);
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [{ id: 'review', name: 'Review' }, { id: 'qa', name: 'QA' }], done: null }]);
    queryClient.setQueryData(['flow'], { count: 3, items: [item(7, 'Bulk edit stock', 'qa', true), item(5, 'Rate fallback', 'review'), item(9, 'Postal codes', 'qa')] });
    show('/flow/7');
    const list = await screen.findByRole('navigation', { name: 'Flow' });
    expect(within(list).getAllByRole('button', { name: /Engineering/ }).map((b) => b.textContent)).toEqual(['EngineeringReview1', 'EngineeringQA2']);
    const open = within(list).getByRole('link', { name: /Bulk edit stock/ });
    expect(open.classList.contains('is-on')).toBe(true);
    expect(open.getAttribute('href')).toBe('/flow/7');
    expect(within(open).getByText('Late')).toBeTruthy();
    expect(within(list).getByRole('link', { name: /Postal codes/ }).classList.contains('is-on')).toBe(false);
    // it says it is Flow, plainly
    expect(within(list).getByRole('button', { name: 'Flow' })).toBeTruthy();
    expect(within(list).getByText('Flow · 3')).toBeTruthy();
    expect(within(list).getByRole('button', { name: 'Exit Flow' }).getAttribute('title')).toBe('Exit Flow');
    expect(await screen.findByText('task chat')).toBeTruthy();
    // a stage's name folds its rows
    fireEvent.click(within(list).getByRole('button', { name: /EngineeringQA/ }));
    expect(within(list).queryByRole('link', { name: /Postal codes/ })).toBeNull();
  });

  it('opened from a thread, keeps that thread beside it in place of the task’s own chat (plan/layers.html)', async () => {
    at(1600);
    people(MAYA, QA);
    show('/flow/7?chat=agent%3Aqa&via=thread');
    const sheet = await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    expect(await within(sheet).findByText('a teammate chat')).toBeTruthy();
    expect(screen.queryByText('task chat')).toBeNull();
  });

  it('the pipeline editor is a page, not a task: no task chat and no Flow list', async () => {
    show('/tasks/eng/edit');
    expect(await screen.findByRole('heading', { name: 'Edit pipeline' })).toBeTruthy();
    expect(screen.queryByText('task chat')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Flow' })).toBeNull();
  });
});

describe('a task opened from a board', () => {
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  const ENG = { id: 'eng', name: 'Engineering', stages: [{ id: 'review', name: 'Review' }, { id: 'qa', name: 'QA' }], done: null };
  const row = (n: number, title: string, stage: string) => ({ id: `t${n}`, number: n, title, status: 'open', pipeline_id: 'eng', stage_id: stage, owner_id: 'bot:qa', labels: [], late: false, turn: null, updated_at: '2026-11-18T09:00:00Z', version: 1 });
  const waits = { id: 't9', number: 9, title: 'Postal codes', pipeline_id: 'eng', stage_id: 'qa', owner_id: 'bot:qa', late: false, stuck: false, since: '2026-11-18T09:00:00Z' };
  beforeEach(() => {
    at(1600);
    people(MAYA, QA);
    queryClient.setQueryData(['pipelines'], [ENG, { id: 'legal', name: 'Legal', stages: [], done: null }]);
    queryClient.setQueryData(['flow'], { count: 3, items: [waits] });
    lists.eng = [row(7, 'Bulk edit stock', 'qa'), row(5, 'Rate fallback', 'review')];
  });
  afterEach(() => at(1024));
  const board = async () => {
    const router = show('/tasks/eng?owner=bot%3Aqa');
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    return router;
  };

  it('opens in place: beside it the same pipeline, as its board was filtered, the task chosen, never Flow; Back is the board again', async () => {
    const router = await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    expect(await screen.findByRole('heading', { name: 'An open task' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng/7');
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    expect(screen.queryByRole('navigation', { name: 'Flow' })).toBeNull();
    expect(within(list).getByRole('button', { name: 'Engineering' })).toBeTruthy();
    expect(within(list).queryByRole('button', { name: 'Exit Flow' })).toBeNull();
    const open = await within(list).findByRole('link', { name: /Bulk edit stock/ });
    expect(open.classList.contains('is-on')).toBe(true);
    expect(open.getAttribute('href')).toBe('/tasks/eng/7');
    expect(within(list).getByRole('link', { name: /Rate fallback/ }).classList.contains('is-on')).toBe(false);
    expect(within(list).getAllByRole('button', { name: /^(Review|QA)/ }).map((b) => b.textContent)).toEqual(['Review1', 'QA1']);
    expect(vi.mocked(pipelineTasksQuery)).toHaveBeenLastCalledWith('eng', expect.objectContaining({ owner: 'bot:qa' }));
    // Flow is there to enter, not entered
    expect(within(list).getByRole('link', { name: 'Flow' }).getAttribute('aria-pressed')).toBe('false');
    expect(await screen.findByText('task chat')).toBeTruthy();
    act(() => router.history.back());
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
  });

  it('enters Flow only from its own play mark, and Escape leaves Flow for the task it was entered from', async () => {
    const router = await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    fireEvent.click(within(list).getByRole('link', { name: 'Flow' }));
    expect(await screen.findByRole('heading', { name: 'Flow page' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/flow');
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(await screen.findByRole('heading', { name: 'An open task' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng/7');
  });

  it('in Flow, × (Exit Flow) and Escape go back to the board it was entered from; Escape while typing stays', async () => {
    const router = await board();
    await act(() => router.navigate({ to: '/flow/$task', params: { task: '9' } }));
    const list = await screen.findByRole('navigation', { name: 'Flow' });
    expect(within(list).getByText('Flow · 3')).toBeTruthy();
    fireEvent.click(within(list).getByRole('button', { name: 'Exit Flow' }));
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });

    await act(() => router.navigate({ to: '/flow/$task', params: { task: '9' } }));
    await screen.findByRole('navigation', { name: 'Flow' });
    const field = document.body.appendChild(document.createElement('input'));
    fireEvent.keyDown(field, { key: 'Escape' });
    await act(async () => {});
    expect(router.state.location.pathname).toBe('/flow/9');
    field.remove();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
    // off Flow, Escape goes nowhere
    fireEvent.keyDown(document.body, { key: 'Escape' });
    await act(async () => {});
    expect(router.state.location.pathname).toBe('/tasks/eng');
  });

  it('Back from Flow is the board as it was', async () => {
    const router = await board();
    await act(() => router.navigate({ to: '/flow/$task', params: { task: '9' } }));
    await screen.findByRole('navigation', { name: 'Flow' });
    act(() => router.history.back());
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
  });
});

describe('the desktop app', () => {
  it('hears who is signed in, the Needs you count for the badge (following it), and the files origin', async () => {
    const t = fakeDesktop();
    show('/');
    await waitFor(() => expect(t.companies.reportSelf).toHaveBeenCalledWith({ name: 'Fernhill', email: 'maya@example.com', signedOut: false }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(4));
    expect(t.setFilesOrigin).toHaveBeenCalledWith('https://files.example.com');
    act(() => queryClient.setQueryData(['flow'], { count: 9, items: [] }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(9));
    act(() => queryClient.setQueryData(['flow'], { count: 0, items: [] }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(0));
    expect(t.companies.reportSelf).toHaveBeenCalledTimes(1);
  });

  it('signed out: the badge clears and the company row keeps its name and email, marked signed out', async () => {
    const t = fakeDesktop();
    gate.screen = 'signin';
    show('/');
    expect(await screen.findByText('start: signin')).toBeTruthy();
    await waitFor(() => expect(t.companies.reportSelf).toHaveBeenCalledWith({ name: 'Fernhill', email: 'maya@example.com', signedOut: true }));
    expect(t.setBadge).toHaveBeenCalledWith(0);
    expect(t.setBadge).not.toHaveBeenCalledWith(4);
  });

  it('hears that a call is live, so an update waits for it', async () => {
    const t = fakeDesktop();
    call.phase = 'live';
    try {
      show('/');
      await waitFor(() => expect(t.reportCall).toHaveBeenCalledWith(true));
    } finally {
      call.phase = 'idle';
    }
  });

  it('loads the call bar only while a call is on: the first load carries the shell alone', async () => {
    show('/');
    await screen.findByRole('navigation', { name: 'Main' });
    expect(screen.queryByText('call bar')).toBeNull();
    call.phase = 'live';
    try {
      show('/');
      expect(await screen.findByText('call bar')).toBeTruthy();
    } finally {
      call.phase = 'idle';
    }
  });

  it('the company at the menu’s head lists every company the app holds, and picking one switches the window @s1160', async () => {
    const t = fakeDesktop();
    show('/');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    await waitFor(() => expect(t.companies.list).toHaveBeenCalled());
    fireEvent.click(await within(nav).findByRole('button', { name: 'Fernhill' }));
    const menu = await screen.findByRole('menu');
    await within(menu).findByRole('menuitem', { name: /Okafor Garden Tours/ });
    expect(within(menu).getByRole('menuitem', { name: /Fernhill/ }).className).toContain('is-on');
    expect(within(menu).getByRole('menuitem', { name: /Okafor Garden Tours/ }).textContent).toContain('3');
    expect(within(menu).getByRole('menuitem', { name: /Add company/ })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Okafor Garden Tours/ }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('shell.switchCompany', { id: 'c2' }, { via: 'click' });
    await waitFor(() => expect(t.companies.switchTo).toHaveBeenCalledWith('c2'));
  });

  it('in a browser, the company pick holds this company only, with no Add company @s1160', async () => {
    show('/');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(await within(nav).findByRole('button', { name: 'Fernhill' }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['FFernhill']);
  });

  it('in a browser, nothing is reported and nothing breaks', async () => {
    show('/');
    expect(await screen.findByRole('heading', { name: 'Home' })).toBeTruthy();
  });
});

describe('a page that fails', () => {
  let quiet: MockInstance[] = [];
  beforeEach(() => {
    quiet = [vi.spyOn(console, 'error').mockImplementation(() => {}), vi.spyOn(console, 'warn').mockImplementation(() => {})];
  });
  afterEach(() => quiet.forEach((q) => q.mockRestore()));
  /** One page under a menu, drawn by a router with the app's own fault for a page. */
  function draw(component: () => ReactNode, loader?: () => void) {
    const top = createRootRoute({
      component: () => (
        <>
          <nav aria-label="Main">menu</nav>
          <Outlet />
        </>
      ),
    });
    const page = createRoute({ getParentRoute: () => top, path: '/', component, loader });
    const router = createRouter({
      routeTree: top.addChildren([page]),
      history: createMemoryHistory({ initialEntries: ['/'] }),
      defaultErrorComponent: appRouter.options.defaultErrorComponent,
    });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return screen.findByRole('main', { name: 'This page' });
  }

  it('shows its fault in the page’s place when it breaks while drawing; the menu stays, and Try again draws it again', async () => {
    let broken = true;
    const main = await draw(() => {
      if (broken) throw new TypeError('d.toISOString is not a function');
      return <h1>Routines</h1>;
    });
    expect(within(main).getByText('This page did not load')).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeTruthy();
    broken = false;
    fireEvent.click(within(main).getByRole('button', { name: 'Try again' }));
    expect(await screen.findByRole('heading', { name: 'Routines' })).toBeTruthy();
    expect(screen.queryByText('This page did not load')).toBeNull();
  });

  it('shows no access, without Try again, when the server refuses it', async () => {
    const main = await draw(
      () => <h1>Finance</h1>,
      () => {
        throw new ApiError(403, 'forbidden', 'Not yours');
      },
    );
    expect(within(main).queryByRole('button', { name: 'Try again' })).toBeNull();
    expect(within(main).queryByText('This page did not load')).toBeNull();
  });
});

describe('the browser tab’s title', () => {
  it('names the open page when its route draws, and when its head renames it; streamed words do not re-read it', async () => {
    function Shell() {
      useTabTitle();
      return <Outlet />;
    }
    const top = createRootRoute({ component: Shell });
    const routes = ['Finance', 'Strategy'].map((name) =>
      createRoute({ getParentRoute: () => top, path: `/${name.toLowerCase()}`, component: () => <main className="pane page"><h1>{name}</h1></main> }),
    );
    const router = createRouter({ routeTree: top.addChildren(routes), history: createMemoryHistory({ initialEntries: ['/finance'] }) });
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(document.title).toBe('Finance - Tico'));
    await act(() => router.navigate({ to: '/strategy' as never }));
    await waitFor(() => expect(document.title).toBe('Strategy - Tico'));
    // a word streamed into the page changes nothing; a rename in the head says so
    const h1 = screen.getByRole('heading', { name: 'Strategy' });
    act(() => void (h1.textContent = 'Plan'));
    expect(document.title).toBe('Strategy - Tico');
    act(() => showPageName('Plan'));
    expect(document.title).toBe('Plan - Tico');
  });

  it('is plain Tico with no page name, and never uses a middle dot', () => {
    expect(tabTitle('')).toBe('Tico');
    expect(tabTitle('Tasks')).toBe('Tasks - Tico');
    expect(tabTitle('Tasks')).not.toContain('·');
  });
});
