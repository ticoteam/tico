import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { Profiler, type ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { queryClient, recordVisit } from '@/data';
import { changeWords, forgetPlaces, openOf, openTasksOver } from '@/lib';
import { PageTabs } from '@/pagekinds/_parts/PageTabs';
import type { TaskData } from './queries';
import { taskRefQuery } from './queries';
import { TaskModal } from './TaskModal';

/* The task open over a page (plan: the task modal): its panes on a phone, what Esc closes first, the keyboard on a
   phone, what it lets go once closed, and the page under it left alone. */

vi.mock('@/live/follow', () => ({ useFollow: vi.fn() }));
vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
vi.mock('@/data', async (orig) => ({ ...(await orig<object>()), recordVisit: vi.fn() }));

type Reply = { status: number; body: unknown };
const ok = (body: unknown): Reply => ({ status: 200, body });
const question = { state: 'person', who: 'human:me', line: 'Which carrier?', requirement_id: 'qmark:m1', since: null, until: null, ask: true };
const task = (more: Partial<TaskData> = {}): TaskData =>
  ({
    id: 't1',
    number: 12,
    title: 'Ship the order',
    status: 'open',
    lane: 'open',
    pipeline_id: 'p1',
    pipeline: { id: 'p1', name: 'Ops' },
    stage_id: 's1',
    stage: { id: 's1', name: 'Pack' },
    owner_id: 'bot:a1',
    requester_id: 'human:me',
    labels: [],
    created_at: '2026-11-01T10:00:00Z',
    documents: [{ file_id: 'f1', name: 'Packing list', version: 1 }],
    to_leave: [],
    waits: [],
    links: [],
    runs: [],
    children: [],
    parent: null,
    can_comment: true,
    mover: true,
    ...more,
  }) as unknown as TaskData;

let shown = task();
/** The task read held back until `let go` is called, where a test says so. */
let held: Promise<void> | null = null;
function serve() {
  vi.stubGlobal('fetch', async (url: string) => {
    const u = String(url);
    if (u.includes('/api/v2/tasks/%2312') || u.endsWith('/api/v2/tasks/t1')) {
      if (held) await held;
      return json(ok({ task: shown, runs: [], children: [], comments: [], events: [], messages: [], has_more: false, can_comment: true, mover: true }));
    }
    if (u.includes('/files')) return json(ok({ files: [] }));
    if (u.includes('/api/v2/pipelines')) return json(ok({ pipelines: [{ id: 'p1', name: 'Ops', stages: [{ id: 's1', name: 'Pack', side: false }] }] }));
    if (u.includes('/needs-you')) return json(ok({ items: [] }));
    if (u.includes('/timeline')) return json(ok({ messages: [], next_cursor: null, read_marks: {} }));
    if (u.includes('/api/v2/tasks?')) return json(ok({ tasks: [] }));
    return json(ok({}));
  });
}
const json = (r: Reply) => new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });

function show(at: string, page: ReactNode = null) {
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {page}
        <TaskModal />
      </>
    ),
  });
  const tree = root.addChildren([createRoute({ getParentRoute: () => root, path: '/tasks' })]);
  const router = createRouter({ routeTree: tree, history: createMemoryHistory({ initialEntries: [at] }) });
  openTasksOver(router);
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}
const modal = () => document.querySelector<HTMLElement>('.tmd');
const open = (router: ReturnType<typeof show>) => openOf(router.state.location.search as Record<string, unknown>);
const esc = (target: Element) => fireEvent.keyDown(target, { key: 'Escape' });

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
});
beforeEach(() => {
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: 'human:me', email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  shown = task();
  held = null;
  serve();
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.mocked(recordVisit).mockClear();
  delete document.documentElement.dataset.layout;
  forgetPlaces();
});

describe('on a phone', () => {
  beforeEach(() => {
    document.documentElement.dataset.layout = 'cards';
  });

  it('shows the chat when a ❓ waits on the person, chosen once the task is read: never the task first, then the chat', async () => {
    shown = task({ waits: [question] } as Partial<TaskData>);
    let go!: () => void;
    held = new Promise((done) => (go = done));
    show('/tasks?task=12');
    await waitFor(() => expect(modal()).toBeTruthy());
    const seen: (string | null)[] = [];
    const watch = new MutationObserver(() => seen.push(modal()?.getAttribute('data-pane') ?? null));
    watch.observe(modal()!, { attributes: true, attributeFilter: ['data-pane'] });
    expect(modal()!.hasAttribute('data-pane')).toBe(false);
    await act(async () => go());
    await waitFor(() => expect(modal()!.dataset.pane).toBe('chat'));
    watch.disconnect();
    expect(seen).not.toContain('task');
  });

  it('a session picked in Details raises the chat, though Task was picked', async () => {
    const router = show('/tasks?task=12');
    await waitFor(() => expect(modal()!.dataset.pane).toBe('task'));
    fireEvent.click(within(modal()!).getByRole('button', { name: 'Chat' }));
    fireEvent.click(within(modal()!).getByRole('button', { name: 'Task' }));
    expect(modal()!.dataset.pane).toBe('task');
    await act(() => router.navigate({ to: '.', search: changeWords((w) => ({ ...w, tab: 'chat', session: 'bot:a1' })) as never }));
    await waitFor(() => expect(modal()!.dataset.pane).toBe('chat'));
  });

  it('rides the keyboard while something in it is typed in: its bottom kept above the keys', async () => {
    const vv = Object.assign(new EventTarget(), { height: 768, scale: 1, offsetTop: 0 });
    vi.stubGlobal('visualViewport', vv);
    show('/tasks?task=12');
    await waitFor(() => expect(modal()!.dataset.pane).toBe('task'));
    // a field in it (the composer, a reply) typed in
    const box = document.createElement('textarea');
    modal()!.append(box);
    act(() => box.focus());
    // the keys come up: 300 of the window's 768 are under them
    vv.height = 468;
    await act(async () => {
      vv.dispatchEvent(new Event('resize'));
      await new Promise((r) => requestAnimationFrame(r));
    });
    expect(modal()!.style.getPropertyValue('--kb')).toBe('300px');
  });
});

describe('Esc', () => {
  it('folds the list pulled out over it first, leaves another layer’s and a field’s own Esc alone, then closes the task', async () => {
    const router = show('/tasks?task=12');
    await waitFor(() => expect(modal()).toBeTruthy());
    const sheet = await screen.findByRole('dialog', { name: 'Ship the order' });
    // the list pulled out (a narrow window)
    fireEvent.click(within(sheet).getByRole('button', { name: 'Show the list' }));
    expect(modal()!.classList.contains('is-list-up')).toBe(true);
    esc(sheet);
    expect(modal()!.classList.contains('is-list-up')).toBe(false);
    expect(open(router)?.task).toBe('12');
    // Cmd+K's search, a dialog of its own in its layer: its Esc is its own
    const search = document.createElement('div');
    search.setAttribute('role', 'dialog');
    sheet.append(search);
    esc(search.appendChild(document.createElement('input')));
    search.remove();
    expect(open(router)?.task).toBe('12');
    // a field that keeps its Esc (a title being edited)
    const title = document.createElement('input');
    title.addEventListener('keydown', (e) => e.preventDefault());
    sheet.append(title);
    esc(title);
    title.remove();
    expect(open(router)?.task).toBe('12');
    // nothing has the focus (a link opened cold, a file closed over it): the task's
    esc(document.body);
    await waitFor(() => expect(open(router)).toBeNull());
  });
});

describe('closed', () => {
  it('lets the last task go once it has faded: nothing drawn, nothing read for it', async () => {
    const router = show('/tasks?task=12');
    await screen.findByRole('dialog', { name: 'Ship the order' });
    await act(() => router.navigate({ to: '.', search: {} as never }));
    await waitFor(() => expect(modal()).toBeNull());
    expect(queryClient.getQueryCache().find({ queryKey: taskRefQuery('12').queryKey })?.getObserversCount() ?? 0).toBe(0);
  });

  it('goes to the person’s page history once each time a task opens, not again as its tabs change', async () => {
    const router = show('/tasks?task=12');
    await screen.findByRole('dialog', { name: 'Ship the order' });
    await act(() => router.navigate({ to: '.', search: changeWords((w) => ({ ...w, tab: 'details' })) as never }));
    await act(() => new Promise((r) => setTimeout(r, 450)));
    await act(() => router.navigate({ to: '.', search: changeWords((w) => ({ ...w, tab: 'packing-list' })) as never }));
    await act(() => new Promise((r) => setTimeout(r, 450)));
    expect(vi.mocked(recordVisit).mock.calls).toEqual([['/task/12', 'Ship the order']]);
  });
});

describe('the page under it', () => {
  it('its tab row draws nothing as a task opens over it, changes tab or goes on to the next', async () => {
    let draws = 0;
    const page = (
      <Profiler id="tabs" onRender={() => void draws++}>
        <PageTabs label="Ops" tabs={[{ label: 'Board', to: '/tasks' }, { label: 'List', to: '/tasks', search: { view: 'list' } }]} />
      </Profiler>
    );
    const router = show('/tasks', page);
    await screen.findByRole('navigation', { name: 'Ops' });
    const before = draws;
    await act(() => router.navigate({ to: '.', search: { task: 12, list: 'p1' } as never }));
    await screen.findByRole('dialog', { name: 'Ship the order' });
    await act(() => router.navigate({ to: '.', search: changeWords((w) => ({ ...w, tab: 'details' })) as never }));
    await act(() => router.navigate({ to: '.', search: { task: 13, list: 'p1' } as never }));
    expect(draws - before).toBe(0);
    cleanup();
  });
});
