import { QueryClientProvider } from '@tanstack/react-query';
import {
  createBrowserHistory,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  Outlet,
  RouterProvider,
  type AnyRouter,
  type RouteComponent,
  type RouterHistory,
} from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/data';
import type * as CallsRouting from '@/features/calls/routing';
import { callState } from '@/features/calls/routing';
import type * as SupportRouting from '@/features/support/routing';
import { loadingReporter } from '@/features/support/routing';
import { resetBuild, sawBuild } from '@/lib/build';
import { AssetRecovery } from './AssetRecovery';
import { Cover } from './Cover';
import type * as Queries from './queries';
import { signedIn } from './queries';
import { isAssetFailure, startRecovery } from './recovery';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('./queries', async (actual) => ({ ...(await actual<typeof Queries>()), signedIn: vi.fn() }));
vi.mock('@/features/support/routing', async (actual) => ({ ...(await actual<typeof SupportRouting>()), loadingReporter: vi.fn(() => false) }));
vi.mock('@/features/calls/routing', async (actual) => ({ ...(await actual<typeof CallsRouting>()), callState: vi.fn() }));

type Call = ReturnType<typeof callState>;

/** The page, as a browser holds it: its address (the real one, which the router's history moves), and the ways to
    leave it, which jsdom cannot do. */
function page() {
  const real = window.location;
  const go = { assign: vi.fn(), replace: vi.fn(), reload: vi.fn() };
  const stub = { ...go };
  for (const k of ['href', 'origin', 'pathname', 'search', 'hash'] as const) Object.defineProperty(stub, k, { get: () => real[k] });
  vi.stubGlobal('location', stub);
  return go;
}

/** This page's build: the entry script a built page carries. */
function entry(name: string) {
  document.head.append(Object.assign(document.createElement('script'), { type: 'module', src: name }));
}

const stops: (() => void)[] = [];
/** Recovery as main.tsx starts it, once for the page. */
const recover = (router?: AnyRouter) => void stops.push(startRecovery(router));

/** The app's pages, Tasks first, with recovery and its card beside the router, as main.tsx has them. */
function app(history: RouterHistory, library: RouteComponent = () => <p>Library</p>) {
  const root = createRootRoute({ component: () => <Outlet />, errorComponent: () => <p>Page fault</p> });
  const pages = [
    createRoute({ getParentRoute: () => root, path: '/tasks', component: () => <p>Tasks</p> }),
    createRoute({ getParentRoute: () => root, path: '/library', component: library }),
    createRoute({ getParentRoute: () => root, path: '/call/$id', component: () => <p>Call</p> }),
  ];
  const router = createRouter({ routeTree: root.addChildren(pages), history });
  recover(router);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <AssetRecovery />
    </QueryClientProvider>,
  );
  return router;
}

/** A dynamic import as Vite's build wraps it (__vitePreload): its failure is told as `vite:preloadError`, then thrown. */
const asBuilt =
  <T,>(load: () => Promise<T>) =>
  () =>
    load().catch((err: unknown) => {
      dispatchEvent(Object.assign(new Event('vite:preloadError', { cancelable: true }), { payload: err }));
      throw err;
    });

const fail = () => act(() => void window.dispatchEvent(new Event('vite:preloadError', { cancelable: true })));
const card = () => screen.findByRole('alertdialog', { name: "Tico couldn't finish loading" });
// This build already reloaded for its files a moment ago.
const reloadedAlready = () => sessionStorage.setItem('tico-next:assets-reloaded', JSON.stringify({ build: '', at: Date.now() }));
const ME = { id: 'human:me', email: '', role: 'person', slug: null };

beforeEach(() => {
  vi.mocked(signedIn).mockResolvedValue(true);
  vi.mocked(callState).mockReturnValue({ phase: 'idle' } as Call);
});

afterEach(() => {
  stops.splice(0).forEach((stop) => stop());
  vi.unstubAllGlobals();
  sessionStorage.clear();
  document.head.querySelectorAll('script').forEach((s) => s.remove());
  resetBuild();
  queryClient.clear();
});

/** An element's load error, as the browser sends it (it does not bubble; the target is the element). */
function failedLoad(el: Element): Event {
  const e = new Event('error');
  Object.defineProperty(e, 'target', { value: el });
  return e;
}

const script = (src: string) => Object.assign(document.createElement('script'), { src });
const link = (rel: string, href: string) => Object.assign(document.createElement('link'), { rel, href });
const rejected = (reason: unknown) => Object.assign(new Event('unhandledrejection'), { reason });

describe('Asset recovery @s1197', () => {
  it('keeps the app and the notice behind a recovery card out of the focus order', async () => {
    const view = (planLocked: boolean, recover: boolean) => (
      <>
        <div data-testid="app">App</div>
        {planLocked && <Cover label="Plan"><button className="btn-fill">Billing</button></Cover>}
        {recover && <AssetRecovery />}
      </>
    );
    reloadedAlready();
    recover();
    const { rerender } = render(view(true, true));
    const plan = screen.getByRole('alertdialog', { name: 'Plan' }) as HTMLElement;
    fail();
    await card();
    // the card comes after the session check: its cover takes the focus once drawn
    await waitFor(() => expect(plan.inert).toBe(true));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Reload' }));
    rerender(view(false, true));
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(true);
    rerender(view(false, false));
    expect((screen.getByTestId('app') as HTMLElement).inert).toBe(false);
  });

  it('counts only our own files failing', () => {
    expect(isAssetFailure(new Event('vite:preloadError'))).toBe(true);
    expect(isAssetFailure(failedLoad(script('/assets/tasks-a1b2.js')))).toBe(true);
    expect(isAssetFailure(failedLoad(link('stylesheet', '/assets/tasks-a1b2.css')))).toBe(true);
    expect(isAssetFailure(failedLoad(link('modulepreload', '/assets/kit-c3d4.js')))).toBe(true);
    expect(isAssetFailure(rejected(new TypeError('Failed to fetch dynamically imported module: /assets/x.js')))).toBe(true);
    expect(isAssetFailure(failedLoad(script('https://beacon.example.com/b.js')))).toBe(false);
    expect(isAssetFailure(failedLoad(link('icon', '/favicon.ico')))).toBe(false);
    expect(isAssetFailure(failedLoad(document.createElement('img')))).toBe(false);
    expect(isAssetFailure(rejected(new Error('The server did not answer')))).toBe(false);
  });

  it('after a release, the next page change is a full load: a link, a replace, Back; a same-page change and a call page stay', async () => {
    const go = page();
    entry('/next/assets/index-a1.js');
    window.history.replaceState(null, '', '/tasks');
    const history = createBrowserHistory();
    try {
      const router = app(history);
      await screen.findByText('Tasks');
      // The server's build is this page's: pages change in place.
      sawBuild('/next/assets/index-a1.js');
      await act(() => router.navigate({ href: '/library' }));
      await screen.findByText('Library');
      // A release.
      sawBuild('/next/assets/index-b2.js');
      await act(() => router.navigate({ href: '/library?q=plans' }));
      expect(router.state.location.search).toEqual({ q: 'plans' });
      // the desktop's "Move the call back to the app" opens the call page, then joins
      await act(() => router.navigate({ href: '/call/c1' }));
      await screen.findByText('Call');
      act(() => history.back());
      await waitFor(() => expect(go.reload).toHaveBeenCalledTimes(1));
      // (a blocked change never settles: the page is on its way out)
      act(() => void router.navigate({ href: '/tasks' }));
      await waitFor(() => expect(go.assign).toHaveBeenCalledWith('/tasks'));
      act(() => void router.navigate({ href: '/tasks', replace: true }));
      await waitFor(() => expect(go.replace).toHaveBeenCalledWith('/tasks'));
      expect(router.state.location.pathname).toBe('/library');
      expect(screen.queryByRole('alertdialog')).toBeNull();
    } finally {
      history.destroy();
      window.history.replaceState(null, '', '/');
    }
  });

  it('a page whose file is gone reloads once, by this alone: the router shows its fault rather than reload on its own', async () => {
    const go = page();
    const gone = new TypeError('Failed to fetch dynamically imported module: http://localhost:3000/next/assets/library-c3.js');
    const library = lazyRouteComponent(asBuilt(() => Promise.reject(gone)) as () => Promise<{ default: () => null }>);
    const router = app(createMemoryHistory({ initialEntries: ['/tasks'] }), library);
    await screen.findByText('Tasks');
    act(() => void router.navigate({ href: '/library' }));
    // the router reloading would leave the page suspended; it throws to the page's fault instead
    await screen.findByText('Page fault');
    await waitFor(() => expect(go.reload).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('reloads on its own at the first file that fails, and shows the card when it fails again after that reload', async () => {
    const go = page();
    const stop = startRecovery();
    const first = render(<AssetRecovery />);
    expect(screen.queryByRole('alertdialog')).toBeNull();
    fail();
    await waitFor(() => expect(go.reload).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    first.unmount();
    stop();
    // The page loaded again, the same build, and its files still fail.
    recover();
    render(
      <>
        <div data-testid="app">App</div>
        <AssetRecovery />
      </>,
    );
    fail();
    const shown = await card();
    await waitFor(() => expect((screen.getByTestId('app') as HTMLElement).inert).toBe(true));
    expect(go.reload).toHaveBeenCalledTimes(1);
    expect(within(shown).getByRole('link', { name: 'Contact Tico support' })).toBeTruthy();
    // no shell, no actions: Reload still reloads
    fireEvent.click(within(shown).getByRole('button', { name: 'Reload' }));
    expect(go.reload).toHaveBeenCalledTimes(2);
  });

  it('lets the error reporter's own file fail: no reload, no card, the app as it is', async () => {
    const go = page();
    recover();
    render(<AssetRecovery />);
    vi.mocked(loadingReporter).mockReturnValueOnce(true);
    fail();
    await act(async () => {});
    expect(signedIn).not.toHaveBeenCalled();
    expect(go.reload).not.toHaveBeenCalled();
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('shows the card, not a reload, when the tab cannot note that it reloaded', async () => {
    const go = page();
    const refuse = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('The site data is blocked', 'SecurityError');
    });
    try {
      recover();
      render(<AssetRecovery />);
      fail();
      await card();
      expect(go.reload).not.toHaveBeenCalled();
    } finally {
      refuse.mockRestore();
    }
  });

  it('keeps a call: no full load and no reload while one is joining or on, the card instead', async () => {
    const go = page();
    vi.mocked(callState).mockReturnValue({ phase: 'joining' } as Call);
    entry('/next/assets/index-a1.js');
    const router = app(createMemoryHistory({ initialEntries: ['/tasks'] }));
    await screen.findByText('Tasks');
    sawBuild('/next/assets/index-b2.js');
    await act(() => router.navigate({ href: '/library' }));
    await screen.findByText('Library');
    vi.mocked(callState).mockReturnValue({ phase: 'live' } as Call);
    fail();
    await card();
    expect(go.assign).not.toHaveBeenCalled();
    expect(go.reload).not.toHaveBeenCalled();
  });

  it('keeps the card, not a reload, while a picked file is not sent, as it was when the file failed', async () => {
    const go = page();
    let answer = (_: boolean) => {};
    vi.mocked(signedIn).mockReturnValue(new Promise((done) => (answer = done)));
    recover();
    const view = (file: boolean) => (
      <>
        {file && <button type="button" data-unsent="">plan.pdf</button>}
        <AssetRecovery />
      </>
    );
    const { rerender } = render(view(true));
    fail();
    // the composer goes with its page before the server answers
    rerender(view(false));
    answer(true);
    await card();
    expect(go.reload).not.toHaveBeenCalled();
  });

  it('keeps the card, not a reload, for a file picked while the server was being asked', async () => {
    const go = page();
    let answer = (_: boolean) => {};
    vi.mocked(signedIn).mockReturnValue(new Promise((done) => (answer = done)));
    recover();
    const view = (file: boolean) => (
      <>
        {file && <button type="button" data-unsent="">plan.pdf</button>}
        <AssetRecovery />
      </>
    );
    const { rerender } = render(view(false));
    fail();
    rerender(view(true));
    answer(true);
    await card();
    expect(go.reload).not.toHaveBeenCalled();
  });

  it('reloads a session that ended, so the server sends it to sign-in, and never shows the card', async () => {
    const real = await vi.importActual<typeof Queries>('./queries');
    vi.mocked(signedIn).mockImplementation(real.signedIn);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })));
    const go = page();
    reloadedAlready();
    recover();
    render(<AssetRecovery />);
    fail();
    await waitFor(() => expect(go.reload).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('shows the card and leaves the app as it is when the server does not answer', async () => {
    const real = await vi.importActual<typeof Queries>('./queries');
    vi.mocked(signedIn).mockImplementation(real.signedIn);
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))));
    const go = page();
    queryClient.setQueryData(['me'], ME);
    recover();
    render(
      <>
        <div data-testid="app">App</div>
        <AssetRecovery />
      </>,
    );
    fail();
    await card();
    expect(queryClient.getQueryState(['me'])?.status).toBe('success');
    expect(queryClient.getQueryData(['me'])).toEqual(ME);
    expect(screen.getByTestId('app')).toBeTruthy();
    expect(go.reload).not.toHaveBeenCalled();
  });
});
