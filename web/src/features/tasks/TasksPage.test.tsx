import { QueryClientProvider, QueryObserver } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ActionsRoot } from '@/actions';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FlowPage } from './FlowPage';
import { flowQuery, listParams, pipelinesQuery, type Pipeline, type TaskItem } from './queries';
import { PipelinePage } from './PipelinePage';
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

it('finishes the route’s Flow read when the shell unsubscribes', async () => {
  let respond!: (reply: Reply) => void;
  serve(() => new Promise((resolve) => { respond = resolve; }));
  const loaded = queryClient.ensureQueryData(flowQuery);
  const stop = new QueryObserver(queryClient, flowQuery).subscribe(() => {});
  stop();
  respond({ status: 200, body: { items: [] } });
  await expect(loaded).resolves.toEqual({ count: 0, items: [] });
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
});

describe('Tasks: more of the page @s0 @s9 @s10', () => {
  const rows = () => [task('a1', 7, 'Fix the export', 's1'), task('a2', 8, 'Write the notes', 's1')];

  it('Archived shows snoozed, done and closed tasks, each naming its stage @s12 @s13 @s14', async () => {
    serve((url) => {
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
    expect(new URLSearchParams(reads[0]?.split('?')[1]).get('counts')).toBe('stage');
    expect(within(screen.getByRole('region', { name: 'Doing' })).getByText('0')).toBeTruthy();
  });

  it('opens the next server page when no stage has enough rows to virtualize', async () => {
    serve((url) =>
      url.includes('after=next')
        ? { status: 200, body: { tasks: [task('a2', 8, 'Review the export', 's2')], cursor: 'c2' } }
        : { status: 200, body: {} },
    );
    seed([]);
    queryClient.setQueryData(listKey, {
      tasks: [task('a1', 7, 'Fix the export', 's1')],
      cursor: 'c1',
      next_after: 'next',
      counts: { total: 2, stage: { s1: 1, s2: 1 } },
      counted: { s1: 1 },
    });
    show(<TasksPage pipeline="eng" search={{ view: 'list' }} />, '/tasks/eng?view=list');
    fireEvent.click(await screen.findByRole('button', { name: 'More tasks' }));
    expect(await screen.findByRole('link', { name: /Review the export/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'More tasks' })).toBeNull();
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

  it('Edit pipeline saves the stages whole; a removed stage that holds tasks asks where they go @s138', async () => {
    const rec = {
      id: 'eng',
      name: 'Engineering',
      settings_version: 3,
      done: null,
      closed: null,
      stages: [
        { id: 's1', name: 'Inbox', side: false, requirements: [] },
        { id: 's2', name: 'Doing', side: false, requirements: [] },
      ],
    };
    let saves = 0;
    serve((url, method) => {
      if (url === '/api/v2/pipelines/eng' && method === 'GET') return { status: 200, body: { pipeline: rec } };
      if (url === '/api/v2/pipelines') return { status: 200, body: { pipelines: [rec] } };
      if (url === '/api/v2/pipelines/eng' && method === 'POST') {
        saves += 1;
        if (saves === 1) return { status: 409, body: { error: { code: 'stage_has_tasks', detail: 'Doing holds tasks' } } };
        return { status: 200, body: { pipeline: { ...rec, settings_version: 4, stages: [rec.stages[0]] } } };
      }
      return { status: 200, body: {} };
    });
    seed(rows());
    show(<PipelinePage pipeline="eng" fresh={false} />, '/tasks/eng/edit');
    fireEvent.click(await screen.findByRole('button', { name: 'More, Doing' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Remove stage' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(posts[0]?.body).toMatchObject({ settings_version: 3, stages: [{ id: 's1', name: 'Inbox', side: false }] });
    fireEvent.click(screen.getByRole('button', { name: 'Move tasks' }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toMatchObject({ move_tasks_to: { pipeline: 'eng', stage: 's1' } });
  });

  it('Edit pipeline opens a stage to show its rules @s115 @s142', async () => {
    const rec = {
      id: 'eng',
      name: 'Engineering',
      settings_version: 3,
      stages: [
        { id: 's1', name: 'Inbox', side: false, requirements: [] },
        { id: 's2', name: 'Doing', side: false, by_hand: false, requirements: [{ kind: 'checks_pass' }] },
      ],
    };
    const rule = { id: 'sr1', name: '', pipeline_id: 'eng', pipeline_name: 'Engineering', stages: ['s2'], stage_names: ['Doing'], scope: 'stage', event: 'enters' };
    const full = { ...rule, timer: null, cond: {}, jev: '', then: 'block', status: 'active', revision: 1, can_edit: true };
    serve((url) => {
      if (url === '/api/v2/pipelines/eng') return { status: 200, body: { pipeline: rec } };
      if (url.startsWith('/api/v2/stage-rules')) return { status: 200, body: { items: [full], built_in: [], may_add: true } };
      return { status: 200, body: {} };
    });
    seed(rows());
    show(<PipelinePage pipeline="eng" fresh={false} />, '/tasks/eng/edit');
    const open = await screen.findByRole('button', { name: 'Rules, Doing' });
    expect(open.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(open);
    expect(open.getAttribute('aria-expanded')).toBe('true');
    const enter = await screen.findByRole('region', { name: 'On entering' });
    expect(await within(enter).findByTitle('Enters Doing, Block')).toBeTruthy();
    expect(within(screen.getByRole('region', { name: 'To leave' })).getByTitle('Checks pass')).toBeTruthy();
    fireEvent.click(within(enter).getByRole('button', { name: 'Add rule' }));
    expect(within(enter).getByRole('textbox', { name: 'Name' })).toBeTruthy();
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

  it('says so when nothing waits on you', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    queryClient.setQueryData(['flow'], { count: 0, items: [] });
    show(<FlowPage />, '/flow');
    expect(await screen.findByRole('heading', { name: 'Nothing waits on you' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Open Tasks' }).getAttribute('href')).toBe('/tasks');
  });
});
