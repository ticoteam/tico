import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data/query-client';
import { Route as root } from '@/routes/__root';
import { resetDesktopReports, type TicoDesktop } from '@/desktop';
import { run } from '@/actions/run';

/* The root as the app draws it, with the features around the shell stood in: the second chat for Tico support, the
   desktop app's small call window, and what the page tells the desktop app. */

const { gate, q } = vi.hoisted(() => ({
  gate: { screen: null as string | null },
  q: <T,>(key: string, data: T) => ({ queryKey: [key], queryFn: async () => data, staleTime: Infinity }),
}));

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
vi.mock('@/features/start/exports', () => ({
  FirstRun: () => null,
  StartScreen: ({ screen }: { screen: string }) => <p>start: {screen}</p>,
  useStartGate: () => ({ screen: gate.screen, error: null, retry: () => {}, busy: false, firstRun: false }),
}));
const call = vi.hoisted(() => ({ phase: 'idle' }));
vi.mock('@/features/calls/exports', () => ({
  CallBar: () => <div>call bar</div>,
  CallSide: () => null,
  RingCard: () => null,
  TOGGLE_MUTE: 'calls.toggleMute',
  useCall: () => ({ muted: false, phase: call.phase }),
  isCallActive: (c: { phase: string }) => c.phase === 'live',
}));
vi.mock('@/features/tasks/exports', () => ({ flowQuery: q('flow', { count: 4, items: [] }), pipelinesQuery: q('pipelines', []), roadmapPipeline: () => null }));
vi.mock('@/features/team/exports', () => ({ TeammateChat: () => null, teamSlug: (t: string) => t, whoHref: () => '/' }));
vi.mock('@/features/threads/exports', () => ({
  ChatSheet: () => <p>a teammate chat</p>,
  catchUpQuery: q('catch-up', { count: 0 }),
  updateListQuery: () => ({ queryKey: ['updates'], queryFn: async () => ({ unread: 0 }), initialPageParam: '', getNextPageParam: () => undefined }),
}));
vi.mock('@/features/support/exports', () => ({ SUPPORT_CHAT: 'support', SupportChat: () => <p>support chat</p>, supportUnreadQuery: q('support-unread', 2) }));
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
  const router = createRouter({ routeTree: root.addChildren([page, call]), context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
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
  queryClient.clear();
  resetDesktopReports();
});
afterEach(() => {
  delete (window as { tico?: TicoDesktop }).tico;
});

describe('the root', () => {
  it('draws Tico support as the second chat for ?chat=support, and the menu shows its unread count', async () => {
    show('/?chat=support');
    const sheet = await screen.findByRole('complementary', { name: 'Tico support' });
    expect(sheet.textContent).toContain('support chat');
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
    expect(sheet.textContent).toContain('a teammate chat');
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
