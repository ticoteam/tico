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
import { pipelineTasksQuery } from '@/features/tasks/routing';
import { TasksHome } from '@/features/tasks/exports';
import { forgetPlaces, heldRow, holdRow, nextInList, notePlace, pressListRow } from '@/lib';
import { TaskPage } from '@/features/task/exports';
import type * as TaskExports from '@/features/task/exports';
import { router as appRouter } from '@/router';
import { showPageName } from '@/tico/pageName';
import { setLinkComponent, type LinkProps } from '@/kit/Link';
import { foldSheet } from './Frame';
import { tabTitle, useTabTitle } from './title';

/* The root as the app draws it, with the features around the shell stood in: the second chat for Tico support, the
   desktop app's small call window, and what the page tells the desktop app. */

const { gate, q, catchUpRead, lists, owners, asks, hops, needsRead, shellDraws } = vi.hoisted(() => ({
  /** Needs you read as the app reads it (GET /needs-you), where a test serves it: else the stand-in below. */
  needsRead: { real: false },
  /** Where `/task/<n>` passes on to, as its route does once the task is read: none unless a test names one. */
  hops: {} as Record<string, string>,
  /** The ❓ questions and asks agents put to Maya that wait (`asks`, from Needs you's items). */
  asks: [] as { id: string; kind?: 'question' | 'ask'; by: string; pipeline: string | null; href: string | null }[],
  gate: { screen: null as string | null, setup: false },
  /** Each page's owner, as GET /page-owners answers: none unless a test names one. */
  owners: {} as Record<string, { bot: string | null; own?: boolean }>,
  /** Each pipeline's open tasks, as its list read gives them. */
  lists: {} as Record<string, unknown[]>,
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
  pageOwnersQuery: { queryKey: ['settings', 'page-owners'], queryFn: async () => ({ pages: owners, canChange: false }), staleTime: Infinity },
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
  pipelineTasksQuery: vi.fn((pipeline: string, f: object) => ({
    queryKey: ['tasks', 'list', pipeline, JSON.stringify(f)],
    queryFn: async () => ({ tasks: lists[pipeline] ?? [], cursor: '' }),
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
// the task page itself where a test draws it (`show`'s `opened`); its chat a stand-in
vi.mock('@/features/task/exports', async (orig) => ({ TaskPage: (await orig<typeof TaskExports>()).TaskPage, TaskChat: () => <p>task chat</p> }));
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
        <Link to="/tasks/$pipeline/$task" params={{ pipeline: 'eng', task: '7' }} onClick={(e) => pressListRow('eng', '7', e)}>
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
    asks.push({ id: 'n5', kind: 'ask', by: 'bot:qa', pipeline: 'eng', href: '/task/t1' });
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [], manager: 'bot:qa', owner: null }]);
  };
  const askRead = async () => {
    await waitFor(() => expect(queryClient.getQueryData<{ asks: unknown[] }>(['flow'])?.asks).toHaveLength(1));
    await act(async () => {});
  };

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

  it('keeps the first run open over Tasks while setup is owed, and for the old welcome address @s1154', async () => {
    queryClient.setQueryData(['pipelines'], [{ id: 'eng', name: 'Engineering', stages: [] }]);
    gate.setup = true;
    const router = land('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(router.state.location.search).toEqual({ setup: 'path' });
    gate.setup = false;
    const again = land('/?setup=path');
    await waitFor(() => expect(again.state.location.pathname).toBe('/tasks'));
    expect(again.state.location.search).toEqual({ setup: 'path' });
  });
});

describe('an open task', () => {
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  afterEach(() => at(1024));

  it('opened from a thread, keeps that thread beside it in place of the task’s own chat (plan/layers.html)', async () => {
    at(1600);
    people(MAYA, QA);
    show('/task/7?chat=agent%3Aqa&via=thread');
    const sheet = await screen.findByRole('complementary', { name: 'Chat with QA Owner' });
    expect(await within(sheet).findByText('a teammate chat')).toBeTruthy();
    expect(screen.queryByText('task chat')).toBeNull();
  });

  it('the pipeline editor is a page, not a task: no task chat and no task list', async () => {
    show('/tasks/eng/edit');
    expect(await screen.findByRole('heading', { name: 'Edit pipeline' })).toBeTruthy();
    expect(screen.queryByText('task chat')).toBeNull();
    expect(screen.queryByRole('navigation', { name: 'Engineering' })).toBeNull();
  });
});

describe('an open task on a phone', () => {
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  const bar = () => screen.getByRole('navigation', { name: 'Bar' });
  const icon = (name: string) => within(bar()).getByRole('button', { name });
  // a task as its route read it: with a chat, and the ❓s in it that wait on Maya
  const read = (n: number, ...questions: string[]) => {
    queryClient.setQueryData(['tasks', 'ref', String(n)], `t${n}`);
    queryClient.setQueryData(['tasks', 'detail', `t${n}`], {
      id: `t${n}`, number: n, pipeline_id: 'eng', owner_id: 'bot:qa', conversation_id: `c${n}`,
      waits: questions.map((q) => ({ state: 'person', who: 'human:maya', requirement_id: `qmark:${q}` })),
    });
  };
  beforeEach(() => {
    at(390);
    people(MAYA, QA);
    read(7);
  });
  afterEach(() => at(1024));

  it('a link to its chat comes up on it: Agent, the task under it', async () => {
    show('/tasks/eng/7/chat');
    const card = await screen.findByRole('dialog', { name: 'Agent' });
    expect(await within(card).findByText('task chat')).toBeTruthy();
    expect(icon('Agent').getAttribute('aria-current')).toBe('true');
    expect(within(bar()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Search', 'Tico', 'Company', 'Agent', 'Task']);
  });

  it('so does a link by its number that names the chat (`?tab=chat`)', async () => {
    show('/task/7?tab=chat');
    expect(await within(await screen.findByRole('dialog', { name: 'Agent' })).findByText('task chat')).toBeTruthy();
  });

  it('opened from its board, its chat stays down; Company goes back to the board and the task’s icons leave', async () => {
    const router = show('/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    fireEvent.click(screen.getAllByRole('link', { name: 'Bulk edit stock' })[0]!);
    expect((await screen.findAllByRole('heading', { name: 'An open task' })).length).toBeGreaterThan(0);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(icon('Task').getAttribute('aria-current')).toBe('true');
    fireEvent.click(icon('Company'));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng'));
    expect(within(bar()).queryByRole('button', { name: 'Task' })).toBeNull();
    expect(icon('Company').getAttribute('aria-current')).toBe('true');
  });

  it('a side thread it was opened from is its Agent, with a link to the task’s own chat in its place; Back brings the thread up again', async () => {
    const router = show('/tasks/eng/7?chat=agent%3Aqa&via=thread');
    const card = await screen.findByRole('dialog', { name: 'Agent' });
    expect(await within(card).findByText('a teammate chat')).toBeTruthy();
    fireEvent.click(within(card).getByRole('link', { name: 'Task chat' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng/7/chat'));
    expect(await within(screen.getByRole('dialog', { name: 'Agent' })).findByText('task chat')).toBeTruthy();
    fireEvent.click(icon('Task'));
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => router.history.back());
    await waitFor(() => expect(router.state.location.search).toEqual({ chat: 'agent:qa', via: 'thread' }));
    expect(await within(await screen.findByRole('dialog', { name: 'Agent' })).findByText('a teammate chat')).toBeTruthy();
  });

  it('a ❓ to the person in a task’s chat brings it up on each task it opens, Back and Forward too; put down, it stays down there', async () => {
    read(7, 'q7');
    read(8, 'q8');
    const router = show('/tasks/eng/7');
    expect(await screen.findByRole('dialog', { name: 'Agent' })).toBeTruthy();
    fireEvent.click(icon('Task'));
    expect(screen.queryByRole('dialog')).toBeNull();
    await act(() => router.navigate({ to: '/tasks/eng/8' as never }));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy());
    act(() => router.history.back());
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng/7'));
    expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy();
  });

  it('a new ❓ brings the chat up again after it was put down, on the chat’s own address too', async () => {
    show('/tasks/eng/7/chat');
    expect(await screen.findByRole('dialog', { name: 'Agent' })).toBeTruthy();
    fireEvent.click(icon('Task'));
    expect(screen.queryByRole('dialog')).toBeNull();
    act(() => read(7, 'q1'));
    await waitFor(() => expect(screen.getByRole('dialog', { name: 'Agent' })).toBeTruthy());
    fireEvent.click(icon('Task'));
    act(() => read(7, 'q1'));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a link to the chat of a task that did not load (gone, not theirs) raises nothing over its error; a thread the address names still comes up', async () => {
    const router = show('/tasks/eng/9/chat');
    expect((await screen.findAllByRole('heading', { name: 'An open task' })).length).toBeGreaterThan(0);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(within(bar()).queryByRole('button', { name: 'Agent' })).toBeNull();
    expect(screen.queryByText('task chat')).toBeNull();
    await act(() => router.navigate({ to: '/tasks/eng/9' as never, search: { chat: 'agent:qa' } as never }));
    expect(await within(await screen.findByRole('dialog', { name: 'Agent' })).findByText('a teammate chat')).toBeTruthy();
  });

  it('`?tab=chat` on a task’s own address brings its chat up too', async () => {
    show('/tasks/eng/7/spec?tab=chat');
    expect(await within(await screen.findByRole('dialog', { name: 'Agent' })).findByText('task chat')).toBeTruthy();
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

  it('opens in place: beside it the same pipeline, as its board was filtered, the task chosen, no count in its head; Back is the board again', async () => {
    const router = await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    expect(await screen.findByRole('heading', { name: 'An open task' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng/7');
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    expect(within(list).getByRole('button', { name: 'Engineering' })).toBeTruthy();
    const open = await within(list).findByRole('link', { name: /Bulk edit stock/ });
    expect(open.classList.contains('is-on')).toBe(true);
    expect(open.getAttribute('href')).toBe('/tasks/eng/7');
    expect(within(list).getByRole('link', { name: /Rate fallback/ }).classList.contains('is-on')).toBe(false);
    expect(within(list).getAllByRole('button', { name: /^(Review|QA)/ }).map((b) => b.textContent)).toEqual(['Review1', 'QA1']);
    expect(vi.mocked(pipelineTasksQuery)).toHaveBeenLastCalledWith('eng', expect.objectContaining({ owner: 'bot:qa' }));
    // what waits on you is the menu's Needs you, not a count over the list: the head is the crumb and ×
    expect(within(list).queryByRole('link', { name: 'Flow' })).toBeNull();
    expect(list.querySelector('.li-head')?.textContent).toBe('TasksEngineering');
    expect(await screen.findByText('task chat')).toBeTruthy();
    act(() => router.history.back());
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
  });

  it('draws the trail over it: the company, Tasks, its pipeline, the task; the pipeline is its board again, as it was', async () => {
    const router = await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    expect(await screen.findByRole('heading', { name: 'An open task' })).toBeTruthy();
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('task');
    const trail = screen.getByRole('navigation', { name: 'Breadcrumb' });
    await waitFor(() => expect([...trail.querySelectorAll('a,span')].map((p) => p.textContent)).toEqual(['Fernhill', 'Tasks', 'Engineering', 'An open task']));
    expect(within(trail).getByRole('link', { name: 'Fernhill' }).getAttribute('href')).toBe('/');
    expect(within(trail).getByRole('link', { name: 'Tasks' }).getAttribute('href')).toBe('/tasks');
    expect(within(trail).queryByRole('link', { name: 'An open task' })).toBeNull();
    // the step before is that board: back to it, its filters kept, the menu in its place again
    fireEvent.click(within(trail).getByRole('link', { name: 'Engineering' }));
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng');
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
    expect(document.querySelector('.shell-frame')?.getAttribute('data-arrangement')).toBe('page');
    expect(screen.getByRole('navigation', { name: 'Main' })).toBeTruthy();
  });

  it('heads its list as a crumb, Tasks › the pipeline ⌄, and × puts the menu back in the list’s place until you leave the tasks', async () => {
    const router = await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    expect(within(list).getByRole('link', { name: 'Tasks' }).getAttribute('href')).toBe('/tasks');
    expect(within(list).getByRole('button', { name: 'Engineering' })).toBeTruthy();
    fireEvent.click(within(list).getByRole('button', { name: 'Close the list' }));
    // the company is one press away again: the menu, open, where the list was; the task stays
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy();
    expect(screen.queryByRole('navigation', { name: 'Engineering' })).toBeNull();
    expect(screen.getByRole('heading', { name: 'An open task' })).toBeTruthy();
    expect(router.state.location.pathname).toBe('/tasks/eng/7');
    // back on the board, the next task opened from it has its list again
    act(() => router.history.back());
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    expect(await screen.findByRole('navigation', { name: 'Engineering' })).toBeTruthy();
  });

  it('keeps its rows in the order drawn, a folded stage’s left out, so an answer that leaves nothing on the task opens the row below', async () => {
    lists.eng = [row(7, 'Bulk edit stock', 'review'), row(8, 'Postal codes', 'qa'), row(5, 'Rate fallback', 'review')];
    await board();
    fireEvent.click(screen.getByRole('link', { name: 'Bulk edit stock' }));
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    await within(list).findByRole('link', { name: /Postal codes/ });
    // by stage, then as the list read gives them: Review's 7 and 5, then QA's 8
    expect(nextInList('t7')).toEqual({ pipeline: 'eng', task: '5' });
    expect(nextInList('t5')).toEqual({ pipeline: 'eng', task: '8' });
    expect(nextInList('t8')).toBeNull();
    fireEvent.click(within(list).getByRole('button', { name: /^QA/ }));
    await waitFor(() => expect(within(list).queryByRole('link', { name: /Postal codes/ })).toBeNull());
    expect(nextInList('t5')).toBeNull();
    // with the list closed there is no row below
    fireEvent.click(within(list).getByRole('button', { name: 'Close the list' }));
    expect(await screen.findByRole('navigation', { name: 'Main' })).toBeTruthy();
    expect(nextInList('t7')).toBeNull();
  });

});

describe('an answer on a task beside its pipeline’s list', () => {
  /* The task page itself under the shell: one plain OK on each task waits on Maya, and Approve leaves nothing on it. */
  const at = (w: number) =>
    act(() => {
      Object.defineProperty(window, 'innerWidth', { value: w, configurable: true });
      window.dispatchEvent(new Event('resize'));
    });
  const ENG = { id: 'eng', name: 'Engineering', stages: [{ id: 'review', name: 'Review' }, { id: 'qa', name: 'QA' }], done: null };
  const OPS = { id: 'ops', name: 'Ops', stages: [{ id: 'review', name: 'Review' }], done: null };
  // task 12 is in Ops; every other one in Engineering
  const pipeOf = (n: number) => (n === 12 ? 'ops' : 'eng');
  const row = (n: number, stage: string) => ({ id: `t${n}`, number: n, title: `Task ${n}`, status: 'open', pipeline_id: pipeOf(n), stage_id: stage, owner_id: 'bot:qa', labels: [], late: false, turn: null, updated_at: '2026-11-18T09:00:00Z', version: 1 });
  const ok = (n: number, answered: boolean) => ({
    requirement_id: `r${n}`,
    kind: 'ok',
    line: 'Ship it?',
    met: answered,
    may: answered ? ['undo'] : ['ok', 'no'],
    shows: [],
    asked_by: 'bot:qa',
    answer: answered ? { id: `a${n}`, value: 'ok', by: 'human:maya', at: '2026-11-19T10:00:00Z', note: null } : null,
    standing: answered ? 'done' : 'waiting',
    who: 'human:maya',
  });
  const task = (n: number, answered = false) => ({
    ...row(n, 'review'),
    pipeline: pipeOf(n) === 'ops' ? { id: 'ops', name: 'Ops' } : { id: 'eng', name: 'Engineering' },
    stage: { id: 'review', name: 'Review' },
    next: { id: 'qa', name: 'QA' },
    conversation_id: null,
    created_at: '2026-11-01T10:00:00Z',
    documents: [],
    links: [],
    to_leave: [ok(n, answered)],
  });
  // what waits on Maya, as Needs you lists it (oldest first): the OK on each of these tasks; an answer takes it out
  let waiting: number[] = [];
  const need = (n: number) => ({
    id: `v1:r${n}`,
    kind: 'ask',
    task_id: `t${n}`,
    requirement_id: `r${n}`,
    line: 'Ship it?',
    since: '2026-11-18T09:00:00Z',
    task: { ...row(n, 'review'), stage_since: '2026-11-18T09:00:00Z' },
    ask: ok(n, false),
  });
  beforeEach(() => {
    at(1600);
    people(MAYA, QA);
    queryClient.setQueryData(['pipelines'], [ENG, OPS]);
    lists.eng = [row(7, 'review'), row(5, 'review'), row(8, 'qa')];
    waiting = [];
    vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
      const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
      const n = Number(/\/api\/v2\/tasks\/(?:%23|t)(\d+)/.exec(url)?.[1]);
      if (url.startsWith('/api/v2/needs-you?limit=')) return json({ actor: 'human:maya', items: waiting.map(need), next: null });
      if (init?.method === 'POST' && /\/asks\/r\d+\/answer$/.test(url)) {
        waiting = waiting.filter((w) => w !== n);
        return json({ task: task(n, true) });
      }
      if (url.includes('/api/v2/oks/')) return json({}, 404);
      if (url.endsWith('/files?all=1')) return json({ files: [] });
      if (n) return json({ task: task(n), runs: [], children: [], can_comment: true, mover: true });
      return json({});
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    at(1024);
  });
  const page = (p: { task: string; tab?: string }) => <TaskPage task={p.task} pipeline="eng" tab={p.tab} />;
  // once the page has named its tab in the address (it has settled), its Approve
  const approve = async (router: { state: { location: { pathname: string } } }, n: number) => {
    await waitFor(() => expect(router.state.location.pathname).toMatch(new RegExp(`^/tasks/eng/${n}/`)));
    const button = await waitFor(() => {
      const b = within(screen.getByRole('region', { name: 'The decision' })).getByRole('button', { name: 'Approve' });
      expect(b).toHaveProperty('disabled', false);
      return b;
    });
    fireEvent.click(button);
  };

  it('opened from its board, Approve opens the task below it in the list, as its row does, and its Approve the one after', async () => {
    const router = show('/tasks/eng?owner=bot%3Aqa', false, page);
    fireEvent.click(await screen.findByRole('link', { name: 'Bulk edit stock' }));
    await screen.findByRole('navigation', { name: 'Engineering' });
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/5(\/|$)/));
    await approve(router, 5);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/8(\/|$)/));
  });

  it('opened over its board with no press on its card (Tico’s tracker), Approve goes back to the board', async () => {
    const router = show('/tasks/eng?owner=bot%3Aqa', false, page);
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    await act(() => router.navigate({ href: '/tasks/eng/7' }));
    await screen.findByRole('navigation', { name: 'Engineering' });
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng'));
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
  });

  it('opened by a link, then its board, a reload and Back: Approve goes to the board', async () => {
    const router = show('/tasks/eng/7', false, page);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng/7/details'));
    await act(() => router.navigate({ href: '/tasks/eng' }));
    expect(await screen.findByRole('heading', { name: 'Board' })).toBeTruthy();
    // the reload: what the app noted in memory is gone, and the board it loads on is noted afresh
    forgetPlaces();
    notePlace('/tasks/eng', '', {}, router.state.location.state);
    act(() => router.history.back());
    await screen.findByRole('navigation', { name: 'Engineering' });
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng'));
  });

  it('a row pressed with a key held (opened elsewhere) does not mark that task here, if it opens from a thread next', async () => {
    const router = show('/needs-you', false, page);
    expect(await screen.findByRole('heading', { name: 'Needs you page' })).toBeTruthy();
    await act(() => router.navigate({ href: '/tasks/eng/7' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng/7/details'));
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    fireEvent.click(await within(list).findByRole('link', { name: /Task 5/ }), { metaKey: true });
    await act(() => router.navigate({ href: '/tasks/eng/5/details?chat=c1&via=thread' }));
    await approve(router, 5);
    await waitFor(() => expect(router.state.location.pathname).toBe('/needs-you'));
  });

  it('opened from Needs you, Approve goes back there; a press on a row of the list opens a task that goes on to the row below', async () => {
    const router = show('/needs-you', false, page);
    expect(await screen.findByRole('heading', { name: 'Needs you page' })).toBeTruthy();
    await act(() => router.navigate({ href: '/tasks/eng/7' }));
    await screen.findByRole('navigation', { name: 'Engineering' });
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toBe('/needs-you'));
    // again, and a row pressed in the list: that task was opened from the list
    await act(() => router.navigate({ href: '/tasks/eng/8' }));
    const list = await screen.findByRole('navigation', { name: 'Engineering' });
    fireEvent.click(await within(list).findByRole('link', { name: /Task 7/ }));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/7(\/|$)/));
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/5(\/|$)/));
  });

  it('with the list folded away, Approve goes back to the board', async () => {
    const router = show('/tasks/eng?owner=bot%3Aqa', false, page);
    fireEvent.click(await screen.findByRole('link', { name: 'Bulk edit stock' }));
    await screen.findByRole('navigation', { name: 'Engineering' });
    act(() => foldSheet('list', true));
    await approve(router, 7);
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks/eng'));
    expect(router.state.location.search).toEqual({ owner: 'bot:qa' });
  });

  // Needs you on top of Tasks, serving an OK on each of `tasks` in that order; a row of it pressed opens the task
  const fromTasks = (tasks: number[]) => {
    waiting = tasks;
    needsRead.real = true;
    for (const n of tasks) hops[String(n)] = `/tasks/${pipeOf(n)}/${n}`;
    // the kit's rows move through the router, as main.tsx has them
    setLinkComponent(({ href, ...rest }: LinkProps) => <Link {...rest} to={href as never} />);
    onTestFinished(() => setLinkComponent((p: LinkProps) => <a {...p} />));
    return show('/tasks', false, (p) => <TaskPage task={p.task} pipeline={p.pipeline} tab={p.tab} />);
  };
  const approveOn = async (router: { state: { location: { pathname: string } } }, pipeline: string, n: number) => {
    await waitFor(() => expect(router.state.location.pathname).toMatch(new RegExp(`^/tasks/${pipeline}/${n}/`)));
    const button = await waitFor(() => {
      const b = within(screen.getByRole('region', { name: 'The decision' })).getByRole('button', { name: 'Approve' });
      expect(b).toHaveProperty('disabled', false);
      return b;
    });
    fireEvent.click(button);
  };

  it('opened from Needs you on top of Tasks: Needs you is the list beside it, and Approve opens the row below there, whatever its pipeline', async () => {
    const router = fromTasks([7, 12, 5]);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('link', { name: /Task 7/ }));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/7\//));
    // beside it, Needs you (not its pipeline's list): title-only rows as a pipeline's list draws them, the open one chosen
    const side = await screen.findByRole('navigation', { name: 'Needs you' });
    expect(screen.queryByRole('navigation', { name: 'Engineering' })).toBeNull();
    expect(side.querySelector('.li-head')?.textContent).toBe('TasksNeeds you');
    await waitFor(() => expect([...side.querySelectorAll('a.li')].map((a) => a.getAttribute('title'))).toEqual(['Task 7', 'Task 12', 'Task 5']));
    expect(within(side).getByRole('link', { name: /Task 7/ }).classList.contains('is-on')).toBe(true);
    expect(within(side).queryByRole('button', { name: /Why here/ })).toBeNull();
    expect(side.querySelector('.row')).toBeNull();
    await approveOn(router, 'eng', 7);
    // the row below in Needs you is in Ops: it opens there, Needs you still beside it
    await approveOn(router, 'ops', 12);
    expect(screen.getByRole('navigation', { name: 'Needs you' })).toBeTruthy();
    await approveOn(router, 'eng', 5);
    // the last row answered: back to Tasks
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    expect(await screen.findByRole('heading', { name: 'Tasks', level: 1 })).toBeTruthy();
  }, 20_000);

  it('a row past a group’s cap, opened once the group shows all: Approve opens the row below it', async () => {
    const many = Array.from({ length: 14 }, (_, i) => 101 + i);
    const router = fromTasks(many);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('button', { name: '4 more' }));
    fireEvent.click(await within(top).findByRole('link', { name: /Task 111/ }));
    await approveOn(router, 'eng', 111);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/112\//));
  }, 20_000);

  it('on a phone, where no list is beside the task, Approve opens the next row of Needs you all the same', async () => {
    at(390);
    const router = fromTasks([7, 12, 5]);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('link', { name: /Task 7/ }));
    expect(screen.queryByRole('navigation', { name: 'Needs you' })).toBeNull();
    await approveOn(router, 'eng', 7);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/ops\/12\//));
  }, 20_000);

  it('Tico’s new order landing while a task from Needs you is open moves only the rows below it, beside it and for Approve; only Tasks asks for it', async () => {
    const asked = vi.spyOn(api, 'post');
    onTestFinished(() => asked.mockRestore());
    const sorts = () => asked.mock.calls.filter(([url]) => url === '/api/v2/needs-you/sort').length;
    const router = fromTasks([7, 12, 5, 9]);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    // Needs you on top of Tasks is the Needs you page: it asks Tico to sort it
    await waitFor(() => expect(sorts()).toBe(1));
    fireEvent.click(await within(top).findByRole('link', { name: /Task 7/ }));
    const side = await screen.findByRole('navigation', { name: 'Needs you' });
    const titles = () => [...side.querySelectorAll('a.li')].map((a) => a.getAttribute('title'));
    await waitFor(() => expect(titles()).toEqual(['Task 7', 'Task 12', 'Task 5', 'Task 9']));
    // Tico's sort lands: 7, the row open, stays where it was and the rows below it take the new order; one more waits
    waiting = [5, 9, 12, 7, 3];
    hops['3'] = '/tasks/eng/3';
    await act(() => queryClient.invalidateQueries({ queryKey: ['flow'], exact: true }));
    await waitFor(() => expect(titles()).toEqual(['Task 7', 'Task 5', 'Task 9', 'Task 12', 'Task 3']));
    // the list beside a task never asks, though what is on it changed
    expect(sorts()).toBe(1);
    await approveOn(router, 'eng', 7);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/5\//));
  }, 20_000);

  it('a press or a focus on the open task’s page keeps its row held in Needs you; a press on another row moves it', async () => {
    const router = fromTasks([7, 12, 5]);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('link', { name: /Task 7/ }));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/7\//));
    const side = await screen.findByRole('navigation', { name: 'Needs you' });
    await waitFor(() => expect(heldRow()).toBe('t7'));
    // Tasks (and its Mine, which lets go of a row on a press elsewhere) is not drawn while the task is open
    fireEvent.pointerDown(document.body);
    fireEvent.focusIn(within(screen.getByRole('region', { name: 'The decision' })).getByRole('button', { name: 'Approve' }));
    expect(heldRow()).toBe('t7');
    fireEvent.click(within(side).getByRole('link', { name: /Task 12/ }));
    await waitFor(() => expect(heldRow()).toBe('t12'));
  }, 20_000);

  it('left by Back, a task from Needs you lets go of its row: Tasks draws Tico’s new order, its rows capped again', async () => {
    const many = Array.from({ length: 14 }, (_, i) => 101 + i);
    const router = fromTasks(many);
    let top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('button', { name: '4 more' }));
    fireEvent.click(await within(top).findByRole('link', { name: /Task 111/ }));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/111\//));
    await screen.findByRole('navigation', { name: 'Needs you' });
    // Tico's sort lands while the task is open, then Back
    waiting = [...many].reverse();
    await act(() => queryClient.invalidateQueries({ queryKey: ['flow'], exact: true }));
    act(() => router.history.back());
    await waitFor(() => expect(router.state.location.pathname).toBe('/tasks'));
    top = await screen.findByRole('region', { name: 'Needs you' });
    const titles = () => [...top.querySelectorAll('a')].map((a) => a.textContent).filter((t) => t?.startsWith('Task'));
    await waitFor(() => expect(titles()[0]).toMatch(/^Task 114/));
    expect(within(top).getByRole('button', { name: '4 more' })).toBeTruthy();
  }, 20_000);

  it('a long Needs you beside a task draws only the rows on screen, and none tucked away; Approve still goes on down all of it', async () => {
    // jsdom lays nothing out: the list gets a window to fill
    const tall = vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(340);
    onTestFinished(() => tall.mockRestore());
    const many = Array.from({ length: 80 }, (_, i) => 201 + i);
    const router = fromTasks(many);
    const top = await screen.findByRole('region', { name: 'Needs you' });
    fireEvent.click(await within(top).findByRole('link', { name: /Task 201/ }));
    const side = await screen.findByRole('navigation', { name: 'Needs you' });
    await waitFor(() => expect(side.querySelectorAll('a.li').length).toBeGreaterThan(0));
    expect(side.querySelectorAll('a.li').length).toBeLessThan(many.length);
    act(() => foldSheet('list', true));
    await waitFor(() => expect(document.querySelectorAll('nav[aria-label="Needs you"] a.li').length).toBe(0));
    await approveOn(router, 'eng', 201);
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/tasks\/eng\/202\//));
  }, 20_000);
});

describe('Needs you’s read, from the menu', () => {
  const reads = () => queryClient.getQueryState(['flow'])?.dataUpdatedAt ?? 0;
  it('is not read on a page that marks no task, the menu reading its count alone', async () => {
    show('/calendar');
    await screen.findByRole('heading', { name: 'Calendar page' });
    await waitFor(() => expect(queryClient.getQueryState(['flow', 'count'])?.status).toBe('success'));
    expect(reads()).toBe(0);
  });

  it('a new read of it, or the row held in it, draws the shell again only while a task opened from it is open', async () => {
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show('/tasks/eng');
    await screen.findByRole('heading', { name: 'Board' });
    await waitFor(() => expect(queryClient.getQueryState(['flow', 'count'])?.status).toBe('success'));
    await act(async () => {});
    const before = shellDraws.n;
    // a new read, and the row the person is on in Needs you on Tasks
    for (let i = 1; i <= 3; i++) act(() => void queryClient.setQueryData(['flow'], { count: i, items: [], task_ids: [`t${i}`] }));
    act(() => holdRow('t1'));
    act(() => holdRow(null));
    await act(async () => {});
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
