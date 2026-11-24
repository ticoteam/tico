import { QueryClientProvider } from '@tanstack/react-query';
import { createBrowserHistory, createMemoryHistory, createRootRoute, createRoute, createRouter, Link, Outlet, redirect, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Suspense, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi, type MockInstance } from 'vitest';
import { ApiError, api } from '@/api/client';
import { queryClient } from '@/data/query-client';
import { Route as root } from '@/routes/__root';
import { Route as landingRoute } from '@/routes/index';
import { resetDesktopReports, type TicoDesktop } from '@/desktop';
import { run } from '@/actions/run';
import type * as TasksRouting from '@/features/tasks/routing';
import { loadMore, pipelineTasksQuery, stagePriorityQuery, useStageSort } from '@/features/tasks/routing';
import { TasksHome } from '@/features/tasks/exports';
import { forgetPlaces, holdRow } from '@/lib';
import { dispatch, installPatchers } from '@/live/patch';
import { useFollow } from '@/live/follow';
import { router as appRouter } from '@/router';
import { showPageName } from '@/tico/pageName';
import { foldSheet } from './Frame';
import { tabTitle, useTabTitle } from './title';

/* The root as the app draws it, with the features around the shell stood in: the second chat for Tico support, the
   desktop app's small call window, and what the page tells the desktop app. */

const { gate, q, catchUpRead, lists, pages, ranked, rankFails, listFails, owners, asks, hops, needsRead, shellDraws } = vi.hoisted(() => ({
  /** Needs you read as the app reads it (GET /needs-you), where a test serves it: else the stand-in below. */
  needsRead: { real: false },
  /** Where `/task/<n>` passes on to, as its route does once the task is read: none unless a test names one. */
  hops: {} as Record<string, string>,
  /** The ❓ questions and asks agents put to Maya that wait (`asks`, from Needs you's items). */
  asks: [] as { id: string; kind?: 'question' | 'ask'; by: string; pipeline: string | null; href: string | null; task?: string; wait?: string }[],
  gate: { screen: null as string | null, setup: false },
  /** Each page's owner, as GET /page-owners answers: none unless a test names one. */
  owners: {} as Record<string, { bot: string | null; own?: boolean }>,
  /** Each pipeline's open tasks, as its list read gives them. */
  lists: {} as Record<string, unknown[]>,
  /** More of a pipeline's first read where a test names it: `next_after`, the counts. */
  pages: {} as Record<string, Record<string, unknown>>,
  /** A stage's open tasks in Tico's order (its read by priority), where a test names it: else its list read's rows. */
  ranked: {} as Record<string, unknown[]>,
  /** How many of the next reads by priority fail (a 503, not tried again). */
  rankFails: { n: 0 },
  /** How many of the next pipeline list reads fail (a 503, not tried again). */
  listFails: { n: 0 },
  q: <T,>(key: string, data: T) => ({ queryKey: [key], queryFn: async () => data, staleTime: Infinity }),
  catchUpRead: vi.fn(async () => ({ count: 0, items: [] })),
  /** How many times the shell drew itself (it asks whether a file is open, once a draw). */
  shellDraws: { n: 0 },
}));

// the router module only: the app's pages are not needed to know what it draws for a page that breaks
vi.mock('@/routeTree.gen', () => ({ routeTree: createRootRoute() }));
vi.mock('@/live/follow', () => ({ useFollow: vi.fn(), follow: vi.fn(() => () => {}) }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
const power = vi.hoisted(() => ({ now: undefined as { available: boolean } | undefined }));
vi.mock('@/tico/power', () => ({ useTicoPower: () => power.now }));
vi.mock('@/tico', async (orig) => ({
  ...(await orig<object>()),
   TicoSheet: () => <aside aria-label="Tico">Tico</aside>,
   // tico/Intro.test.tsx tests the intros
   PageIntro: () => null }));
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
  pageOwnersQuery: { queryKey: ['settings', 'page-owners'], queryFn: async () => ({ pages: owners, canChange: false }), staleTime: Infinity },
}));
// A feature's routing.ts is what the first load carries (the gate, counts, reads); its exports.ts the parts the root
// loads when they first have something to show.
vi.mock('@/features/start/exports', () => ({ SetupChat: () => null }));
vi.mock('@/features/start/routing', () => ({
  StartScreen: ({ screen }: { screen: string }) => <p>start: {screen}</p>,
  useStartGate: () => ({ screen: gate.screen, error: null, retry: () => {}, busy: false }),
  useSetupOwed: () => gate.setup,
  useSetupTranscript: () => [],
  landing: async () => (gate.screen ? null : { setup: gate.setup }),
}));
const call = vi.hoisted(() => ({ phase: 'idle' }));
vi.mock('@/features/calls/exports', () => ({
  CallBar: () => <div>call bar</div>,
  CallSide: ({ ticoBeside }: { ticoBeside?: boolean }) => <p>{ticoBeside ? 'Tico beside the call' : 'the call alone'}</p>,
  RingCard: () => null,
  StartCall: ({ open }: { open: boolean }) => (open ? <p>start call</p> : null),
}));
vi.mock('@/features/calls/routing', () => ({
  ringingQuery: q('ringing', { calls: [] }),
  callQuery: (id: string) => q(`call:${id}`, { id, title: 'Pricing review' }),
  TOGGLE_MUTE: 'calls.toggleMute',
  useCall: () => ({ muted: false, phase: call.phase }),
  isCallActive: (c: { phase: string }) => c.phase === 'live',
}));
vi.mock('@/features/library/routing', () => ({ useFileOpen: () => (shellDraws.n++, false), takeHelperCode: (at: string) => at }));
vi.mock('@/features/tasks/routing', async () => {
  const real = await vi.importActual<typeof TasksRouting>('@/features/tasks/routing');
  return {
  useFlowCountRefresh: real.useFlowCountRefresh,
  flowCountQuery: real.flowCountQuery,
  // the Dot beside an open task: what Needs you names
  useNeedsYou: real.useNeedsYou,
  waitsOnMe: real.waitsOnMe,
  needsYouIds: real.needsYouIds,
  needsYouLeft: real.needsYouLeft,
  needsYouWithout: real.needsYouWithout,
  isNeedsYouRead: real.isNeedsYouRead,
  leaveNeedsYou: real.leaveNeedsYou,
  settleNeedsYou: real.settleNeedsYou,
  needsYouRows: real.needsYouRows,
  useNeedsYouDrawn: real.useNeedsYouDrawn,
  useNeedsYouRead: real.useNeedsYouRead,
  // the real address checks: the list beside a task reads its pipeline with the filters its board had
  filtersOf: real.filtersOf,
  validateTasksSearch: real.validateTasksSearch,
  inOrder: real.inOrder,
  isFiltered: real.isFiltered,
  sortOf: real.sortOf,
  stageCount: real.stageCount,
  loadListTools: real.loadListTools,
  loadMore: vi.fn(async () => {}),
  // the real key (a stage by priority), its rows served here
  stagePriorityQuery: vi.fn((pipeline: string, stage: string, f: TasksRouting.Filters) => ({
    ...real.stagePriorityQuery(pipeline, stage, f),
    queryFn: async () => {
      if (rankFails.n > 0) {
        rankFails.n -= 1;
        throw new ApiError(503, 'starting', 'Starting');
      }
      return { tasks: ranked[stage] ?? (lists[pipeline] ?? []).filter((r) => (r as { stage_id: string }).stage_id === stage), cursor: '' };
    },
    staleTime: Infinity,
  })),
  useStageSort: vi.fn(() => false),
  pipelineTasksQuery: vi.fn((pipeline: string, f: object) => ({
    queryKey: ['tasks', 'list', pipeline, JSON.stringify(f)],
    queryFn: async () => {
      if (listFails.n > 0) {
        listFails.n -= 1;
        throw new ApiError(503, 'starting', 'Starting');
      }
      return { tasks: lists[pipeline] ?? [], cursor: '', ...pages[pipeline] };
    },
    staleTime: Infinity,
  })),
  flowQuery: {
    queryKey: ['flow'],
    queryFn: async (ctx: never) => (needsRead.real ? real.flowQuery.queryFn!(ctx) : { count: 4, items: [], asks: [...asks] }),
    staleTime: Infinity,
  },
  flowSnoozedAsksQuery: real.flowSnoozedAsksQuery,
  pipelinesQuery: q('pipelines', []),
  roadmapPipeline: () => null,
  firstPipeline: (p: { id: string }[]) => p[0]?.id ?? null,
  };
});
vi.mock('@/features/team/exports', () => ({
  TeammateChat: () => null,
  AddTeammate: ({ team }: { team: string }) => <p>add teammate onto {team}</p>,
  TeamAccess: () => null,
}));
// the task modal a stand-in, drawn while the address names a task (features/task tests it)
vi.mock('@/features/task/exports', async () => {
  const { useLocation } = await import('@tanstack/react-router');
  return { TaskModal: () => (useLocation({ select: (l) => 'task' in (l.search as object) }) ? <p>task modal</p> : null) };
});
vi.mock('@/features/team/routing', () => ({
  whoHref: () => '/',
  groupsQuery: q('groups', []),
  teammateAccessQuery: () => q('team-access', null),
  validateTeamSearch: (s: { add?: unknown }) => (s.add === 'pick' ? { add: 'pick' } : {}),
}));
vi.mock('@/features/threads/exports', () => ({
  ChatSheet: () => <p>a teammate chat</p>,
  MenuThreads: () => <p>the threads</p>,
  MessagePick: () => null,
  TeamThread: () => <p>the team thread</p>,
  threadQuery: (id: string) => q(`thread:${id}`, null),
}));
vi.mock('@/features/threads/routing', async () => ({
  asksOf: (await vi.importActual<{ asksOf: unknown }>('@/features/threads/routing')).asksOf,
  threadQuery: (id: string) => q(`thread:${id}`, null),
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

function show(at: string, browser = false, opened?: (p: { pipeline: string; task: string; tab?: string }) => ReactNode) {
  const page = createRoute({ getParentRoute: () => root, path: '/', component: () => <main className="pane page"><h1>Home</h1></main> });
  const call = createRoute({ getParentRoute: () => root, path: '/call/$room', component: () => <main className="pane cw">call page</main> });
  const calendar = createRoute({ getParentRoute: () => root, path: '/calendar', component: () => <main className="pane page"><h1>Calendar page</h1></main> });
  const task = createRoute({
    getParentRoute: () => root,
    path: '/task/$task',
    beforeLoad: ({ params }) => {
      if (hops[params.task]) throw redirect({ href: hops[params.task], replace: true });
    },
    component: () => <main className="pane page"><h1>A task</h1></main>,
  });
  // Tasks itself: Needs you on top, then the pipelines
  const tasks = createRoute({ getParentRoute: () => root, path: '/tasks/', component: () => <Suspense fallback={null}><TasksHome /></Suspense> });
  const needs = createRoute({ getParentRoute: () => root, path: '/needs-you', component: () => <main className="pane page"><h1>Needs you page</h1></main> });
  const editor = createRoute({ getParentRoute: () => root, path: '/tasks/$pipeline/edit', component: () => <main className="pane page"><h1>Edit pipeline</h1></main> });
  // a board whose card opens task 7 as the board's own card does: under its pipeline
  const board = createRoute({
    getParentRoute: () => root,
    path: '/tasks/$pipeline',
    component: () => (
      <main className="pane page">
        <h1>Board</h1>
        <Link to="/tasks/$pipeline/$task" params={{ pipeline: 'eng', task: '7' }} data-list="eng">
          Bulk edit stock
        </Link>
      </main>
    ),
  });
  // an open task: a stand-in, or the page a test hands in (`opened`)
  const open = createRoute({
    getParentRoute: () => root,
    path: '/tasks/$pipeline/$task',
    component: function OpenTask() {
      const p = open.useParams();
      return opened ? opened(p) : <main className="pane page"><h1>An open task</h1></main>;
    },
  });
  const tab = createRoute({
    getParentRoute: () => root,
    path: '/tasks/$pipeline/$task/$tab',
    component: function OpenTab() {
      const p = tab.useParams();
      return opened ? opened(p) : <main className="pane page"><h1>An open task</h1></main>;
    },
  });
  const finance = createRoute({ getParentRoute: () => root, path: '/company/finance', component: () => <main className="pane page"><h1>Finance</h1></main> });
  const router = createRouter({ routeTree: root.addChildren([page, call, calendar, task, tasks, needs, editor, board, open, tab, finance]),
    context: { queryClient },
    // the window's own history where a test needs what the phone's cards push onto it (shell/phone PhoneCards)
    history: browser ? (window.history.replaceState(null, '', at), createBrowserHistory()) : createMemoryHistory({ initialEntries: [at] }),
  });
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
  queryClient.setQueryData(['flow', 'count'], { count: 4, wakeAt: null });
  forgetPlaces();
  for (const k of Object.keys(lists)) delete lists[k];
  for (const k of Object.keys(pages)) delete pages[k];
  for (const k of Object.keys(ranked)) delete ranked[k];
  rankFails.n = 0;
  listFails.n = 0;
  for (const k of Object.keys(owners)) delete owners[k];
  for (const k of Object.keys(hops)) delete hops[k];
  needsRead.real = false;
  asks.length = 0;
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

  it('builds the Team tab from who reports to whom, opens it to the team ?team= names, and Add teammate onto it from ?add=', async () => {
    localStorage.clear();
    const em = { id: 'bot:em', kind: 'agent', name: 'Engineering Manager', slug: 'em', teamId: 'eng', reportsTo: 'human:maya' };
    const qa = { ...QA, teamId: 'eng', reportsTo: 'bot:em' };
    const tool = { id: 'bot:tico', kind: 'agent', name: 'Assistant', slug: 'assistant', assistant: true, reportsTo: 'human:maya' };
    const actors = [MAYA, em, qa, tool];
    queryClient.setQueryData(['directory'], {
      actors,
      byId: Object.fromEntries(actors.map((a) => [a.id, a])),
      teams: [{ id: 'eng', name: 'Engineering', parent: '', order: 0, lead: '' }],
    });
    show('/?team=eng&add=pick');
    expect(await screen.findByText('add teammate onto eng')).toBeTruthy();
    const nav = within(await screen.findByRole('navigation', { name: 'Main' }));
    // your direct report, opened down to the team the address names; you and the Assistant are no rows
    expect(nav.getByRole('link', { name: 'Engineering Manager' }).getAttribute('href')).toBe('/');
    expect(await nav.findByRole('link', { name: 'QA Owner' })).toBeTruthy();
    expect(nav.queryByRole('link', { name: 'Assistant' })).toBeNull();
    localStorage.clear();
  });

  /* concept D (Nov 23): the menu's foot moved into its head. The pick is the company and you, and opens one menu. */
  it('the company and you at the menu’s head open one menu: Companies, this one’s settings, you with the theme, Sign out @s1189 @s1206', async () => {
    people(MAYA, QA);
    const router = show('/');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    const pick = await within(nav).findByRole('button', { name: /^Fernhill/ });
    expect(pick.textContent).toContain('Maya Okafor');
    // no foot: no name, gear or "?" under the list; the "?" is in the head
    expect(document.querySelector('.nav-foot, .m-foot')).toBeNull();
    expect(within(nav).queryByRole('link', { name: 'Settings' })).toBeNull();
    expect(within(nav).getByRole('button', { name: /^Tico support/ }).closest('.nav-head')).toBeTruthy();
    fireEvent.click(pick);
    const menu = await screen.findByRole('menu');
    const rows = [...menu.querySelectorAll('.menu-group-label, [role="menuitem"], [role="separator"], [role="group"][aria-label="Theme"]')].map((e) =>
      e.getAttribute('role') === 'separator' ? '—' : e.getAttribute('aria-label') === 'Theme' ? 'theme' : e.textContent,
    );
    expect(rows).toEqual(['Companies', 'FFernhill', 'Fernhill settings', '—', 'MOMaya Okaformaya@example.com', 'theme', 'Sign out']);
    expect(within(menu).queryByText('My page')).toBeNull();
    // the theme: three icons, each named
    for (const [name, theme] of [['Light', 'light'], ['Auto', 'system'], ['Dark', 'dark']] as const) {
      fireEvent.click(within(menu).getByRole('button', { name }));
      expect(vi.mocked(run)).toHaveBeenCalledWith('settings.setTheme', { theme }, { via: 'click' });
    }
    // your row: your own settings, in Settings' Account section
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Maya Okafor/ }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('shell.goTo', { to: '/settings/account' }, { via: 'click' });
    fireEvent.click(pick);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Sign out' }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('settings.signOut', {}, { via: 'click' });
    // the company's settings
    fireEvent.click(pick);
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Fernhill settings' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings'));
  });

  it('draws the call in the frame without ?window=1', async () => {
    show('/call/abc');
    expect(await screen.findByText('call page')).toBeTruthy();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('call');
    // the menu is on the row, at its edge, and the trail leads back up: the company, the calendar, the call's own title
    expect(document.querySelector('.shell-place[data-sheet="menu"]')).toBeTruthy();
    const trail = screen.getByRole('navigation', { name: 'Breadcrumb' });
    await vi.waitFor(() => expect(trail.textContent).toContain('Pricing review'));
    expect(within(trail).getByRole('link', { name: 'Calendar' }).getAttribute('href')).toBe('/calendar');
    expect(within(trail).getAllByRole('link')[0]?.getAttribute('href')).toBe('/');
  });

  it('tells the call’s side when Tico’s chat shows beside it: Tico open and on, not folded, not off', async () => {
    // wide enough for Tico, the call and its side side by side
    Object.defineProperty(window, 'innerWidth', { value: 1440, configurable: true });
    window.dispatchEvent(new Event('resize'));
    localStorage.clear();
    const open = async () => {
      show('/call/abc');
      expect(await screen.findByText('the call alone')).toBeTruthy(); // Tico folds by default on a call
      fireEvent.keyDown(document, { key: 'j', metaKey: true });
      await waitFor(() => expect(document.querySelector('.shell-place[data-sheet="tico"]')?.hasAttribute('data-shut')).toBe(false));
    };
    try {
      await open();
      expect(await screen.findByText('Tico beside the call')).toBeTruthy();
      cleanup();
      localStorage.clear();
      power.now = { available: false };
      await open(); // open, but off: the sheet says so, not the chat
      expect(screen.getByText('the call alone')).toBeTruthy();
    } finally {
      power.now = undefined;
      localStorage.clear();
      Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
      window.dispatchEvent(new Event('resize'));
    }
  });

  it('on a phone, a call is the page: no card of its own, no trail over it, the bar under it', async () => {
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true });
    window.dispatchEvent(new Event('resize'));
    try {
      show('/call/abc');
      const call = (await screen.findAllByText('call page')).find((el) => el.closest('.phone > .pg'));
      expect(call).toBeTruthy();
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(screen.queryByRole('navigation', { name: 'Breadcrumb' })).toBeNull();
      expect(within(screen.getByRole('navigation', { name: 'Bar' })).getByRole('button', { name: 'Company' }).getAttribute('aria-current')).toBe('true');
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
      window.dispatchEvent(new Event('resize'));
    }
  });
});

describe('the Agent bar @4857', () => {
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  const bar = () => document.querySelector<HTMLElement>('.shell-place[data-sheet="chat"]');
  const edge = () => screen.queryByRole('button', { name: 'Open QA Owner' });
  beforeEach(() => localStorage.clear());
  afterEach(() => at(1024));

  it("is the page owner's chat, open on a wide window, and a fold kept for that page and that person", async () => {
    at(1440);
    people(MAYA, QA);
    owners.tasks = { bot: 'qa' };
    show('/tasks/eng');
    const sheet = await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    expect(within(sheet).getByText('a teammate chat')).toBeTruthy();
    expect(bar()?.dataset.shut).toBeUndefined();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('chat');
    // its head's fold control, as the chat draws it (stood in here): the shell's fold
    act(() => foldSheet('chat', true));
    expect(await screen.findByRole('button', { name: 'Open QA Owner' })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem('tico-next:owner-bar.v1') ?? '{}')).toEqual({ 'human:maya': { tasks: true } });
  });

  it('is folded to its named edge under 1200 with no chat drawn in it, and opens from it @4857-S8', async () => {
    at(1100);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    show('/tasks/eng');
    await screen.findByRole('complementary', { name: 'Chat with QA Owner', hidden: true });
    await waitFor(() => expect(edge()).toBeTruthy());
    expect(bar()?.dataset.shut).toBe('true');
    // folded, it opens no chat: no DM is made and nothing is marked read
    expect(screen.queryByText('a teammate chat', { ignore: false })).toBeNull();
    fireEvent.click(edge()!);
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
    expect(await screen.findByText('a teammate chat')).toBeTruthy();
  });

  it('opens by itself when a question from the owner waits on you, until you fold it again @4857-S9', async () => {
    at(1100);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa', own: true };
    asks.push({ id: 'n1', by: 'bot:other', pipeline: null, href: '/threads/other' }, { id: 'n2', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' });
    show('/tasks/eng');
    await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
    // the question may wait outside this DM (its Home, a task's chat): one line goes to it
    expect((await screen.findByRole('link', { name: 'A question waits on you' })).getAttribute('href')).toBe('/threads/home-qa');
    act(() => foldSheet('chat', true));
    await waitFor(() => expect(edge()).toBeTruthy());
    // folded away, it does not open by itself again; opened by hand while the question waits, the line is there @4857-S9
    fireEvent.click(edge()!);
    expect(await screen.findByRole('link', { name: 'A question waits on you' })).toBeTruthy();
  });

  it('opens by itself again for a new question after one was folded away, as each is its own Needs you item @4857-S9', async () => {
    at(1100);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa', own: true };
    asks.push({ id: 'q1', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' });
    show('/tasks/eng');
    await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
    act(() => foldSheet('chat', true));
    await waitFor(() => expect(edge()).toBeTruthy());
    act(() => queryClient.setQueryData(['flow'], { count: 4, items: [], asks: [{ id: 'q1', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' }] }));
    expect(edge()).toBeTruthy();
    act(() =>
      queryClient.setQueryData(['flow'], {
        count: 4,
        items: [],
        asks: [
          { id: 'q1', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' },
          { id: 'q2', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' },
        ],
      }),
    );
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
  });

  it("counts a pipeline's own agent as its page's for a question with no task @4857-S26", async () => {
    at(1100);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:gm', own: false };
    asks.push({ id: 'n3', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' });
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:qa', owner: null }]);
    show('/tasks/eng');
    await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
  });

  it("opens only for an ask on this page: not where its agent stands in, and a task's ask on its pipeline's page @4857-N1", async () => {
    at(1100);
    people(MAYA, QA);
    // the agent only stands in for this page's owner: its question in its own chat does not open the bar here
    owners.tasks = { bot: 'bot:qa', own: false };
    asks.push({ id: 'n4', by: 'bot:qa', pipeline: null, href: null });
    show('/tasks/eng');
    await waitFor(() => expect(edge()).toBeTruthy());
    expect(bar()?.dataset.shut).toBe('true');
    cleanup();
    queryClient.clear();
    people(MAYA, QA);
    // an ask on a task in Engineering opens Engineering's page, the pipeline's agent being QA
    asks.length = 0;
    asks.push({ id: 'n5', kind: 'ask', by: 'bot:qa', pipeline: 'eng', href: '/task/t1' });
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:qa', owner: null }]);
    show('/tasks/eng');
    await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    await waitFor(() => expect(bar()?.dataset.shut).toBeUndefined());
    // an OK is named as one, its link to the task it waits on
    expect((await screen.findByRole('link', { name: 'An OK waits on you' })).getAttribute('href')).toBe('/task/t1');
  });

  it('under 1200 follows only the saved fold: opened where the page would go under its floor, it is a card over the page, and stays open on reload @4857-S4', async () => {
    at(820);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    show('/tasks/eng');
    await waitFor(() => expect(edge()).toBeTruthy());
    expect(document.documentElement.dataset.layout).toBe('sheets');
    fireEvent.click(edge()!);
    const lip = () => document.querySelector('.tabbar button[aria-label="QA Owner"]');
    await waitFor(() => expect(lip()?.getAttribute('aria-current')).toBe('true'));
    expect(document.documentElement.dataset.layout).toBe('cards');
    expect(JSON.parse(localStorage.getItem('tico-next:owner-bar.v1') ?? '{}')).toEqual({ 'human:maya': { tasks: false } });
    // a reload: the card is up from the saved fold, with nothing pressed
    cleanup();
    queryClient.clear();
    people(MAYA, QA);
    show('/tasks/eng');
    await waitFor(() => expect(lip()?.getAttribute('aria-current')).toBe('true'));
  });

  it("is the pipeline's first agent that is set up and on the team, never a planned or gone one, before the page's owner @4857-S7", async () => {
    at(1440);
    const em = { id: 'bot:em', kind: 'agent', name: 'Engineering Manager', slug: 'em', lifecycle: 'planned' };
    const ops = { id: 'bot:ops', kind: 'agent', name: 'Ops', slug: 'ops', lifecycle: 'active' };
    people(MAYA, QA, em, ops);
    owners.tasks = { bot: 'bot:qa' };
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:em', owner: 'bot:gone' }]);
    show('/tasks/eng');
    expect(await screen.findByRole('complementary', { name: 'Chat with QA Owner' })).toBeTruthy();
    cleanup();
    queryClient.clear();
    people(MAYA, QA, em, ops);
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:em', owner: 'bot:ops' }]);
    show('/tasks/eng');
    expect(await screen.findByRole('complementary', { name: 'Chat with Ops' })).toBeTruthy();
  });

  it("on a phone is the Agent icon, with the page's fold: raising it and putting it down are kept @4857-S3", async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    show('/tasks/eng');
    const icon = await screen.findByRole('button', { name: 'QA Owner' });
    expect(icon.getAttribute('aria-current')).toBeNull();
    fireEvent.click(icon);
    await waitFor(() => expect(JSON.parse(localStorage.getItem('tico-next:owner-bar.v1') ?? '{}')).toEqual({ 'human:maya': { tasks: false } }));
    expect(await screen.findByText('a teammate chat')).toBeTruthy();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    await waitFor(() => expect(JSON.parse(localStorage.getItem('tico-next:owner-bar.v1') ?? '{}')).toEqual({ 'human:maya': { tasks: true } }));
  });

  // an OK on a task in Engineering, its pipeline's agent being QA: Needs you's items as the server gives them
  const okOnTask = () => {
    asks.push({ id: 'n5', kind: 'ask', by: 'bot:qa', pipeline: 'eng', href: '/task/t1', task: 't1', wait: 'r1' });
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:qa', owner: null }]);
  };
  const askRead = async () => {
    await waitFor(() => expect(queryClient.getQueryData<{ asks: unknown[] }>(['flow'])?.asks).toHaveLength(1));
    await act(async () => {});
  };

  const okLine = () => screen.queryByRole('link', { name: 'An OK waits on you' });

  it('drops its line to the OK the moment the person approves it, before the server answers', async () => {
    at(1440);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    okOnTask();
    show('/tasks/eng');
    await askRead();
    expect(await screen.findByRole('link', { name: 'An OK waits on you' })).toBeTruthy();
    // the approve is on its way: no reply yet, and so no read of Needs you either
    const post = vi.spyOn(api, 'post').mockReturnValue(new Promise(() => {}));
    onTestFinished(() => post.mockRestore());
    act(() => void run('task.approve', { task: 't1', requirement: 'r1', shown: null }, { via: 'click' }));
    await waitFor(() => expect(okLine()).toBeNull());
  });

  it('drops its line to the OK live, when the stream says it left Needs you (another tab, or the agent took it back)', async () => {
    at(1440);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    okOnTask();
    show('/tasks/eng');
    await askRead();
    expect(await screen.findByRole('link', { name: 'An OK waits on you' })).toBeTruthy();
    asks.length = 0; // settled on the server
    // the event alone: the read again it also asks for never goes out here
    installPatchers();
    const read = vi.spyOn(queryClient, 'invalidateQueries').mockResolvedValue();
    onTestFinished(() => read.mockRestore());
    act(() => dispatch('needs', [{ seq: 41, count: 3, changed: [], removed: ['n5'] }]));
    await waitFor(() => expect(okLine()).toBeNull());
  });

  it('on a phone keeps the board up when an OK from the owner waits on you: the ask stays behind the Agent icon', async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    okOnTask();
    show('/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    const icon = await screen.findByRole('button', { name: 'QA Owner' });
    await askRead();
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('none');
    expect(icon.getAttribute('aria-current')).toBeNull();
    // raised by hand, the line to the OK is there
    fireEvent.click(icon);
    expect((await screen.findByRole('link', { name: 'An OK waits on you' })).getAttribute('href')).toBe('/task/t1');
  });

  it('a phone widened to 780, still a phone until 800, keeps the board up when an OK from the owner waits', async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    okOnTask();
    show('/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    await askRead();
    at(780);
    await act(async () => {});
    expect(document.documentElement.dataset.layout).toBe('cards');
    expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('none');
    expect(document.querySelector('.tabbar button[aria-label="QA Owner"]')?.getAttribute('aria-current')).toBeNull();
  });

  it('at 820, a computer, an OK from the owner still brings the bar up, as a card over the page', async () => {
    at(820);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    okOnTask();
    show('/tasks/eng');
    await askRead();
    const lip = () => document.querySelector('.tabbar button[aria-label="QA Owner"]');
    await waitFor(() => expect(lip()?.getAttribute('aria-current')).toBe('true'));
    expect(document.documentElement.dataset.layout).toBe('cards');
    expect(await screen.findByRole('link', { name: 'An OK waits on you' })).toBeTruthy();
  });

  it("on a phone follows each page's own fold as you move between pages, both ways @4857-S3", async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    owners.finance = { bot: 'bot:qa' };
    localStorage.setItem('tico-next:owner-bar.v1', JSON.stringify({ 'human:maya': { tasks: false, finance: true } }));
    const router = show('/tasks/eng');
    const lip = () => document.querySelector('.tabbar button[aria-label="QA Owner"]')!;
    await waitFor(() => expect(lip().getAttribute('aria-current')).toBe('true'));
    act(() => void router.navigate({ to: '/company/finance' as never }));
    await screen.findByRole('heading', { name: 'Finance' });
    await waitFor(() => expect(lip().getAttribute('aria-current')).toBeNull());
    act(() => void router.navigate({ to: '/tasks/$pipeline' as never, params: { pipeline: 'eng' } as never }));
    await screen.findByRole('heading', { name: 'Board' });
    await waitFor(() => expect(lip().getAttribute('aria-current')).toBe('true'));
  });

  it("on a phone sets the card again moving from one pipeline's board to another's, one page in the fold @4857-S3", async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    localStorage.setItem('tico-next:owner-bar.v1', JSON.stringify({ 'human:maya': { tasks: false } }));
    const router = show('/tasks/eng');
    const lip = (name: string) => document.querySelector(`.tabbar button[aria-label="${name}"]`)!;
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
    // the menu up over the page, by way of Tico: the fold stays as it was
    fireEvent.click(lip('Tico'));
    fireEvent.click(lip('Company'));
    fireEvent.click(lip('Company'));
    await waitFor(() => expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('menu'));
    act(() => void router.navigate({ to: '/tasks/$pipeline' as never, params: { pipeline: 'ops' } as never }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/ops'));
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
  });

  it('on a phone sets the card again when only the search of the address changes @4857-S3', async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    localStorage.setItem('tico-next:owner-bar.v1', JSON.stringify({ 'human:maya': { tasks: false } }));
    const router = show('/tasks/eng');
    const lip = (name: string) => document.querySelector(`.tabbar button[aria-label="${name}"]`)!;
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
    // the menu up over the page, by way of Tico: the fold stays as it was
    fireEvent.click(lip('Tico'));
    fireEvent.click(lip('Company'));
    fireEvent.click(lip('Company'));
    await waitFor(() => expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('menu'));
    act(() => void router.navigate({ to: '/tasks/$pipeline' as never, params: { pipeline: 'eng' } as never, search: { view: 'list' } as never }));
    await waitFor(() => expect(router.state.location.searchStr).toContain('view=list'));
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
  });

  it('on a phone sets the card again on every page change, when both pages keep the same fold @4857-S3', async () => {
    at(390);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    owners.finance = { bot: 'bot:qa' };
    localStorage.setItem('tico-next:owner-bar.v1', JSON.stringify({ 'human:maya': { tasks: false, finance: false } }));
    const router = show('/tasks/eng');
    const lip = (name: string) => document.querySelector(`.tabbar button[aria-label="${name}"]`)!;
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
    // the menu up over the page, by way of Tico: the fold stays as it was
    fireEvent.click(lip('Tico'));
    fireEvent.click(lip('Company'));
    fireEvent.click(lip('Company'));
    await waitFor(() => expect(document.querySelector('.phone')?.getAttribute('data-card')).toBe('menu'));
    act(() => void router.navigate({ to: '/company/finance' as never }));
    await screen.findByRole('heading', { name: 'Finance' });
    await waitFor(() => expect(lip('QA Owner').getAttribute('aria-current')).toBe('true'));
  });

  it('is not drawn on a personal page, whose chat is Tico, nor where no owner is set', async () => {
    at(1440);
    people(MAYA, QA);
    owners.tasks = { bot: 'bot:qa' };
    show('/needs-you');
    expect(await screen.findByRole('heading', { name: 'Needs you page' })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Chat with QA Owner' })).toBeNull();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('page');
  });
});

describe('the landing address', () => {
  function land(at: string) {
    const home = landingRoute.update({ getParentRoute: () => root, id: '/', path: '/' } as never);
    const tasks = createRoute({ getParentRoute: () => root, path: '/tasks/', component: () => <main className="pane page"><h1>Tasks</h1></main> });
    const router = createRouter({ routeTree: root.addChildren([home as never, tasks]), context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    return router;
  }

  it('lands on Tasks (Needs you on top), with no Start page between', async () => {
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [] }]);
    const router = land('/');
    expect(await screen.findByRole('heading', { name: 'Tasks', level: 1 })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks');
    expect(router.state.location.search).toEqual({});
  });

  it('while setup is owed, lands on Tasks with the first run in Tico’s sheet, not the address @s1154', async () => {
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [] }]);
    gate.setup = true;
    const router = land('/?setup=path');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(router.state.location.search).toEqual({});
  });
});

describe('a task over the page', () => {
  it('draws the task modal over the page the address names, the page still drawn under it, and nothing over a page without one', async () => {
    const router = show('/tasks/eng?task=7&list=eng');
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(await screen.findByText('task modal')).toBeTruthy();
    await act(() => router.navigate({ to: '/calendar' }));
    expect(await screen.findByRole('heading', { name: 'Calendar page' })).toBeTruthy();
    expect(screen.queryByText('task modal')).toBeNull();
  });
});

describe('Needs you’s read, from the menu', () => {
  const reads = () => queryClient.getQueryState(['flow'])?.dataUpdatedAt ?? 0;
  it('is not read on a page that marks no task, the menu reading its count alone', async () => {
    show('/calendar');
    await screen.findByRole('heading', { name: 'Calendar page' });
    await waitFor(() => expect(queryClient.getQueryState(['flow', 'count'])?.status).toBe('success'));
    expect(reads()).toBe(0);
  });

  it('a new read of it, or the row held in it, does not draw the shell again', async () => {
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show('/tasks/eng');
    await screen.findByRole('heading', { name: 'Board' });
    await waitFor(() => expect(queryClient.getQueryState(['flow', 'count'])?.status).toBe('success'));
    // TanStack hands a read to the shell on the next timer (notifyManager), so a microtask is not enough on either side:
    // the shell's other reads (the page owners, who is signed in) all in (the directory, never answered here, aside) and
    // drawn before the count, else one is counted as a draw (537 for 536); and the reads below drawn before it is read
    // again, else a redraw they cause lands after the check and it passes. A read drawn in that turn can start others
    // (the page owners and pipelines wait for who is signed in), and one that starts and ends inside a turn is never seen
    // fetching but still draws on the next, so it is quiet only after a whole turn that fetches nothing and draws nothing
    const drawn = () => act(() => new Promise<void>((done) => setTimeout(done, 0)));
    const fetching = () => queryClient.isFetching({ predicate: (q) => q.queryKey[0] !== 'directory' });
    // a shell that never goes quiet (a render loop) fails here by name, not as the test's timeout
    const quiet = async () => {
      await waitFor(() => expect(fetching()).toBe(0));
      const seen = shellDraws.n;
      await drawn();
      return fetching() === 0 && shellDraws.n === seen;
    };
    let settled = false;
    for (let turn = 0; turn < 20 && !settled; turn++) settled = await quiet();
    expect(settled, 'the shell drew or read something in each of 20 timer turns').toBe(true);
    const before = shellDraws.n;
    // a new read, and the row the person is on in Needs you on Tasks
    for (let i = 1; i <= 3; i++) act(() => void queryClient.setQueryData(['flow'], { count: i, items: [], task_ids: [`t${i}`] }));
    act(() => holdRow('t1'));
    act(() => holdRow(null));
    await drawn();
    expect(shellDraws.n).toBe(before);
  });

  it('is read once where tasks carry the Dot: a task, a board', async () => {
    for (const at of ['/task/x1', '/tasks/eng']) {
      queryClient.removeQueries({ queryKey: ['flow'], exact: true });
      show(at);
      await waitFor(() => expect(reads()).toBeGreaterThan(0));
      cleanup();
    }
  });
});

describe('the desktop app', () => {
  it('hears who is signed in, the Needs you count for the badge (following it), and the files origin', async () => {
    const t = fakeDesktop();
    show('/');
    await waitFor(() => expect(t.companies.reportSelf).toHaveBeenCalledWith({ name: 'Fernhill', email: 'maya@example.com', signedOut: false }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(4));
    expect(t.setFilesOrigin).toHaveBeenCalledWith('https://files.example.com');
    act(() => queryClient.setQueryData(['flow', 'count'], { count: 9, wakeAt: null }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(9));
    act(() => queryClient.setQueryData(['flow', 'count'], { count: 0, wakeAt: null }));
    await waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(0));
    expect(t.companies.reportSelf).toHaveBeenCalledTimes(1);
  });

  it('refreshes the Dock badge and the menu’s Needs you count at snooze expiry', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout'] });
    const t = fakeDesktop();
    queryClient.setQueryData(['flow', 'count'], { count: 4, wakeAt: 60_000 });
    const read = vi.spyOn(api, 'get').mockResolvedValue({ flow_count: 5, snooze_refresh_in_ms: null });
    try {
      show('/');
      await vi.waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(4));
      expect(document.querySelector('.nav-flow')?.textContent).toBe('4');
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      await vi.waitFor(() => expect(t.setBadge).toHaveBeenLastCalledWith(5));
      expect(document.querySelector('.nav-flow')?.textContent).toBe('5');
      expect(read).toHaveBeenCalledWith('/api/v2/needs-you?count=true', expect.anything());
    } finally {
      cleanup();
      read.mockRestore();
      vi.useRealTimers();
    }
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
    fireEvent.click(await within(nav).findByRole('button', { name: /^Fernhill/ }));
    const menu = await screen.findByRole('menu');
    await within(menu).findByRole('menuitem', { name: /Okafor Garden Tours/ });
    expect(within(menu).getByRole('menuitem', { name: 'Fernhill' }).className).toContain('is-on');
    expect(within(menu).getByRole('menuitem', { name: /Okafor Garden Tours/ }).textContent).toContain('3');
    expect(within(menu).getByRole('menuitem', { name: /Add company/ })).toBeTruthy();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Okafor Garden Tours/ }));
    expect(vi.mocked(run)).toHaveBeenCalledWith('shell.switchCompany', { id: 'c2' }, { via: 'click' });
    await waitFor(() => expect(t.companies.switchTo).toHaveBeenCalledWith('c2'));
  });

  it('in a browser, the company pick holds this company only, with no Add company @s1160', async () => {
    show('/');
    const nav = await screen.findByRole('navigation', { name: 'Main' });
    fireEvent.click(await within(nav).findByRole('button', { name: /^Fernhill/ }));
    const menu = await screen.findByRole('menu');
    expect(within(within(menu).getByRole('group', { name: 'Companies' })).getAllByRole('menuitem').map((i) => i.textContent)).toEqual(['FFernhill']);
    expect(within(menu).queryByRole('menuitem', { name: /Add company/ })).toBeNull();
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
