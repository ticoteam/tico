import { QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
// eslint-disable-next-line no-restricted-imports -- the test hands the page a stream event, as the shell's startLive does
import { dispatch, installPatchers } from '@/live/patch';
import { dragTo } from '@/test/drag';
import { ArchivedSections } from './Archived';
import { FlowPage } from './FlowPage';
import { flowQuery, listParams, pipelinesQuery, refreshLoaded, type Pipeline, type TaskItem } from './queries';
import { TasksPage } from './TasksPage';

vi.mock('@/tico/dot', () => ({ Dot: () => <span className="dot" />, dot: { set: vi.fn(() => () => {}), clear: vi.fn(), el: () => null } }));

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
  // The shell's ActionsRoot lends the router to the action layer: a view action (Filter, View) runs through it.
  const root = createRootRoute({
    component: () => (
      <>
        <ActionsRoot dot={() => null} fold={() => undefined} />
        {node}
      </>
    ),
  });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
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
  localStorage.clear();
  queryClient.clear();
  queryClient.setQueryData(['me'], { id: ME, email: '', role: 'person', slug: null });
  queryClient.setQueryData(['directory'], { actors: [], byId: {} });
  queryClient.setQueryData(['tags'], []);
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
    { id: 'q1', kind: 'question', by: 'bot:qa', pipeline: null, href: '/threads/home-qa' },
    { id: 'a1', kind: 'ask', by: 'bot:fin', pipeline: 'finance', href: '/task/t9' },
    { id: 'a2', kind: 'ask', by: 'bot:eng', pipeline: 'eng', href: '/task/t8' },
  ]);
});

it('finishes the route’s Flow read when the shell unsubscribes', async () => {
  let respond!: (reply: Reply) => void;
  serve(() => new Promise((resolve) => { respond = resolve; }));
  const loaded = queryClient.ensureQueryData(flowQuery);
  const stop = new QueryObserver(queryClient, flowQuery).subscribe(() => {});
  stop();
  respond({ status: 200, body: { items: [] } });
  await expect(loaded).resolves.toEqual({ count: 0, items: [], requests: [], task_ids: [], approvals: [], asks: [] });
});

it('keeps the Reconcile summary on a Flow row that folds several items of one task', async () => {
  const brief = { id: 't1', number: 3, title: 'Make the shots', pipeline_id: 'p', stage_id: 's', owner_id: 'bot:ops' };
  const ask = { kind: 'ask', task_id: 't1', requirement_id: 'r1', line: 'OK the cut?', since: ago(60), task: brief };
  const shots = { kind: 'ask', task_id: 't1', requirement_id: 'paid_render:t1', line: 'Reconcile 1 shot', since: ago(30), task: brief };
  serve(() => ({ status: 200, body: { items: [ask, shots] } }));
  const flow = await queryClient.fetchQuery(flowQuery);
  expect(flow.items.map((i) => i.about)).toEqual(['Reconcile 1 shot · Make the shots']);
});

it('a board card shows its shots to reconcile apart from its turn and status line', async () => {
  serve(() => ({ status: 200, body: {} }));
  seed([task('a1', 7, 'Fix the export', 's1', { reconcile: { who: 'human:me', line: 'Reconcile 2 shots' } })]);
  show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
  expect(within(await screen.findByTitle('Fix the export')).getByText('Reconcile 2 shots')).toBeTruthy();
});

it('keeps Flow cached when returning to Tasks, where the live needs topic stays followed', async () => {
  seed([]);
  show(<FlowPage />, '/flow');
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

  it('a list row opens its task under the pipeline', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1')]);
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    const row = await screen.findByTitle('Fix the export');
    expect(row.closest('a')?.getAttribute('href')).toBe('/tasks/eng/7');
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

  it('a card and a row say which tasks they block', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([task('a1', 7, 'Fix the export', 's1', { blocks: [{ id: 'b1', number: 613, label: 'E613' }, { id: 'b2', number: 633, label: null }] })]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    expect(within(await screen.findByTitle('Fix the export')).getByText('Blocks E613, #633')).toBeTruthy();
  });

  it("a phone's head keeps Flow and \"⋯\"; New task, Search, Filter and the other view are in the \"⋯\"", async () => {
    document.documentElement.dataset.layout = 'cards';
    try {
      serve(() => ({ status: 200, body: {} }));
      seed([task('a1', 7, 'Fix the export', 's1')]);
      show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
      expect((await screen.findByRole('link', { name: 'Flow' })).hasAttribute('data-phone')).toBe(true);
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

describe('Flow @s5 @s112', () => {
  const item = { id: 'a1', number: 7, title: 'Approve the copy', pipeline_id: 'eng', stage_id: 's2', owner_id: null, late: false, stuck: false, since: ago(5) };

  it('a row opens the task in Flow, under its pipeline and stage', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    queryClient.setQueryData(['flow'], { count: 1, items: [item] });
    show(<FlowPage />, '/flow');
    const row = await screen.findByTitle('Approve the copy');
    expect(row.closest('a')?.getAttribute('href')).toBe('/flow/7');
    expect(screen.getByRole('region', { name: 'Engineering, Doing' })).toBeTruthy();
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
    show(<FlowPage />, '/flow');
    expect(await screen.findByTitle('Check the totals')).toBeTruthy();
    expect(screen.getByTitle('Sign the lease')).toBeTruthy();
    expect(screen.queryByTitle('Approve the copy')).toBeNull();
    expect(queryClient.getQueryData<{ count: number }>(['flow'])?.count).toBe(2);
    expect(asked.filter((u) => u.includes('/needs-you')).map((u) => new URL(u, 'http://x').searchParams.get('cursor'))).toEqual([null, 'v1.1', 'v2.1']);
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
        show(<FlowPage />, '/flow');
        expect((await screen.findByTitle('Pay 540.00 USD to Figma by ach')).closest('a')?.getAttribute('href')).toBe('/flow/10');
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
    show(<FlowPage />, '/flow');
    expect(await screen.findByRole('heading', { name: 'Nothing waits on you' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Tasks' }).getAttribute('href')).toBe('/tasks');
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
    send(4, { status_line: 'needs_you', turn: { ...bot, state: 'person', who: ME } });
    expect(await within(el).findByText('Needs you')).toBeTruthy();
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
