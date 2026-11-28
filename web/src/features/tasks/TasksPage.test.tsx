import { QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { createMemoryHistory, createRootRouteWithContext, createRoute, createRouter, Outlet, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
// eslint-disable-next-line no-restricted-imports -- the test hands the page a stream event, as the shell's startLive does
import { dispatch, installPatchers, resetPatchState } from '@/live/patch';
import { follow, resetFollows } from '@/live/follow';
import { forgetPlaces, hearListClicks, openTasksOver } from '@/lib';
import { dragTo } from '@/test/drag';
import type * as VirtualListModule from '@/kit/VirtualList';
import { ArchivedSections } from './Archived';
import { leaveNeedsYou, needsYouWith, needsYouWithout, settleNeedsYou, useNeedsYou } from './needs';
import { NeedsYouPage } from './NeedsYouPage';
import { NeedsYouViews } from './NeedsYouViews';
import { flowCountQuery, flowListParams, flowListQuery, flowQuery, listParams, loadMoreOf, pipelinesQuery, pipelineTasksQuery, refreshLoaded, STATUS_BACKSTOP_MS, useFlowCountRefresh, type Flow, type Pipeline, type TaskItem } from './queries';
import { TasksPage } from './TasksPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));
type VirtualLists = typeof VirtualListModule;
// The real long list, with what each draw was asked to do kept (a long Needs you measures its rows).
const virtual = vi.hoisted(() => ({ measure: [] as (boolean | undefined)[] }));
vi.mock('@/kit/VirtualList', async (orig) => {
  const real = await orig<VirtualLists>();
  return {
    ...real,
    VirtualList: (p: Parameters<typeof real.VirtualList>[0]) => {
      virtual.measure.push(p.measure);
      return real.VirtualList(p);
    },
  };
});

type Reply = { status: number; body: unknown };
let posts: { url: string; body: unknown }[] = [];
function serve(reply: (url: string, method: string) => Reply | Promise<Reply>) {
  vi.stubGlobal('fetch', async (url: string, init?: RequestInit) => {
    const method = init?.method ?? 'GET';
    if (method === 'POST') posts.push({ url: String(url), body: typeof init?.body === 'string' ? JSON.parse(init.body) : init?.body });
    const r = await reply(String(url), method);
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { 'Content-Type': 'application/json' } });
  });
}

function show(node: ReactNode, at: string) {
  const flow = at.split('?')[0] === '/needs-you';
  // The shell's ActionsRoot lends the router to the action layer: a view action (Filter, View) runs through it.
  const root = createRootRouteWithContext<{ queryClient: typeof queryClient }>()({
    component: function TestShell() {
      useFlowCountRefresh(flow);
      return (
        <>
          <ActionsRoot dot={() => null} fold={() => undefined} />
          {flow ? <Outlet /> : node}
        </>
      );
    },
  });
  // the Needs you page as its route was before it moved to the top of Tasks: Mine's list read first
  const needs = createRoute({
    getParentRoute: () => root,
    path: '/needs-you',
    validateSearch: (s: Record<string, unknown>): { view?: 'all' } => (s.view === 'all' ? { view: 'all' } : {}),
    loaderDeps: ({ search }) => ({ all: search.view === 'all' }),
    loader: ({ deps }) => (deps.all ? undefined : queryClient.ensureQueryData(flowQuery)),
    component: () => node,
  });
  const routeTree = flow ? root.addChildren([needs]) : root;
  const router = createRouter({ routeTree, context: { queryClient }, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
  return router;
}

const ME = 'human:me';
const PIPE: Pipeline = {
  id: 'eng',
  name: 'Engineering',
  stages: [
    { id: 's1', name: 'Inbox' },
    { id: 's2', name: 'Doing' },
  ],
  owner: null,
  done: { id: 'sd', name: 'Done' },
  closed: { id: 'sc', name: 'Closed' },
  mayChange: true,
};
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const task = (id: string, number: number, title: string, stage: string, extra: Partial<TaskItem> = {}): TaskItem => ({
  id,
  number,
  title,
  status: 'open',
  pipeline_id: 'eng',
  stage_id: stage,
  owner_id: null,
  labels: [],
  late: null,
  turn: null,
  updated_at: ago(30),
  stage_since: ago(30),
  version: 1,
  ...extra,
});
const listKey = keys.tasks.list(listParams('eng', {}));
const seed = (tasks: TaskItem[]) => {
  queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
  queryClient.setQueryData(listKey, { tasks, cursor: 'c1' });
  queryClient.setQueryData(['flow'], { count: 0, items: [] });
};
const stageOf = (id: string) => queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks.find((t) => t.id === id)?.stage_id;

beforeAll(() => {
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } });
  installPatchers();
});
beforeEach(() => {
  posts = [];
  // the row the person is on in Needs you, and the order it was drawn in, start over
  forgetPlaces();
  resetPatchState();
  resetFollows();
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['tags'], []);
  queryClient.setQueryData(flowCountQuery.queryKey, { count: 0, wakeAt: null });
});
afterEach(() => vi.unstubAllGlobals());

it('finishes the route’s pipeline read when the shell unsubscribes', async () => {
  let respond!: (reply: Reply) => void;
  serve(() => new Promise((resolve) => { respond = resolve; }));
  const loaded = queryClient.ensureQueryData(pipelinesQuery);
  const stop = new QueryObserver(queryClient, pipelinesQuery).subscribe(() => {});
  stop();
  respond({ status: 200, body: { pipelines: [{ id: PIPE.id, name: PIPE.name, stages: PIPE.stages }] } });
  await expect(loaded).resolves.toEqual([expect.objectContaining({ id: PIPE.id })]);
});

it('names the agents with a ❓ question or an ask waiting on the person, not those with tasks, pauses or requests @4857-S9', async () => {
  serve(() => ({
    status: 200,
    body: {
      items: [
        { id: 'q1', kind: 'question', from_id: 'bot:qa', conversation_id: 'home-qa' },
        { id: 'a1', kind: 'ask', ask: { kind: 'ok', asked_by: 'bot:fin' }, task: { id: 't9', pipeline_id: 'finance' } },
        { id: 'a2', kind: 'ask', ask: { kind: 'ok', asked_by: null }, task: { id: 't8', pipeline_id: 'eng', owner_id: 'human:me', requester_id: 'bot:eng' } },
        { id: 't1', kind: 'task', from_id: 'bot:ops' },
        { id: 'p1', kind: 'paused', bots: ['bot:ops'] },
        { id: 'r1', kind: 'access', from_id: 'bot:tools' },
      ],
    },
  }));
  expect((await queryClient.fetchQuery(flowQuery)).asks).toEqual([
    { id: 'q1', kind: 'question', by: 'bot:qa', pipeline: null, href: '/threads/home-qa', task: null, wait: 'q1' },
    { id: 'a1', kind: 'ask', by: 'bot:fin', pipeline: 'finance', href: '/task/t9', task: 't9', wait: 'a1' },
    { id: 'a2', kind: 'ask', by: 'bot:eng', pipeline: 'eng', href: '/task/t8', task: 't8', wait: 'a2' },
  ]);
});

it('finishes the route’s Needs you read when the shell unsubscribes', async () => {
  let respond!: (reply: Reply) => void;
  serve(() => new Promise((resolve) => { respond = resolve; }));
  const loaded = queryClient.ensureQueryData(flowQuery);
  const stop = new QueryObserver(queryClient, flowQuery).subscribe(() => {});
  stop();
  respond({ status: 200, body: { items: [] } });
  await expect(loaded).resolves.toEqual({ count: 0, items: [], requests: [], task_ids: [], waits: {}, approvals: [], asks: [], order: [], sorting: false, sortingUntil: null, itemIds: [] });
});

it('keeps the Reconcile summary on a Needs you row that folds several items of one task', async () => {
  const brief = { id: 't1', number: 3, title: 'Make the shots', pipeline_id: 'p', stage_id: 's', owner_id: 'bot:ops' };
  const ask = { kind: 'ask', task_id: 't1', requirement_id: 'r1', line: 'OK the cut?', since: ago(60), task: brief };
  const shots = { kind: 'ask', task_id: 't1', requirement_id: 'paid_render:t1', line: 'Reconcile 1 shot', since: ago(30), task: brief };
  serve(() => ({ status: 200, body: { items: [ask, shots] } }));
  const flow = await queryClient.fetchQuery(flowQuery);
  expect(flow.items.map((i) => i.about)).toEqual(['Reconcile 1 shot · Make the shots']);
  // the one row waits on both: an answer to the OK leaves it waiting on the shots
  expect(flow.waits).toEqual({ t1: ['r1', 'paid_render:t1'] });
});

it('a board card shows its shots to reconcile apart from its turn and status line', async () => {
  serve(() => ({ status: 200, body: {} }));
  seed([task('a1', 7, 'Fix the export', 's1', { reconcile: { who: 'human:me', line: 'Reconcile 2 shots' } })]);
  show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
  expect(within(await screen.findByTitle('Fix the export')).getByText('Reconcile 2 shots')).toBeTruthy();
});

it('keeps Needs you cached when returning to Tasks, where the live needs topic stays followed', async () => {
  seed([]);
  show(<NeedsYouPage />, '/needs-you');
  await screen.findByRole('heading', { name: 'Nothing waits on you' });
  cleanup();
  expect(queryClient.getQueryState(flowQuery.queryKey)?.isInvalidated).toBe(false);
});

describe('Tasks board and list @s0 @s9 @s10', () => {
  it('a board card opens its task under the pipeline, with a count per stage and Late only when late @s8', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's2', { late: { level: 'told', since: ago(60) } })]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    const card = await screen.findByTitle('Fix the export');
    expect(card.getAttribute('href')).toBe('/tasks/eng/7');
    expect(within(card).queryByText('Late')).toBeNull();
    expect(within(screen.getByTitle('Write the notes')).getByText('Late')).toBeTruthy();
    expect(screen.getByRole('heading', { level: 1, name: 'Tasks' })).toBeTruthy();
  });

  it('a plain press on a board card opens its task over the board, with the board as its list', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's1')]);
    const router = show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    // as the app's router does (lib openOver)
    openTasksOver(router);
    hearListClicks(document);
    // pressed with Cmd it opens elsewhere: nothing opens here
    fireEvent.click(await screen.findByTitle('Fix the export'), { metaKey: true });
    expect(router.state.location.search).toEqual({});
    fireEvent.click(screen.getByTitle('Fix the export'));
    await waitFor(() => expect(router.state.location.search).toEqual({ task: 7, list: 'eng' }));
    expect(router.state.location.pathname).toBe('/tasks/eng');
  });

  it.each([
    ['board', 'ok', 'Approving…'],
    ['list', 'no', 'Sending back…'],
  ] as const)('on the %s, a task whose answer is being sent says so and cannot be opened until its reply @approvals', async (view, how, says) => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1')]);
    show(<TasksPage pipeline="eng" search={{ view }} />, `/tasks/eng?view=${view}`);
    const shown = () => screen.getByTitle('Fix the export');
    await screen.findByTitle('Fix the export');
    act(() => void leaveNeedsYou('a1', 'r1', how));
    await waitFor(() => expect(within(shown()).getByText(says)).toBeTruthy());
    // on the list, beside the title, not in the mark's one-icon track (hidden on a phone)
    if (view === 'list') expect(within(shown()).getByText(says).closest('.row-pills')).toBeTruthy();
    expect(shown().closest('a')).toBeNull();
    expect(shown().closest('[aria-busy="true"]')).toBeTruthy();
    act(() => settleNeedsYou({ task: 'a1', wait: 'r1' }));
    await waitFor(() => expect(shown().closest('a')?.getAttribute('href')).toBe('/tasks/eng/7'));
    expect(within(shown()).queryByText(says)).toBeNull();
  });

  it('a list row opens its task under the pipeline', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1')]);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const row = await screen.findByTitle('Fix the export');
    expect(row.closest('a')?.getAttribute('href')).toBe('/tasks/eng/7');
  });

  it('a list row sits in the board’s list, so a press on it opens its task with that list', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1')]);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const row = await screen.findByTitle('Fix the export');
    expect(row.closest('[data-list]')?.getAttribute('data-list')).toBe('eng');
  });

  it('puts the stage paging buttons outside the scrolling board', async () => {
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(220);
    try {
      serve(() => ({ status: 200, body: {} }));
      seed([task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Review the export', 's2')]);
      show(<TasksPage pipeline="eng" search={{ view: 'board' }} />, '/tasks/eng?view=board');
      const next = await screen.findByRole('button', { name: 'Later stages' });
      expect(next.closest('.board-wrap')).toBeTruthy();
      expect(next.closest('.board')).toBeNull();
      fireEvent.click(next);
      expect(await screen.findByRole('button', { name: 'Earlier stages' })).toBeTruthy();
    } finally {
      width.mockRestore();
    }
  });

  it('the arrow past the edge counts what waits on you there, again as Needs you changes', async () => {
    const width = vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(220);
    try {
      serve(() => ({ status: 200, body: {} }));
      seed([task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Review the export', 's2')]);
      show(<TasksPage pipeline="eng" search={{ view: 'board' }} />, '/tasks/eng?view=board');
      const later = (await screen.findByRole('button', { name: 'Later stages' })).closest('.board-more') as HTMLElement;
      expect(later.querySelector('.col-flow')).toBeNull();
      // Needs you now names the task in Doing, past the edge; the cards and folds are as they were
      act(() => queryClient.setQueryData(['flow'], { count: 1, items: [], task_ids: ['a2'] }));
      await waitFor(() => expect(later.querySelector('.col-flow > b')?.textContent).toBe('1'));
      act(() => queryClient.setQueryData(['flow'], { count: 0, items: [], task_ids: [] }));
      await waitFor(() => expect(later.querySelector('.col-flow')).toBeNull());
    } finally {
      width.mockRestore();
    }
  });

  it('a Needs you read that names the same tasks keeps the same set, so nothing that reads it draws again', async () => {
    seed([]);
    queryClient.setQueryData(['flow'], { count: 1, items: [{ id: 'a1', title: 'Fix the export' }], task_ids: ['a1'] });
    const { result } = renderHook(() => useNeedsYou(), { wrapper: ({ children }) => <QueryClientProvider client={queryClient}>{children}</QueryClientProvider> });
    const first = result.current;
    expect(first && [...first]).toEqual(['a1']);
    act(() => queryClient.setQueryData(['flow'], { count: 1, items: [{ id: 'a1', title: 'Fix the export, renamed' }], task_ids: ['a1'] }));
    // the read's news reaches the hook a moment later
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(queryClient.getQueryData<{ items: { title: string }[] }>(['flow'])?.items[0]?.title).toBe('Fix the export, renamed');
    expect(result.current).toBe(first);
    act(() => queryClient.setQueryData(['flow'], { count: 2, items: [], task_ids: ['a1', 'a2'] }));
    await waitFor(() => expect(result.current && [...result.current]).toEqual(['a1', 'a2']));
  });

  it('a move shows at once and is taken back with the reason when the server refuses it', async () => {
    let refuse!: () => void;
    const rows = [task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's2')];
    // After a refusal the list is read again; the server still has the task where it was.
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/move'))
        return new Promise<Reply>((r) => (refuse = () => r({ status: 409, body: { error: { code: 'requirement', detail: 'Needs a review first' } } })));
      if (url.startsWith('/api/v2/tasks?')) return { status: 200, body: { tasks: rows, cursor: 'c2' } };
      return { status: 200, body: {} };
    });
    seed(rows);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const row = await screen.findByTitle('Fix the export');
    const item = row.closest('.tk-item') as HTMLElement;
    fireEvent.click(within(item).getByRole('button', { name: 'Move to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Doing' }));

    await waitFor(() => expect(stageOf('a1')).toBe('s2'));
    expect(posts[0]).toEqual({ url: '/api/v2/tasks/a1/move', body: { to: 's2', version: 1 } });
    await waitFor(() => expect(refuse).toBeTypeOf('function'));
    await act(async () => refuse());
    await waitFor(() => expect(stageOf('a1')).toBe('s1'));
    expect(await screen.findByText('Needs a review first')).toBeTruthy();
  });

  it('a card dragged onto another stage moves there and says so; onto another card it only moves', async () => {
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/move')) return { status: 200, body: { task: task('a1', 7, 'Fix the export', 's2', { version: 2 }) } };
      return { status: 404, body: {} };
    });
    seed([task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's2'), task('a3', 9, 'Plan the week', 's1')]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    const card = (title: string) => screen.getByTitle(title).closest('.tk-item') as HTMLElement;
    await screen.findByTitle('Fix the export');
    const column = (name: string) => screen.getByRole('region', { name });

    // its own stage, or let go nowhere: nothing moves
    dragTo(card('Fix the export'), column('Inbox'));
    dragTo(card('Fix the export'), null);
    expect(posts).toEqual([]);

    dragTo(card('Fix the export'), column('Doing').querySelector('.col-head'));
    await waitFor(() => expect(posts).toEqual([{ url: '/api/v2/tasks/a1/move', body: { to: 's2', version: 1 } }]));
    expect(await screen.findByText('Moved #7 to Doing')).toBeTruthy();
    expect(document.querySelector('[data-drop], .is-drag')).toBeNull();

    // over another card it lands in that card's column: a move, never a subtask or a merge
    posts = [];
    dragTo(card('Plan the week'), screen.getByTitle('Write the notes'));
    await waitFor(() => expect(posts).toEqual([{ url: '/api/v2/tasks/a3/move', body: { to: 's2', version: 1 } }]));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.querySelector('[data-drop], .is-drag')).toBeNull();
  });

  it('says so when the pipeline has nothing open, offering New task, and keeps every stage head', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Nothing in Engineering' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'New task' }).length).toBeGreaterThan(0);
    expect(screen.getByRole('region', { name: 'Inbox' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Doing' })).toBeTruthy();
  });

  it('the empty list keeps the stages folded on one line under the line and New task', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([]);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    expect(await screen.findByRole('heading', { name: 'Nothing in Engineering' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: /Inbox/ }).closest('.stage-line')).toBeTruthy();
  });

  it('what waits on you leads its stage past the cap, on the board and in the list; a bulk move keeps the stage order', async () => {
    serve(() => ({ status: 200, body: { batch_id: 'b', moved: [], moved_unseen: 0, skipped: [] } }));
    const turn = (who: string) => ({ state: 'person' as const, who, line: 'Approve it', requirement_id: null, since: null, until: null, ask: false });
    const many = Array.from({ length: 13 }, (_, i) => task(`a${i + 1}`, i + 1, `Task ${i + 1}`, 's1'));
    many[11] = task('a12', 12, 'Ana approves', 's1', { turn: turn('human:ana') });
    many[12] = task('a13', 13, 'You approve', 's1', { turn: turn(ME) });
    const titles = () => screen.getAllByTitle(/^(Task \d+|Ana approves|You approve)$/).map((el) => el.getAttribute('title'));
    for (const view of ['board', 'list'] as const) {
      seed(many);
      // what waits on you is what Needs you names
      queryClient.setQueryData(['flow'], { count: 1, items: [], task_ids: ['a13'] });
      show(<TasksPage pipeline="eng" search={{ view }} />, `/tasks/eng?view=${view}`);
      await screen.findByTitle('You approve');
      // Only yours leads: a turn that is someone else's keeps its place.
      expect(titles().slice(0, 2)).toEqual(['You approve', 'Task 1']);
      expect(titles()).not.toContain('Ana approves'); // still past the cap, behind "more"
      cleanup();
    }
    seed(many);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'More, Inbox' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Close all' }));
    await waitFor(() => expect(posts.length).toBe(1));
    expect(posts[0]!.body).toEqual({ ids: many.map((x) => x.id), to: 'closed' });
  });

  it('a card and a row say which tasks they block', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1', { blocks: [{ id: 'b1', number: 613, label: 'E613' }, { id: 'b2', number: 633, label: null }] })]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    expect(within(await screen.findByTitle('Fix the export')).getByText('Blocks E613, #633')).toBeTruthy();
  });

  it("a phone's head keeps \"⋯\": New task, Search, Filter and the other view are in it", async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => ({ status: 200, body: {} }));
      seed([task('a1', 7, 'Fix the export', 's1')]);
      show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
      await screen.findByTitle('Fix the export');
      expect(screen.queryByRole('link', { name: 'Flow' })).toBeNull();
      expect(screen.queryByRole('textbox', { name: 'Search tasks' })).toBeNull();
      fireEvent.click(document.querySelector('[data-target="tasks:more"]') as HTMLElement);
      for (const name of ['New task', 'Search', 'List', 'Archived', 'Select']) expect(await screen.findByRole('menuitem', { name })).toBeTruthy();
      expect(screen.getByText('Filter')).toBeTruthy();
      fireEvent.click(screen.getByRole('menuitem', { name: 'Search' }));
      expect(await screen.findByRole('textbox', { name: 'Search tasks' })).toBeTruthy();
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });

  it('shows the filters set in the address as chips', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(keys.tasks.list(listParams('eng', { late: true })), { tasks: [], cursor: 'c1' });
    seed([]);
    show(<TasksPage pipeline="eng" search={{ late: true }} />, '/tasks/eng?late=true');
    expect(await screen.findByRole('button', { name: 'Clear Late' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeTruthy();
  });

  it('Filter › Late puts the filter in the address @s17', async () => {
    serve(() => ({ status: 200, body: { tasks: [], cursor: 'c1' } }));
    seed([]);
    const router = show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    fireEvent.click(await screen.findByRole('button', { name: 'Filter' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Late' }));
    await waitFor(() => expect(router.state.location.search).toMatchObject({ late: true }));
  });

  it('reads tags when the filter opens', async () => {
    queryClient.removeQueries({ queryKey: ['tags'] });
    const get = vi.fn((url: string) => ({ status: 200, body: url.includes('/tags?') ? { tags: [{ key: 'review' }] } : {} }));
    serve(get);
    seed([]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    const filter = await screen.findByRole('button', { name: 'Filter' });
    expect(get).not.toHaveBeenCalledWith(expect.stringContaining('/api/v2/tags?'), 'GET');
    fireEvent.click(filter);
    expect(await screen.findByRole('menuitem', { name: 'Tag' })).toBeTruthy();
    expect(get.mock.calls.filter(([url]) => url.includes('/api/v2/tags?'))).toHaveLength(1);
  });
});

describe('Tasks: more of the page @s0 @s9 @s10', () => {
  const rows = () => [task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's1')];

  it('Archived shows snoozed, done and closed tasks, each naming its stage @s12 @s13 @s14', async () => {
    const reads: URLSearchParams[] = [];
    serve((url) => {
      if (url.startsWith('/api/v2/tasks?')) reads.push(new URL(url, 'http://localhost').searchParams);
      if (url.includes('snoozed=only')) return { status: 200, body: { tasks: [task('z1', 3, 'Try the new font', 's1', { snoozed: true })], cursor: 'c' } };
      // Done holds more than one page: its count is the server's total
      if (url.includes('status=done'))
        return { status: 200, body: { tasks: [task('d1', 2, 'Ship the price page', 'sd', { status: 'done' })], cursor: 'c', next_after: 'x', counts: { total: 340, stage: {} } } };
      if (url.includes('status=closed')) return { status: 200, body: { tasks: [], cursor: 'c' } };
      return { status: 200, body: {} };
    });
    seed([]);
    show(<TasksPage pipeline="eng" search={{ view: 'archived' }} />, '/tasks/eng?view=archived');
    expect(await screen.findByRole('heading', { level: 1, name: 'Archived' })).toBeTruthy();
    const done = await screen.findByRole('region', { name: 'Done' });
    const row = within(done).getByTitle('Ship the price page');
    expect(row.getAttribute('href')).toBe('/tasks/eng/2');
    expect(within(row).getByText('Done')).toBeTruthy();
    expect(within(done).getByText('340')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Snoozed' })).getByText('Inbox')).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'Closed' })).toBeNull();
    expect(reads.map((params) => params.get('brief'))).toEqual(['true', 'true', 'true']);
  });

  it('positions an expanded phone archive using the measured row heights', async () => {
    const viewportHeight = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockImplementation(function (this: Element) {
      return this.classList.contains('tk-long') ? 528 : 0;
    });
    const height = vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('vl-item')) return this.dataset.index === '0' ? 68 : 44;
      return 0;
    });
    document.documentElement.dataset.layout = 'cards';
    try {
      const archived = Array.from({ length: 101 }, (_, i) => task(`d${i}`, i + 1, `Archived task ${i + 1}`, 'sd', { status: 'done' }));
      const empty = { tasks: [], cursor: 'c' };
      show(<ArchivedSections p={PIPE} filters={{}} shelves={{ snoozed: empty, done: { ...empty, tasks: archived }, closed: empty }} />, '/tasks/eng');
      fireEvent.click(await screen.findByRole('button', { name: '91 more' }));
      const [, second] = screen.getAllByRole('listitem');
      expect(second?.style.transform).toContain(', 68px,');
    } finally {
      delete document.documentElement.dataset.layout;
      height.mockRestore();
      viewportHeight.mockRestore();
    }
  });

  it('keeps the Archived filter in the phone header', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => ({ status: 200, body: { tasks: [], cursor: null } }));
      seed([]);
      const router = show(<TasksPage pipeline="eng" search={{ view: 'archived' }} />, '/tasks/eng?view=archived');
      const filter = await screen.findByRole('button', { name: 'Filter' });
      expect(filter.hasAttribute('data-phone')).toBe(true);
      fireEvent.click(filter);
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Late' }));
      await waitFor(() => expect(router.state.location.search).toMatchObject({ view: 'archived', late: true }));
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });

  it('keeps Select controls reachable on the phone list', async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => ({ status: 200, body: {} }));
      seed(rows());
      show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
      fireEvent.click(await screen.findByRole('button', { name: 'More' }));
      fireEvent.click(await screen.findByRole('menuitem', { name: 'Select' }));
      const bar = await screen.findByRole('group', { name: 'Selected tasks' });
      expect(bar.hasAttribute('data-phone')).toBe(true);
      fireEvent.click(screen.getByTitle('Fix the export'));
      expect(within(bar).getByRole('button', { name: 'Change' })).toBeTruthy();
      fireEvent.click(within(bar).getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByRole('group', { name: 'Selected tasks' })).toBeNull();
    } finally {
      delete document.documentElement.dataset.layout;
    }
  });

  it('Select picks rows instead of opening them; a bulk move shows at once and comes back when the server skips it', async () => {
    let skip!: () => void;
    serve((url, method) => {
      if (method === 'POST' && url === '/api/v2/tasks/move')
        return new Promise<Reply>(
          (r) =>
            (skip = () => r({ status: 200, body: { batch_id: 'b', moved: [], moved_unseen: 0, skipped: [{ code: 'requirement', id: 'a1', reason: 'Needs a review first' }] } })),
        );
      if (url.startsWith('/api/v2/tasks?')) return { status: 200, body: { tasks: rows(), cursor: 'c2' } };
      return { status: 200, body: {} };
    });
    seed(rows());
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Select' }));
    const bar = await screen.findByRole('group', { name: 'Selected tasks' });
    expect(within(bar).getByText('0 selected')).toBeTruthy();
    const row = screen.getByTitle('Fix the export').closest('a') as HTMLElement;
    fireEvent.click(row);
    expect(within(bar).getByText('1 selected')).toBeTruthy();
    expect(row.getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(within(bar).getByRole('button', { name: 'Change' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Move to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Doing' }));
    await waitFor(() => expect(stageOf('a1')).toBe('s2'));
    expect(posts[0]).toEqual({ url: '/api/v2/tasks/move', body: { ids: ['a1'], to: 's2' } });
    await waitFor(() => expect(skip).toBeTypeOf('function'));
    await act(async () => skip());
    await waitFor(() => expect(stageOf('a1')).toBe('s1'));
    expect(await screen.findByText('Needs a review first')).toBeTruthy();
  });

  it("a stage's ⋯ closes every task in it, with Undo", async () => {
    serve((url, method) => {
      if (method === 'POST' && url === '/api/v2/tasks/move')
        return { status: 200, body: { batch_id: 'b', moved: ['a1', 'a2'], moved_unseen: 0, skipped: [] } };
      return { status: 200, body: {} };
    });
    seed(rows());
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'More, Inbox' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Close all' }));
    await waitFor(() => expect(screen.queryByTitle('Fix the export')).toBeNull());
    expect(posts[0]).toEqual({ url: '/api/v2/tasks/move', body: { ids: ['a1', 'a2'], to: 'closed' } });
    expect(await screen.findByText('Closed 2 tasks')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Undo' })).toBeTruthy();
  });

  it('undoes only the tasks the bulk close actually moved', async () => {
    serve((url, method) => {
      if (method === 'POST' && url === '/api/v2/tasks/move')
        return {
          status: 200,
          body: { batch_id: 'b', moved: ['a1'], moved_unseen: 0, skipped: [{ code: 'task_turn_running', id: 'a2', reason: 'A turn is running' }] },
        };
      if (url.startsWith('/api/v2/tasks?')) return { status: 200, body: { tasks: [rows()[1]], cursor: 'c2' } };
      return { status: 200, body: {} };
    });
    seed(rows());
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'More, Inbox' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Close all' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Undo' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toEqual({ ids: ['a1'], to: 'reopen' });
  });

  it("search's New task lands with the box up; closing it takes new=1 out of the address", async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's2')]);
    const router = show(<TasksPage pipeline="eng" search={{ new: 1 }} />, '/tasks/eng?new=1');
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Title')).toBeTruthy();
    fireEvent.keyDown(within(dialog).getByLabelText('Title'), { key: 'Escape' });
    await waitFor(() => expect(router.state.location.search).not.toHaveProperty('new'));
  });

  it("a stage's + files the task, then moves it into that stage @s24", async () => {
    serve((url, method) => {
      if (method === 'POST' && url === '/api/v2/tasks') return { status: 200, body: { task: task('n1', 9, 'Check the copy', 's1') } };
      if (method === 'POST' && url === '/api/v2/tasks/n1/move') return { status: 200, body: { task: task('n1', 9, 'Check the copy', 's2', { version: 2 }) } };
      return { status: 200, body: {} };
    });
    seed([task('a1', 7, 'Fix the export', 's2')]);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'New task in Doing' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Title'), { target: { value: 'Check the copy' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(posts.map((x) => x.url)).toEqual(['/api/v2/tasks', '/api/v2/tasks/n1/move']));
    expect(posts[1]?.body).toEqual({ to: 's2', version: 1 });
    await waitFor(() => expect(stageOf('n1')).toBe('s2'));
  });

  it("the list read asks for stage counts; a column shows the server's count, 0 when its stage is not in it", async () => {
    const reads: string[] = [];
    serve((url) => {
      if (url.startsWith('/api/v2/tasks?')) {
        reads.push(url);
        return { status: 200, body: { tasks: rows(), cursor: 'c1', next_after: 'x', counts: { total: 1200, stage: { s1: 1200 } } } };
      }
      return { status: 200, body: {} };
    });
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    const inbox = await screen.findByRole('region', { name: 'Inbox' });
    await waitFor(() => expect(within(inbox).getByText('1200')).toBeTruthy());
    const params = new URLSearchParams(reads[0]?.split('?')[1]);
    expect(params.get('counts')).toBe('stage');
    expect(params.get('brief')).toBe('true');
    expect(within(screen.getByRole('region', { name: 'Doing' })).getByText('0')).toBeTruthy();
  });

  it('opens the next server page when no stage has enough rows to virtualize', async () => {
    const reads: string[] = [];
    serve((url) => {
      if (!url.includes('after=next')) return { status: 200, body: {} };
      reads.push(url);
      return { status: 200, body: { tasks: [task('a2', 8, 'Review the export', 's2')], cursor: 'c2' } };
    });
    seed([]);
    queryClient.setQueryData(listKey, {
      tasks: [task('a1', 7, 'Fix the export', 's1')],
      cursor: 'c1',
      next_after: 'next',
      counts: { total: 2, stage: { s1: 1, s2: 1 } },
      counted: { s1: 1 },
    });
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const more = await screen.findByRole('button', { name: 'More tasks' });
    expect(within(screen.getByRole('heading', { name: /Doing/ })).getByText('1')).toBeTruthy();
    fireEvent.click(more);
    expect(await screen.findByRole('link', { name: /Review the export/ })).toBeTruthy();
    expect(reads.map((url) => new URL(url, 'http://localhost').searchParams.get('brief'))).toEqual(['true']);
    expect(screen.queryByRole('button', { name: 'More tasks' })).toBeNull();
  });

  it('keeps the loaded tasks and allows another attempt when a page fails', async () => {
    let reads = 0;
    serve((url) => {
      if (!url.includes('after=next')) return { status: 200, body: {} };
      if (++reads === 1) return { status: 500, body: {} };
      return { status: 200, body: { tasks: [task('a2', 8, 'Review the export', 's2')], cursor: 'c2' } };
    });
    seed([]);
    queryClient.setQueryData(listKey, { tasks: [task('a1', 7, 'Fix the export', 's1')], cursor: 'c1', next_after: 'next' });
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const more = await screen.findByRole('button', { name: 'More tasks' });
    fireEvent.click(more);
    expect(await screen.findByText("Couldn't read more")).toBeTruthy();
    expect(screen.getByTitle('Fix the export')).toBeTruthy();
    fireEvent.click(more);
    expect(await screen.findByTitle('Review the export')).toBeTruthy();
  });

  it("a move changes both stages' counts at once, and the next read sets them", async () => {
    let answer!: (r: Reply) => void;
    let counts = { s1: 40, s2: 7 };
    const held = [task('a1', 7, 'Fix the export', 's1'), task('a3', 9, 'Check the copy', 's1'), task('a2', 8, 'Write the notes', 's2')];
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/move')) return new Promise<Reply>((r) => (answer = r));
      if (url.startsWith('/api/v2/tasks?')) return { status: 200, body: { tasks: held, cursor: 'c1', next_after: 'x', counts: { total: 47, stage: counts } } };
      return { status: 200, body: {} };
    });
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const inbox = await screen.findByRole('region', { name: 'Inbox' });
    await waitFor(() => expect(within(inbox).getByText('40')).toBeTruthy());
    const item = screen.getByTitle('Fix the export').closest('.tk-item') as HTMLElement;
    fireEvent.click(within(item).getByRole('button', { name: 'Move to' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Doing' }));
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Inbox' })).getByText('39')).toBeTruthy());
    expect(within(screen.getByRole('region', { name: 'Doing' })).getByText('8')).toBeTruthy();
    counts = { s1: 39, s2: 9 };
    await act(async () => answer({ status: 200, body: { task: task('a1', 7, 'Fix the export', 's2', { version: 2 }) } }));
    await act(async () => queryClient.invalidateQueries({ queryKey: listKey }));
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Doing' })).getByText('9')).toBeTruthy());
  });

  it('shows Waiting on, Asked by and Due as chips', async () => {
    serve(() => ({ status: 200, body: {} }));
    const f = { waiting: 'human:x', due: 'week' as const };
    queryClient.setQueryData(keys.tasks.list(listParams('eng', f)), { tasks: [], cursor: 'c1' });
    seed([]);
    show(<TasksPage pipeline="eng" search={f} />, '/tasks/eng');
    expect(await screen.findByRole('button', { name: 'Clear Waiting on someone' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Clear Due this week' })).toBeTruthy();
  });
});

describe('Needs you @s5 @s112', () => {
  const item = { id: 'a1', number: 7, title: 'Approve the copy', pipeline_id: 'eng', stage_id: 's2', owner_id: null, late: false, stuck: false, since: ago(5) };
  // what waits on you and its Snoozed view (a link in its head on Tasks), side by side
  const MineAndSnoozed = () => (
    <>
      <NeedsYouPage />
      <NeedsYouViews view="snoozed" />
    </>
  );

  it('keeps the top 5 rows on the page live while Tico sorts, applies the order when the push lands below the row you are on', async () => {
    const tasks = [1, 2, 3, 4, 5, 6].map((n) => `t${n}`);
    const order0 = ['r1', 't1', 't2', 't3', 't4', 't5', 't6'];
    const need = (id: string) => id === 'r1'
      ? { kind: 'access', id: 'r1', from_id: 'bot:tools', line: 'Tools asks for the repo', since: ago(70), access: { id: 'x1', subject: 'bot:tools', subject_name: 'Tools', resource_name: 'the repo', level: 'read' } }
      : { kind: 'ask', id: `v:${id}`, task_id: id, requirement_id: `r${id}`, line: '', since: ago(60),
        task: { id, number: tasks.indexOf(id) + 1, title: `Task ${tasks.indexOf(id) + 1}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null, ...(id === 't5' || id === 't1' ? { entity_id: 'organization/acme' } : {}) } };
    let order = order0;
    let sorting = true;
    serve((url) => {
      if (url.endsWith('/entities/names')) return { status: 200, body: { names: { 'organization/acme': 'Acme' } } };
      if (!url.includes('/needs-you?limit=')) return { status: 200, body: { items: [] } };
      return { status: 200, body: { items: order.map(need), next: null, sorting, sorting_until: sorting ? new Date(Date.now() + 60_000).toISOString() : null } };
    });
    show(<NeedsYouPage />, '/needs-you');
    const item = async (n: number) => (await screen.findByTitle(`Task ${n}`)).closest('.flow-item')!;
    const shown = () => [...document.querySelectorAll('.flow-item')].map((r) => r.querySelector('[title^="Task "]')?.getAttribute('title') ?? 'request');
    const dimmed = () => [...document.querySelectorAll('.flow-item')].map((r) => r.classList.contains('flow-wait'));
    const status = await screen.findByText('Tico is sorting…');
    expect(status.closest('.flow-mine')).toBeNull(); // in the page's header line, not above the rows
    await item(6);
    // One list in Tico's order, the request among the rows: the first five on the page are live, whatever their kind.
    expect(shown()).toEqual(['request', 'Task 1', 'Task 2', 'Task 3', 'Task 4', 'Task 5', 'Task 6']);
    expect(dimmed()).toEqual([false, false, false, false, false, true, true]);
    await waitFor(() => expect(within(screen.getByTitle('Task 5')).getByText('Acme')).toBeTruthy());

    fireEvent.pointerDown((await item(2)).querySelector('a')!); // the person is on Task 2
    order = ['t6', 't5', 't4', 't3', 'r1', 't2', 't1'];
    sorting = false;
    act(() => dispatch('needs', [{ seq: 2, count: 7 }]));
    await waitFor(() => expect(screen.queryByText('Tico is sorting…')).toBeNull());
    expect(document.querySelector('.flow-sorting')).toBeTruthy(); // still there, empty: nothing below it moves
    // The rows down to Task 2 stay where they were; only the rows below it take the new order.
    expect(shown()).toEqual(['request', 'Task 1', 'Task 2', 'Task 6', 'Task 5', 'Task 4', 'Task 3']);
    expect(dimmed().some(Boolean)).toBe(false);
  });

  it('only the Needs you page asks Tico to sort: the shared list read and its refresh never do', async () => {
    const urls: string[] = [];
    const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    serve((url) => {
      urls.push(url);
      return { status: 200, body: url.includes('/needs-you?') ? { items: [1, 2].map(need), next: null, sorting: false } : { items: [] } };
    });
    const sorts = () => urls.filter((u) => u.endsWith('/needs-you/sort')).length;
    const stop = new QueryObserver(queryClient, flowQuery).subscribe(() => {}); // Tools, a task's chat, the Tracker
    await waitFor(() => expect(urls.some((u) => u.includes('/needs-you?limit='))).toBe(true));
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] })); // the five-minute refresh
    expect(urls.filter((u) => u.includes('/needs-you?limit=200')).length).toBe(2);
    expect(sorts()).toBe(0);
    stop();
    show(<NeedsYouPage />, '/needs-you');
    expect(await screen.findByTitle('Task 2')).toBeTruthy();
    await waitFor(() => expect(sorts()).toBe(1));
    const reads = urls.length;
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] })); // a push or the refresh reads the list again
    expect(urls.length).toBeGreaterThan(reads);
    expect(sorts()).toBe(1); // the same items: no second ask
  });

  it('shows the list live again when a sort’s claim runs out with no push, and reads it again, even if that read fails', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout'] });
    try {
      let reads = 0;
      const until = new Date(Date.now() + 60_000).toISOString();
      const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
        task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
      serve((url) => {
        if (!url.includes('/needs-you?limit=')) return { status: 200, body: { items: [] } };
        // The read again fails, and for good: a retryable failure would be read again for a minute (api/client.ts
        // RETRY_FOR_MS), past this test and into the next ones' mocks.
        if (++reads > 1) return { status: 500, body: { error: { code: 'internal', detail: 'The read failed', retryable: false } } };
        return { status: 200, body: { items: [1, 2, 3, 4, 5, 6].map(need), next: null, sorting: true, sorting_until: until } };
      });
      show(<NeedsYouPage />, '/needs-you');
      await vi.waitFor(() => expect(screen.getByText('Tico is sorting…')).toBeTruthy());
      expect(document.querySelector('.flow-wait')).toBeTruthy();
      await act(() => vi.advanceTimersByTimeAsync(60_000)); // the claim runs out, with no push
      await vi.waitFor(() => expect(reads).toBeGreaterThan(1));
      expect(screen.queryByText('Tico is sorting…')).toBeNull();
      expect(document.querySelector('.flow-wait')).toBeNull();
    } finally {
      cleanup();
      vi.useRealTimers();
    }
  });

  it('asks Tico to sort again when a task already on the list asks a second thing', async () => {
    const urls: string[] = [];
    const ask = (n: number, id = `v:t${n}`) => ({ kind: 'ask', id, task_id: `t${n}`, requirement_id: id, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    let items = [ask(1), ask(2)];
    serve((url) => {
      urls.push(url);
      return { status: 200, body: url.includes('/needs-you?') ? { items, next: null, sorting: false } : { items: [] } };
    });
    const sorts = () => urls.filter((u) => u.endsWith('/needs-you/sort')).length;
    show(<NeedsYouPage />, '/needs-you');
    expect(await screen.findByTitle('Task 2')).toBeTruthy();
    await waitFor(() => expect(sorts()).toBe(1));
    items = [ask(1), ask(1, 'v:t1:second'), ask(2)]; // the same two rows, three things asked
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] }));
    await waitFor(() => expect(sorts()).toBe(2));
  });

  it('a slow answer to the ask to sort never dims a list whose newer order is already shown', async () => {
    let answer!: (r: Reply) => void;
    const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    serve((url) => {
      if (url.endsWith('/needs-you/sort')) return new Promise<Reply>((r) => (answer = r));
      return { status: 200, body: url.includes('/needs-you?') ? { items: [1, 2, 3, 4, 5, 6].map(need), next: null, sorting: false } : { items: [] } };
    });
    show(<NeedsYouPage />, '/needs-you');
    expect(await screen.findByTitle('Task 6')).toBeTruthy();
    await waitFor(() => expect(answer).toBeTruthy());
    // That sort ran and finished before its answer came: the list read since says it is done.
    await act(async () => answer({ status: 200, body: { sorting: true, sorting_until: new Date(Date.now() + 60_000).toISOString() } }));
    await waitFor(() => expect(queryClient.getQueriesData({ queryKey: ['needs-you-sort'] }).map(([, d]) => d)).toEqual([expect.objectContaining({ sorting: true })]));
    expect(screen.queryByText('Tico is sorting…')).toBeNull();
    expect(document.querySelector('.flow-wait')).toBeNull();
  });

  it('lets go of the row the person was on once they click or focus anything outside the list', async () => {
    const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    let order = [1, 2, 3];
    serve((url) => ({ status: 200, body: url.includes('/needs-you?') ? { items: order.map(need), next: null, sorting: false } : { items: [] } }));
    show(<NeedsYouPage />, '/needs-you');
    const shown = () => [...document.querySelectorAll('.flow-item [title^="Task "]')].map((r) => r.getAttribute('title'));
    fireEvent.pointerDown((await screen.findByTitle('Task 3')).closest('.flow-item')!.querySelector('a')!); // the last row
    order = [3, 2, 1];
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] }));
    expect(shown()).toEqual(['Task 1', 'Task 2', 'Task 3']); // held: the rows down to it stay
    fireEvent.pointerDown(document.body); // then somewhere else on the page: the new order at once, no read
    expect(shown()).toEqual(['Task 3', 'Task 2', 'Task 1']);
  });

  it('moving up to another row keeps only the rows down to it; the rest take the latest order', async () => {
    const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    let order = [1, 2, 3, 4, 5, 6];
    serve((url) => ({ status: 200, body: url.includes('/needs-you?') ? { items: order.map(need), next: null, sorting: false } : { items: [] } }));
    show(<NeedsYouPage />, '/needs-you');
    const shown = () => [...document.querySelectorAll('.flow-item [title^="Task "]')].map((r) => r.getAttribute('title'));
    const row = async (n: number) => (await screen.findByTitle(`Task ${n}`)).closest('.flow-item')!.querySelector('a')!;
    fireEvent.pointerDown(await row(3));
    order = [6, 5, 4, 3, 2, 1];
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] }));
    await waitFor(() => expect(shown()).toEqual(['Task 1', 'Task 2', 'Task 3', 'Task 6', 'Task 5', 'Task 4']));
    fireEvent.pointerDown(await row(1)); // up to the first row
    expect(shown()).toEqual(['Task 1', 'Task 6', 'Task 5', 'Task 4', 'Task 3', 'Task 2']);
  });

  it('keeps the row the person is on drawn when a new item pushes the list past its cap', async () => {
    const need = (n: number) => ({ kind: 'ask', id: `v:t${n}`, task_id: `t${n}`, requirement_id: `r${n}`, line: '', since: ago(60 - n),
      task: { id: `t${n}`, number: n, title: `Task ${n}`, pipeline_id: 'eng', stage_id: 's2', owner_id: null } });
    let order = Array.from({ length: 12 }, (_, i) => i + 1);
    serve((url) => ({ status: 200, body: url.includes('/needs-you?') ? { items: order.map(need), next: null, sorting: false } : { items: [] } }));
    show(<NeedsYouPage />, '/needs-you');
    fireEvent.pointerDown((await screen.findByTitle('Task 12')).closest('.flow-item')!.querySelector('a')!); // the last row
    order = [13, ...order];
    await act(() => queryClient.refetchQueries({ queryKey: ['flow'] }));
    await waitFor(() => expect(queryClient.getQueryData<{ count: number }>(['flow'])?.count).toBe(13));
    expect(screen.getByTitle('Task 12')).toBeTruthy();
  });

  it('lists Mine as one list, each row with its relationship', async () => {
    serve(() => ({ status: 200, body: { names: { 'organization/acme': 'Acme', 'organization/acme-partners': 'Acme' } } }));
    const entity = { entity_id: 'organization/acme' };
    queryClient.setQueryData(['flow'], { count: 4, items: [
      { ...item, ...entity, reason: 'Needs your approval' },
      { ...item, ...entity, id: 'a2', number: 8, title: 'Check the delivery', pipeline_id: 'support', stage_id: 'inbox' },
      { ...item, ...entity, entity_id: 'organization/acme-partners', id: 'a3', number: 9, title: 'Sign the lease' },
      { ...item, id: 'a4', number: 10, title: 'Review the agenda' },
    ] });
    show(<NeedsYouPage />, '/needs-you');
    const row = await screen.findByTitle('Approve the copy');
    expect(row.closest('a')?.getAttribute('href')).toBe('/task/7');
    expect(within(row).getByText('Needs your approval')).toBeTruthy();
    await waitFor(() => expect(within(row).getByText('Acme')).toBeTruthy());
    expect(within(screen.getByTitle('Sign the lease')).getByText('Acme')).toBeTruthy();
    expect(within(screen.getByTitle('Review the agenda')).queryByText('Acme')).toBeNull();
    expect(screen.queryByRole('region', { name: 'Acme' })).toBeNull();
    expect(posts).toEqual([{ url: '/api/v2/entities/names', body: { ids: ['organization/acme', 'organization/acme-partners'] } }]);
  });

  it('answers a ❓ in place: its answers and a reply box under the row, a tap sends one and the row leaves Needs you', async () => {
    const question = { kind: 'question', id: 'qmark:q1', task_id: 't1', from_id: 'bot:gm', line: '❓ Switch the HeyGen check to the new address?', detail: '1. Keep the old one as a fallback?', choices: ['Yes', 'Not now'], since: ago(5),
      task: { id: 't1', number: 7, title: 'Fix the HeyGen check', pipeline_id: 'eng', stage_id: 's2', owner_id: 'bot:gm' } };
    let answered = false;
    serve((url, method) => {
      if (method === 'POST' && url === '/api/v2/tasks/t1/comments') {
        answered = true;
        return { status: 200, body: { comment: { id: 'm9' } } };
      }
      // Needs you read again after the answer never comes back: the row leaves without it
      if (url.includes('/needs-you?limit=')) return answered ? new Promise<Reply>(() => {}) : { status: 200, body: { items: [question], next: null } };
      return { status: 200, body: { items: [] } };
    });
    show(<NeedsYouPage />, '/needs-you');
    const row = await screen.findByTitle('Fix the HeyGen check');
    expect(row.closest('a')?.getAttribute('href')).toBe('/task/7'); // the title still opens the task
    expect(within(row).queryByText(/Switch the HeyGen check/)).toBeNull(); // the card asks it, not the row
    const card = screen.getByRole('region', { name: 'Question' });
    expect(within(card).getByText('Switch the HeyGen check to the new address?')).toBeTruthy();
    expect(within(card).getByText('1. Keep the old one as a fallback?')).toBeTruthy(); // the whole question, not its first line
    expect(within(card).getByRole('textbox', { name: 'Reply' })).toBeTruthy();
    fireEvent.click(within(card).getByRole('button', { name: 'Yes' }));
    await waitFor(() => expect(screen.queryByTitle('Fix the HeyGen check')).toBeNull());
    expect(queryClient.getQueryData<{ count: number }>(flowQuery.queryKey)?.count).toBe(0);
    expect(posts).toContainEqual({ url: '/api/v2/tasks/t1/comments', body: { text: 'Yes', ask: 'qmark:q1' } });
  });

  it('a ❓ answered on a task that waits on another keeps its row, and its card shows the next ❓', () => {
    const one = { id: 'qmark:q1', line: 'Switch it?', choices: ['Yes'], by: 'bot:gm' };
    const two = { id: 'qmark:q2', line: 'Keep the old one?', choices: [], by: 'bot:gm' };
    const row = { ...item, id: 't1', reason: 'Switch it?', questions: [one, two] };
    const read = { count: 1, items: [row], waits: { t1: ['qmark:q1', 'qmark:q2'] }, task_ids: ['t1'], order: ['t1'] };
    const after = needsYouWithout('t1', 'qmark:q1', read)(read) as typeof read;
    expect(after.items.map((w) => [w.id, w.questions, w.reason])).toEqual([['t1', [two], undefined]]);
    expect(after.waits.t1).toEqual(['qmark:q2']);
    expect(after.count).toBe(1);
  });

  it('the menu count takes the list\'s own count when an answer leaves it, so a live count read meanwhile never falls twice', () => {
    const q = (id: string) => ({ ...item, id, number: null, questions: [{ id: `qmark:${id}`, line: 'Ship it?', choices: [], by: 'bot:gm' }] });
    queryClient.setQueryData(flowQuery.queryKey, { count: 2, items: [q('t1'), q('t2')], waits: { t1: ['qmark:t1'], t2: ['qmark:t2'] }, task_ids: ['t1', 't2'], order: ['t1', 't2'] });
    // a live event already read the count without the answered ❓; the list still has its row
    queryClient.setQueryData(flowCountQuery.queryKey, { count: 1, wakeAt: null });
    leaveNeedsYou('t1', 'qmark:t1');
    expect(queryClient.getQueryData<{ count: number }>(flowQuery.queryKey)?.count).toBe(1);
    expect(queryClient.getQueryData<{ count: number }>(flowCountQuery.queryKey)?.count).toBe(1);
  });

  it('two answers in flight each keep their own row out of a list read that lands meanwhile, until their own reply @approvals', async () => {
    const need = (id: string, n: number) => ({ kind: 'ask', id: `v:${id}`, task_id: id, requirement_id: `r-${id}`, line: 'Send it?', since: ago(5),
      task: { id, number: n, title: `Task ${n}`, pipeline_id: 'eng' }, ask: { kind: 'ok', requirement_id: `r-${id}`, asked_by: 'bot:so' } });
    // what the server lists: all three until it takes an answer
    let listed = ['t1', 't2', 't3'];
    serve((url) => (url.includes('/needs-you?limit=') ? { status: 200, body: { items: listed.map((id, n) => need(id, n + 1)), next: null } } : { status: 200, body: { items: [] } }));
    const ids = () => queryClient.getQueryData<{ task_ids: string[] }>(flowQuery.queryKey)?.task_ids;
    const readAgain = () => queryClient.refetchQueries({ queryKey: flowQuery.queryKey, exact: true });
    await queryClient.fetchQuery(flowQuery);
    const undoFirst = leaveNeedsYou('t1', 'r-t1');
    leaveNeedsYou('t2', 'r-t2');
    expect(ids()).toEqual(['t3']);
    await readAgain(); // the five-minute read, begun after both presses, lands before either reply
    expect(ids()).toEqual(['t3']);
    // the first refused: its row is the server's again; the second is still being sent, and stays out
    undoFirst();
    settleNeedsYou();
    await readAgain();
    expect(ids()).toEqual(['t1', 't3']);
    // the second taken: the read after its reply is the server's own
    listed = ['t1', 't3'];
    settleNeedsYou({ task: 't2', wait: 'r-t2' });
    await readAgain();
    expect(ids()).toEqual(['t1', 't3']);
  });

  it('an answer being sent takes out only what it answers: a read that adds another ask on the task keeps its row @approvals', async () => {
    const need = (id: string, rid: string) => ({ kind: 'ask', id: `v:${rid}`, task_id: id, requirement_id: rid, line: 'Send it?', since: ago(5),
      task: { id, number: 1, title: 'Task 1', pipeline_id: 'eng' }, ask: { kind: 'ok', requirement_id: rid, asked_by: 'bot:so' } });
    let listed = [need('t1', 'r1')];
    serve((url) => (url.includes('/needs-you?limit=') ? { status: 200, body: { items: listed, next: null } } : { status: 200, body: { items: [] } }));
    const read = () => queryClient.getQueryData<{ task_ids: string[]; waits: Record<string, string[]> }>(flowQuery.queryKey);
    await queryClient.fetchQuery(flowQuery);
    leaveNeedsYou('t1', 'r1'); // its only ask then: the row goes
    expect(read()?.task_ids).toEqual([]);
    listed = [need('t1', 'r1'), need('t1', 'r2')]; // a second ask on it arrives while the first answer is sent
    await queryClient.refetchQueries({ queryKey: flowQuery.queryKey, exact: true });
    expect(read()?.task_ids).toEqual(['t1']);
    expect(read()?.waits.t1).toEqual(['r2']);
  });

  describe('another ask on the task arriving while an answer is sent @approvals', () => {
    const need = (id: string, rid: string) => ({ kind: 'ask', id: `v:${rid}`, task_id: id, requirement_id: rid, line: `Send ${rid}?`, since: ago(5),
      task: { id, number: 1, title: 'Task 1', pipeline_id: 'eng' }, ask: { kind: 'ok', requirement_id: rid, asked_by: 'bot:so' } });
    let listed: ReturnType<typeof need>[] = [];
    let down = false;
    const serveList = () =>
      serve((url) => (!url.includes('/needs-you?limit=') ? { status: 200, body: { items: [] } } : down ? { status: 400, body: { error: 'down' } } : { status: 200, body: { items: listed, next: null } }));
    const readAgain = () => act(() => queryClient.refetchQueries({ queryKey: flowQuery.queryKey, exact: true }));
    beforeEach(() => {
      listed = [need('t1', 'r1')];
      down = false;
    });

    it.each([
      ['Approve', 'ok', 'Approving…'],
      ['Request changes', 'no', 'Sending back…'],
    ] as const)('%s: the row kept listed by the new ask says so and opens nothing until the reply', async (_, how, says) => {
      serveList();
      show(<NeedsYouPage />, '/needs-you');
      const row = () => document.querySelector('.flow-row .task');
      await waitFor(() => expect(row()?.closest('a')).toBeTruthy());
      act(() => void leaveNeedsYou('t1', 'r1', how));
      listed = [need('t1', 'r1'), need('t1', 'r2')];
      await readAgain();
      await waitFor(() => expect(within(row() as HTMLElement).getByText(says)).toBeTruthy());
      expect(row()?.closest('a')).toBeNull();
      expect(row()?.getAttribute('aria-busy')).toBe('true');
      act(() => settleNeedsYou({ task: 't1', wait: 'r1' }));
      await waitFor(() => expect(row()?.closest('a')).toBeTruthy());
      expect(within(row() as HTMLElement).queryByText(says)).toBeNull();
    });

    it.each([['ok'], ['no']] as const)('%s refused, the read after it failing: the refused ask comes back and the new one stays', async (how) => {
      serveList();
      const read = () => queryClient.getQueryData<Flow>(flowQuery.queryKey);
      await queryClient.fetchQuery(flowQuery);
      const undo = leaveNeedsYou('t1', 'r1', how);
      listed = [need('t1', 'r1'), need('t1', 'r2')];
      await readAgain();
      expect(read()?.waits?.t1).toEqual(['r2']);
      down = true;
      act(() => {
        undo();
        settleNeedsYou();
      });
      await waitFor(() => expect(queryClient.getQueryState(flowQuery.queryKey)?.fetchStatus).toBe('idle'));
      expect(read()?.waits?.t1).toEqual(['r1', 'r2']);
      expect(read()?.task_ids).toEqual(['t1']);
      expect(read()?.approvals?.filter((a) => a.id === 't1').map((a) => a.ask.requirement)).toEqual(['r1', 'r2']);
      // the row stands for the refused ask again, as it did before the press
      expect(read()?.items.find((w) => w.id === 't1')?.ask?.requirement).toBe('r1');
    });

    it.each([['ok'], ['no']] as const)('%s refused after a ❓ on the task was answered elsewhere, the read after it failing: the row comes back without the ❓', async (how) => {
      const question = { kind: 'question', id: 'qmark:q1', task_id: 't1', from_id: 'bot:so', line: 'Cc the landlord too?', choices: ['Yes'], since: ago(4),
        task: { id: 't1', number: 1, title: 'Task 1', pipeline_id: 'eng' } } as unknown as ReturnType<typeof need>;
      listed = [need('t1', 'r1'), question];
      serveList();
      const read = () => queryClient.getQueryData<Flow>(flowQuery.queryKey);
      await queryClient.fetchQuery(flowQuery);
      expect(read()?.items.find((w) => w.id === 't1')?.questions?.map((q) => q.id)).toEqual(['qmark:q1']);
      const undo = leaveNeedsYou('t1', 'r1', how);
      listed = [need('t1', 'r1')]; // another session answers the ❓; the approval being sent keeps the task out
      await readAgain();
      expect(read()?.items.some((w) => w.id === 't1')).toBe(false);
      down = true;
      act(() => {
        undo();
        settleNeedsYou();
      });
      await waitFor(() => expect(queryClient.getQueryState(flowQuery.queryKey)?.fetchStatus).toBe('idle'));
      expect(read()?.waits?.t1).toEqual(['r1']);
      const back = read()?.items.find((w) => w.id === 't1');
      expect(back?.ask?.requirement).toBe('r1');
      expect(back?.questions ?? []).toEqual([]); // the answered ❓ stays gone: no card for it under the row
      expect(back?.reason).not.toBe('Cc the landlord too?');
      expect(read()?.count).toBe(1);
    });
  });

  it('refused after the list moved on, for a task only a grouped row named: its Dot comes back @approvals', () => {
    const before = { count: 1, items: [], requests: [], task_ids: ['t1', 't3'], waits: { t1: ['r1'], t3: ['r3'] }, approvals: [], asks: [] } as unknown as Flow;
    const cur = { count: 1, items: [], requests: [], task_ids: ['t3', 't4'], waits: { t3: ['r3'], t4: ['r4'] }, approvals: [], asks: [] } as unknown as Flow;
    const back = needsYouWith('t1', 'r1', before, cur);
    expect(back.task_ids).toEqual(['t1', 't3', 't4']);
    expect(back.waits).toEqual({ t1: ['r1'], t3: ['r3'], t4: ['r4'] });
    expect(back.count).toBe(1); // a grouped task is no row of its own
  });

  it('refused after the list moved on and dropped its row: the row put back holds only what still waits @approvals', () => {
    const row = { id: 't1', number: 1, title: 'Task 1', reason: 'Which day?', ask: { kind: 'ok', by: 'bot:so', shows: [], requirement: 'r1' },
      questions: [{ id: 'qmark:q1', line: 'Which day?', choices: [], by: 'bot:so' }] };
    const r2 = { id: 't1', number: 1, title: 'Task 1', reason: 'Send r2?', ask: { kind: 'ok', by: 'bot:so', shows: [], requirement: 'r2' } };
    const before = { count: 1, items: [row], requests: [], task_ids: ['t1'], order: ['t1'], waits: { t1: ['r1', 'qmark:q1', 'r2'] }, approvals: [row, r2], asks: [] } as unknown as Flow;
    const cur = { count: 0, items: [], requests: [], task_ids: [], order: [], waits: {}, approvals: [], asks: [] } as unknown as Flow;
    // the ❓ was refused (the approvals were answered elsewhere): the row is the ❓ alone
    const q = needsYouWith('t1', 'qmark:q1', before, cur).items[0];
    expect(q?.ask).toBeUndefined();
    expect(q?.questions?.map((x) => x.id)).toEqual(['qmark:q1']);
    // r2 was refused: the row stands for r2, with no ❓ and not the ❓'s line
    const back = needsYouWith('t1', 'r2', before, cur);
    expect(back.items[0]?.ask?.requirement).toBe('r2');
    expect(back.items[0]?.questions).toBeUndefined();
    expect(back.items[0]?.reason).toBe('Send r2?');
    expect(back.waits?.t1).toEqual(['r2']);
  });

  it('a long Needs you, opened past its cap, measures each row it draws, so a ❓ card never overlaps the rows below', async () => {
    virtual.measure.length = 0;
    const rows = Array.from({ length: 120 }, (_, n) => ({ ...item, id: `t${n}`, number: n + 1, title: `Task ${n + 1}` }));
    rows[0] = { ...rows[0]!, questions: [{ id: 'qmark:q1', line: 'Ship it on Friday?', choices: ['Yes'], by: 'bot:gm' }] } as (typeof rows)[number];
    queryClient.setQueryData(flowQuery.queryKey, { count: rows.length, items: rows, order: rows.map((r) => r.id) });
    serve(() => ({ status: 200, body: { items: [] } }));
    show(<NeedsYouPage />, '/needs-you');
    fireEvent.click(await screen.findByRole('button', { name: '110 more' }));
    await waitFor(() => expect(virtual.measure.length).toBeGreaterThan(0));
    expect(virtual.measure.every((m) => m === true)).toBe(true); // a computer as a phone: no fixed 34 px rows
  });

  it('shows a task\'s ❓ under its row even when another item on the task comes first in the order', async () => {
    const task = { id: 't1', number: 7, title: 'Reply to Dana', pipeline_id: 'eng', stage_id: 's2', owner_id: 'bot:so' };
    const email = { kind: 'ask', id: 'ask:t1', task_id: 't1', requirement_id: 'r-mail', line: 'Send this email?', since: ago(9),
      ask: { requirement_id: 'r-mail', kind: 'ok', line: 'Send this email?', met: false, may: ['ok', 'no'], shows: ['email:s1'], asked_by: 'bot:so', answer: null }, task };
    const question = { kind: 'question', id: 'qmark:q1', task_id: 't1', from_id: 'bot:so', line: 'Cc the landlord too?', choices: ['Yes', 'No'], since: ago(5), task };
    serve((url) => (url.includes('/needs-you?limit=') ? { status: 200, body: { items: [email, question], next: null } } : { status: 200, body: { items: [] } }));
    show(<NeedsYouPage />, '/needs-you');
    const row = await screen.findByTitle('Reply to Dana');
    expect(within(row).getByText('Send this email?')).toBeTruthy(); // what the row stands for still shows
    expect(within(screen.getByRole('region', { name: 'Question' })).getByText('Cc the landlord too?')).toBeTruthy();
  });

  it('All (its link picks it) lists claimable work and explains a row on demand; no tabs, no archive of its own', async () => {
    const reads: string[] = [];
    const relationship = { entity_id: 'organization/acme' };
    const active = task('a2', 8, 'Help with the order', 's1', { ...relationship });
    serve((url) => {
      reads.push(url);
      if (url.endsWith('/entities/names')) return { status: 200, body: { names: { [relationship.entity_id]: 'Acme' } } };
      if (url.includes('/origins/')) return { status: 200, body: { cause: { type: 'automation', name: 'Support intake' } } };
      if (url.includes('claimable=true')) return { status: 200, body: { tasks: [active] } };
      return { status: 200, body: { items: [] } };
    });
    show(<NeedsYouPage />, '/needs-you?view=all');
    expect((await screen.findByTitle('Help with the order')).getAttribute('href')).toBe('/task/8');
    expect(screen.getByRole('heading', { level: 1, name: 'All' })).toBeTruthy();
    // one list, each row's relationship a chip on it, never a section of its own
    await waitFor(() => expect(within(screen.getByTitle('Help with the order')).getByText('Acme')).toBeTruthy());
    expect(screen.queryByRole('region', { name: 'Acme' })).toBeNull();
    expect(within(screen.getByRole('region', { name: 'Tasks' })).getByTitle('Help with the order')).toBeTruthy();
    // the link in Needs you's head on Tasks picked the view; Snoozed and Done are its other links
    for (const name of ['Mine', 'All']) expect(screen.queryByRole('link', { name })).toBeNull();
    for (const name of ['Snoozed', 'Done', 'Hide archive']) expect(screen.queryByRole('button', { name })).toBeNull();
    expect(reads.some((u) => u.includes('/needs-you/sort'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: 'Why here for Help with the order' }));
    expect(await within(await screen.findByRole('dialog', { name: 'Why here' })).findByText('Support intake')).toBeTruthy();
  });

  it('opens All independently of Needs you and keeps later pages when a task is completed live', async () => {
    queryClient.removeQueries({ queryKey: flowCountQuery.queryKey });
    const active = task('a1', 7, 'Approve the copy', 's1');
    let done = false;
    let reads = 0;
    let needsReads = 0;
    serve((url) => {
      const search = new URL(url, 'http://localhost').searchParams;
      if (search.has('count')) return { status: 200, body: { count: 3, flow_count: 2 } };
      if (!search.has('claimable')) {
        needsReads += 1;
        return { status: 503, body: {} };
      }
      reads += 1;
      return {
        status: 200,
        body: search.has('after')
          ? { tasks: [task('a3', 9, 'Review the agenda', 's1')], cursor: done ? '2.read' : '1.read' }
          : { tasks: [...(done ? [] : [active]), task('a2', 8, 'Check the totals', 's1')], next_after: 'page-2', cursor: done ? '2.read' : '1.read' },
      };
    });
    show(<NeedsYouPage />, '/needs-you?view=all');
    expect(await screen.findByTitle('Approve the copy')).toBeTruthy();
    await waitFor(() => expect(queryClient.getQueryData<{ count: number }>(flowCountQuery.queryKey)?.count).toBe(2));
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    expect(await screen.findByTitle('Review the agenda')).toBeTruthy();
    done = true;
    act(() => dispatch('tasks', [{ seq: 2, id: active.id, kind: 'update', task: { ...active, status: 'done', version: 2 } }]));
    await waitFor(() => expect(screen.queryByTitle('Approve the copy')).toBeNull());
    expect(screen.getByTitle('Check the totals')).toBeTruthy();
    expect(screen.getByTitle('Review the agenda')).toBeTruthy();
    expect(reads).toBe(4);
    expect(needsReads).toBe(0);
  });

  it('removes a reassigned task from Snoozed on a live event', async () => {
    const held = task('a3', 9, 'Send the quote', 's1', { owner_id: ME, snoozed: true });
    let reassigned = false;
    serve((url) => {
      if (url.includes('snoozed=only')) return { status: 200, body: { tasks: reassigned ? [] : [held], cursor: '1.read' } };
      return { status: 200, body: { items: [] } };
    });
    show(<NeedsYouViews view="snoozed" />, '/needs-you?view=snoozed');
    expect(await screen.findByTitle('Send the quote')).toBeTruthy();
    reassigned = true;
    act(() => dispatch('tasks', [{ seq: 2, id: held.id, kind: 'update', task: { ...held, owner_id: 'human:teammate', version: 2 } }]));
    await waitFor(() => expect(screen.queryByTitle('Send the quote')).toBeNull());
  });

  it('refreshes All after a task completes during its first read', async () => {
    const active = task('a1', 7, 'Approve the copy', 's1');
    let finishFirst: ((reply: Reply) => void) | undefined;
    let reads = 0;
    serve(() => {
      reads += 1;
      if (reads > 1) return { status: 200, body: { tasks: [], cursor: '2.read' } };
      return new Promise((resolve) => { finishFirst = resolve; });
    });
    show(<NeedsYouPage />, '/needs-you?view=all');
    await waitFor(() => expect(finishFirst).toBeDefined());
    await act(async () => {
      dispatch('tasks', [{ seq: 2, id: active.id, kind: 'update', task: { ...active, status: 'done', version: 2 } }]);
      finishFirst?.({ status: 200, body: { tasks: [active], cursor: '1.read' } });
    });
    expect(await screen.findByText('No work to claim')).toBeTruthy();
    expect(reads).toBe(2);
  });

  it('drops a revoked relationship\'s chip when Contacts is read again', async () => {
    const related = { ...item, entity_id: 'organization/acme-shop' };
    const shelf = task('a3', 9, 'Send the quote', 's1', { owner_id: ME, snoozed: true, entity_id: related.entity_id });
    let visible = true;
    let taskReads = 0;
    serve((url) => {
      if (url.endsWith('/entities/names')) return { status: 200, body: { names: visible ? { [related.entity_id]: 'Acme Shop' } : {} } };
      if (url.includes('/tasks?')) {
        taskReads++;
        return { status: 200, body: { tasks: [shelf] } };
      }
      if (url.includes('/needs-you?limit=')) {
        taskReads++;
        return { status: 200, body: { items: [{ kind: 'task', id: 'n1', task: related }] } };
      }
      return { status: 200, body: { items: [] } };
    });
    show(<MineAndSnoozed />, '/needs-you');
    await waitFor(() => expect(screen.getAllByText('Acme Shop')).toHaveLength(2));
    visible = false;
    await act(() => queryClient.invalidateQueries({ queryKey: ['contacts', 'names'] }));
    await waitFor(() => expect(screen.queryByText('Acme Shop')).toBeNull());
    expect(within(screen.getByRole('region', { name: 'Needs you' })).getByTitle('Approve the copy')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'Tasks' })).getByTitle('Send the quote')).toBeTruthy();
    expect(taskReads).toBe(2);
  });

  it('refreshes claimable work when availability changes', async () => {
    const held = task('a1', 7, 'Help with the order', 's1');
    let off = true;
    serve((url) => ({
      status: 200,
      body: url.includes('claimable=true') ? { tasks: off ? [held] : [] } : { items: [] },
    }));
    show(<NeedsYouPage />, '/needs-you?view=all');
    expect(await screen.findByTitle('Help with the order')).toBeTruthy();
    off = false;
    act(() => dispatch('setup', [{ area: 'availability' }]));
    expect(await screen.findByText('No work to claim')).toBeTruthy();
  });

  it('marks cached Needs you pages stale when the all-task subscription ends', async () => {
    vi.useFakeTimers();
    try {
      const queryKey = flowListQuery(flowListParams(undefined, 'open')).queryKey;
      queryClient.setQueryData(queryKey, { pages: [{ tasks: [], cursor: '1.read' }], pageParams: [null] });
      const stop = follow({ allTasks: true });
      stop();
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(false);
      await vi.advanceTimersByTimeAsync(5_000);
      expect(queryClient.getQueryState(queryKey)?.isInvalidated).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('brings back an ask from Snoozed even when someone else owns the task', async () => {
    const ask = {
      id: 'ask:pay',
      kind: 'ask',
      line: 'Approve the transfer',
      since: ago(5),
      snoozed_until: '2099-01-01T00:00:00Z',
      task: { ...item, owner_id: 'human:teammate' },
    };
    let snoozed = true;
    serve((url, method) => {
      if (method === 'POST' && url.endsWith('/needs-you/unsnooze')) {
        snoozed = false;
        return { status: 200, body: {} };
      }
      if (url.endsWith('/needs-you/snoozed')) return { status: 200, body: { items: snoozed ? [ask] : [] } };
      if (url.includes('/needs-you?limit=')) return { status: 200, body: { items: snoozed ? [] : [ask] } };
      return { status: 200, body: url.includes('/tasks?') ? { tasks: [] } : { count: +!snoozed } };
    });
    show(<MineAndSnoozed />, '/needs-you');
    const shelf = await screen.findByRole('region', { name: 'Asks' });
    expect(await within(shelf).findByTitle('Approve the transfer')).toBeTruthy();
    fireEvent.click(within(shelf).getByRole('button', { name: 'Unsnooze' }));
    expect(await screen.findByTitle('Approve the copy')).toBeTruthy();
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Asks' })).toBeNull());
    expect(posts).toEqual([{ url: '/api/v2/needs-you/unsnooze', body: { id: ask.id } }]);
  });

  it('refreshes Mine and Snoozed at the API’s next expiry', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout'] });
    try {
      queryClient.removeQueries({ queryKey: flowCountQuery.queryKey });
      const expiry = 65_000;
      const until = new Date(Date.now() - 60_000).toISOString(); // the browser clock is ahead
      const ask = { id: 'ask:pay', kind: 'ask', line: 'Approve the transfer', snoozed_until: until, task: item };
      let countdowns = 0;
      serve((url) => {
        const snoozed = performance.now() < expiry;
        if (url.includes('count=true')) {
          countdowns++;
          return { status: 200, body: { flow_count: +!snoozed, snooze_refresh_in_ms: snoozed ? (countdowns === 1 ? 60_000 : expiry - performance.now()) : null } };
        }
        if (url.endsWith('/needs-you/snoozed')) return { status: 200, body: { items: snoozed ? [ask] : [], next: snoozed ? until : null } };
        if (url.includes('/needs-you?limit=')) return { status: 200, body: { items: snoozed ? [] : [ask], snoozed_until: snoozed ? until : null } };
        return { status: 200, body: url.includes('/tasks?') ? { tasks: [] } : { flow_count: +!snoozed } };
      });
      show(<MineAndSnoozed />, '/needs-you');
      await vi.waitFor(() => expect(screen.getByTitle('Approve the transfer')).toBeTruthy());
      expect(screen.queryByTitle('Approve the copy')).toBeNull();
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      expect(countdowns).toBe(2);
      expect(screen.getByTitle('Approve the transfer')).toBeTruthy();
      await act(() => vi.advanceTimersByTimeAsync(5_000));
      await vi.waitFor(() => {
        expect(screen.getByTitle('Approve the copy')).toBeTruthy();
        // Snoozed holds no ask now (its Asks go)
        expect(screen.queryByRole('region', { name: 'Asks' })).toBeNull();
      });
      expect(posts).toEqual([]);
    } finally {
      cleanup();
      vi.useRealTimers();
    }
  });

  it('refreshes the count on All at expiry and on the five-minute backstop', async () => {
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout'] });
    try {
      queryClient.removeQueries({ queryKey: flowCountQuery.queryKey });
      let count = 0;
      serve((url) => url.includes('count=true')
        ? { status: 200, body: { flow_count: ++count, snooze_refresh_in_ms: count === 1 ? 60_000 : null } }
        : { status: 200, body: { tasks: [] } });
      show(<NeedsYouPage />, '/needs-you?view=all');
      await vi.waitFor(() => expect(queryClient.getQueryData<{ count: number }>(flowCountQuery.queryKey)?.count).toBe(1));
      await act(() => vi.advanceTimersByTimeAsync(60_000));
      await vi.waitFor(() => expect(queryClient.getQueryData<{ count: number }>(flowCountQuery.queryKey)?.count).toBe(2));
      await act(() => vi.advanceTimersByTimeAsync(STATUS_BACKSTOP_MS));
      await vi.waitFor(() => expect(queryClient.getQueryData<{ count: number }>(flowCountQuery.queryKey)?.count).toBe(3));
    } finally {
      cleanup();
      vi.useRealTimers();
    }
  });

  it('reads every page of what waits on you, starting over when the list moved between pages', async () => {
    const need = (id: string, number: number, title: string) => ({ id: `n-${id}`, kind: 'task', line: title, since: ago(5), task: { ...item, id, number, title } });
    const asked: string[] = [];
    let moved = true;
    serve((url) => {
      asked.push(url);
      if (!url.includes('/needs-you')) return { status: 200, body: {} };
      if (!url.includes('cursor=')) return { status: 200, body: { actor: ME, items: [need('a1', 7, 'Approve the copy')], next: 'v1.1' } };
      if (moved) {
        moved = false;
        return { status: 200, body: { actor: ME, items: [need('a3', 9, 'Sign the lease')], next: 'v2.1', restart: true } };
      }
      return { status: 200, body: { actor: ME, items: [need('a2', 8, 'Check the totals')], next: null } };
    });
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    show(<NeedsYouPage />, '/needs-you');
    expect(await screen.findByTitle('Check the totals')).toBeTruthy();
    expect(screen.getByTitle('Sign the lease')).toBeTruthy();
    expect(screen.queryByTitle('Approve the copy')).toBeNull();
    expect(queryClient.getQueryData<{ count: number }>(['flow'])?.count).toBe(2);
    expect(asked.filter((u) => u.includes('/needs-you?')).map((u) => new URL(u, 'http://x').searchParams.get('cursor'))).toEqual([null, 'v1.1', 'v2.1']);
  });

  it("an ask's row says what it is about, on a desktop and a phone, whichever of its task's items comes first", async () => {
    const task = { ...item, id: 'a4', number: 10, title: 'OK a Finance action' };
    const mention = { id: 'n-m4', kind: 'mention', line: 'Can you look?', since: ago(9), task };
    const pay = { id: 'n-a4', kind: 'ask', line: 'OK this in Finance?', about: 'Pay 540.00 USD to Figma by ach', since: ago(5), task };
    serve((url) => (url.includes('/needs-you') ? { status: 200, body: { actor: ME, items: [mention, pay], next: null } } : { status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    for (const layout of ['', 'cards']) {
      if (layout) document.documentElement.dataset.layout = layout;
      try {
        queryClient.removeQueries({ queryKey: ['flow'] });
        show(<NeedsYouPage />, '/needs-you');
        expect((await screen.findByTitle('Pay 540.00 USD to Figma by ach')).closest('a')?.getAttribute('href')).toBe('/task/10');
        expect(screen.queryByTitle('OK a Finance action')).toBeNull();
        // One row, named by the ask's `about`, keeping its oldest item's own fields (no ask: a mention's).
        const flow = queryClient.getQueryData<{ count: number; items: { about?: string; ask?: unknown }[] }>(['flow']);
        expect(flow?.count).toBe(1);
        expect(flow?.items[0]?.ask).toBeUndefined();
        cleanup();
      } finally {
        delete document.documentElement.dataset.layout;
      }
    }
  });

  it('says so when nothing waits on you', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show(<NeedsYouPage />, '/needs-you');
    expect(await screen.findByRole('heading', { name: 'Nothing waits on you' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Tasks' }).getAttribute('href')).toBe('/tasks');
    // a plain list under its own name: no Flow to enter or leave
    expect(screen.getByRole('heading', { level: 1, name: 'Needs you' })).toBeTruthy();
    expect(screen.queryByRole('link', { name: 'Flow' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Exit Flow' })).toBeNull();
  });
});

describe('an approved email on its card @s0', () => {
  afterEach(() => vi.useRealTimers());

  it('the card shows its send from its own row: Sending with the time left, then Sent when the stream sends the row again', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(Date.parse('2026-11-19T13:20:00Z'));
    const sending = { id: 's1', state: 'pending' as const, send_after: '2026-11-19T13:21:40Z', finished_at: null, detail: '' };
    // No memory on this device: the row the server sent carries the send, for every device and every person.
    const card = task('a1', 7, 'Acme lease: reply letter', 's1', { email_send: sending });
    const reads: string[] = [];
    serve((url) => {
      reads.push(url);
      return { status: 200, body: url.includes('/api/v2/tasks?') ? { tasks: queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks ?? [], cursor: '9' } : {} };
    });
    seed([card]);
    show(<TasksPage pipeline="eng" search={{ view: 'board' }} />, '/tasks/eng?view=board');
    const el = (await screen.findByTitle('Acme lease: reply letter')).closest('.card') as HTMLElement;
    expect(await within(el).findByText('Sending · 1:40')).toBeTruthy();
    const at = '2026-11-19T13:21:41Z';
    act(() => dispatch('tasks', [{ seq: 2, id: 'a1', kind: 'email_send', kinds: ['email_send'], task: { ...card, email_send: { ...sending, state: 'sent', finished_at: at } } }]));
    const time = new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' }).format(new Date(at));
    expect(await within(el).findByText(`Sent ${time}`)).toBeTruthy();
    expect(reads.filter((u) => u.includes('/email/sends/'))).toEqual([]);
  });

  it('a card shows its one status line, and the next one when the stream sends the row again', async () => {
    serve((url) => ({ status: 200, body: url.includes('/api/v2/tasks?') ? { tasks: queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks ?? [], cursor: '1' } : {} }));
    const bot = { state: 'bot' as const, who: 'bot:b1', line: 'Fix the export', requirement_id: null, since: null, until: null, ask: false };
    const card = task('a1', 7, 'Fix the export', 's1', { owner_id: 'bot:b1', turn: bot, status_line: 'up_next' });
    seed([card]);
    show(<TasksPage pipeline="eng" search={{ view: 'board' }} />, '/tasks/eng?view=board');
    const el = (await screen.findByTitle('Fix the export')).closest('.card') as HTMLElement;
    expect(within(el).getByText('Up next')).toBeTruthy();
    const send = (seq: number, more: Partial<TaskItem>) =>
      act(() => dispatch('tasks', [{ seq, id: 'a1', kind: 'run', kinds: ['run'], task: { ...card, version: seq, ...more } }]));
    send(2, { status_line: 'working' });
    expect(await within(el).findByText('Working')).toBeTruthy();
    // Needs you is said only to the person it waits on: no "You" mark for anyone else.
    send(3, { status_line: 'needs_you', turn: { ...bot, state: 'person', who: 'human:ana' } });
    await waitFor(() => expect(within(el).queryByText('Working')).toBeNull());
    expect(el.querySelector('.status-line')).toBeNull();
    // One dot, one meaning: a turn that is yours says Needs you only once Needs you names the task.
    send(4, { status_line: 'needs_you', turn: { ...bot, state: 'person', who: ME } });
    await waitFor(() => expect(queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks[0]?.version).toBe(4));
    expect(el.querySelector('.status-line')).toBeNull();
    act(() => queryClient.setQueryData(['flow'], { count: 1, items: [], task_ids: ['a1'] }));
    expect(await within(el).findByText('Needs you')).toBeTruthy();
    act(() => queryClient.setQueryData(['flow'], { count: 0, items: [], task_ids: [] }));
    await waitFor(() => expect(el.querySelector('.status-line')).toBeNull());
    send(5, { status_line: 'paused', turn: { ...bot, state: 'paused', line: 'Birch has no computer', href: '#/settings/computers' } });
    const line = await within(el).findByTitle('Birch has no computer');
    expect(line.textContent).toBe('Paused · Fix');
    // Fix is a link of its own beside the card's (a link may not hold a link), so the keyboard reaches it.
    const fix = screen.getByRole('link', { name: 'Fix: Birch has no computer' });
    expect(fix.getAttribute('href')).toBe('/settings/computers');
    expect(fix.closest('a.card, .card')).toBeNull();
    expect(fix.closest('.tk-item')).toBe(el.closest('.tk-item'));
    // Stopped by the ladder with no line: the Stopped mark stays.
    send(6, { status_line: null, stuck: true });
    expect(await within(el).findByTitle('Stopped')).toBeTruthy();
    expect(el.querySelector('.status-line')).toBeNull();
    // Stopped stays beside a line too.
    send(7, { status_line: 'working', stuck: true, turn: bot });
    expect(await within(el).findByText('Working')).toBeTruthy();
    expect(within(el).getByTitle('Stopped')).toBeTruthy();
  });

  it("the backstop reads a list's loaded rows again in place, every page kept", async () => {
    const rows = [task('a1', 7, 'One', 's1', { status_line: 'up_next' }), task('a2', 8, 'Two', 's1'), task('a3', 9, 'Three', 's2')];
    seed(rows);
    const reads: string[] = [];
    serve((url) => {
      reads.push(url);
      return { status: 200, body: { tasks: [{ ...rows[0], status_line: 'paused' }], cursor: '2' } };
    });
    await refreshLoaded(listParams('eng', {}));
    const held = queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks ?? [];
    expect(held.map((t) => t.id)).toEqual(['a1', 'a2', 'a3']);
    expect(held[0]?.status_line).toBe('paused');
    expect(new URLSearchParams(reads[0]?.split('?')[1]).get('ids')).toBe('a1,a2,a3');
  });

  it('the backstop puts back only the status, and leaves a row the stream changed while it read', async () => {
    const rows = [task('a1', 7, 'One', 's1', { status_line: 'up_next' }), task('a2', 8, 'Two', 's1', { status_line: 'up_next' })];
    seed(rows);
    let answer!: (r: Reply) => void;
    serve(() => new Promise<Reply>((done) => (answer = done)));
    const read = refreshLoaded(listParams('eng', {}));
    // The stream changes a2 while the read is out.
    const streamed = { ...rows[1]!, status_line: 'working' as const };
    queryClient.setQueryData<{ tasks: TaskItem[]; cursor: string }>(listKey, (cur) => cur && { ...cur, tasks: [cur.tasks[0]!, streamed] });
    await vi.waitFor(() => expect(answer).toBeTruthy());
    answer({ status: 200, body: { tasks: [{ ...rows[0], title: 'Renamed', status_line: 'paused' }, { ...rows[1], status_line: 'up_next' }], cursor: '2' } });
    await read;
    const held = queryClient.getQueryData<{ tasks: TaskItem[] }>(listKey)?.tasks ?? [];
    expect([held[0]?.title, held[0]?.status_line]).toEqual(['One', 'paused']);
    expect(held[1]?.status_line).toBe('working');
  });
});

describe('a list in made order (the list beside an open task, Newest)', () => {
  it('starts again from its first page for a live insert while it has more: a More on its way is dropped, so no row goes missing', async () => {
    const filters = { sort: 'newest' as const };
    const key = keys.tasks.list(listParams('eng', filters));
    let page2: (r: Reply) => void = () => {};
    serve((url) => {
      if (url.includes('after=k8')) return new Promise<Reply>((r) => (page2 = r));
      // the first page as it is now: the new task on top moved the page's end up a row
      return { status: 200, body: { tasks: [task('t10', 10, 'Ten', 'todo'), task('t9', 9, 'Nine', 'todo')], cursor: '3', next_after: 'k9' } };
    });
    queryClient.setQueryData(key, { tasks: [task('t9', 9, 'Nine', 'todo'), task('t8', 8, 'Eight', 'todo')], cursor: '1', next_after: 'k8' });
    const watch = new QueryObserver(queryClient, { ...pipelineTasksQuery('eng', filters), staleTime: Infinity });
    const stop = watch.subscribe(() => {});
    try {
      const ids = () => queryClient.getQueryData<{ tasks: TaskItem[] }>(key)?.tasks.map((t) => t.id);
      const more = loadMoreOf(listParams('eng', filters));
      act(() => dispatch('tasks', [{ seq: 2, id: 't10', kind: 'create', kinds: ['create'], task: task('t10', 10, 'Ten', 'todo') }]));
      await waitFor(() => expect(ids()).toEqual(['t10', 't9']));
      // the More read from the old end lands after the list started again: dropped, and 8 is still to read from k9
      page2({ status: 200, body: { tasks: [task('t7', 7, 'Seven', 'todo')], next_after: null } });
      await more;
      expect(ids()).toEqual(['t10', 't9']);
      expect(queryClient.getQueryData<{ next_after: string }>(key)?.next_after).toBe('k9');
    } finally {
      stop();
    }
  });
});
