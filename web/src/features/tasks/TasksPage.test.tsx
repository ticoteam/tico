import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRootRoute, createRouter, RouterProvider } from '@tanstack/react-router';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { keys, queryClient } from '@/data';
import { Toasts } from '@/kit/Toast';
import { FlowPage } from './FlowPage';
import { listParams, pipelinesQuery, type Pipeline, type TaskItem } from './queries';
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
  const root = createRootRoute({ component: () => node });
  const router = createRouter({ routeTree: root, history: createMemoryHistory({ initialEntries: [at] }) });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toasts />
    </QueryClientProvider>,
  );
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
  done: null,
  closed: null,
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

describe('Tasks board and list @s0 @s9 @s10', () => {
  it('a board card opens its task under the pipeline, with a count per stage and Late only when late', async () => {
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

  it('says so when the pipeline has nothing open, offering New task', async () => {
    serve(() => ({ status: 200, body: {} }));
    seed([]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Nothing in Engineering' })).toBeTruthy();
    expect(screen.getAllByRole('button', { name: 'New task' }).length).toBeGreaterThan(0);
  });

  it('names the page and offers Try again when the read fails', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'down', detail: 'down' } } }));
    queryClient.setQueryData(pipelinesQuery.queryKey, [PIPE]);
    show(<TasksPage pipeline="eng" search={{}} />, '/tasks/eng');
    expect(await screen.findByRole('heading', { name: 'Tasks did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('shows the filters set in the address as chips', async () => {
    serve(() => ({ status: 200, body: {} }));
    queryClient.setQueryData(keys.tasks.list(listParams('eng', { late: true })), { tasks: [], cursor: 'c1' });
    seed([]);
    show(<TasksPage pipeline="eng" search={{ late: true }} />, '/tasks/eng?late=true');
    expect(await screen.findByRole('button', { name: 'Clear Late' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Clear' })).toBeTruthy();
  });
});

describe('Flow @s24', () => {
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
  });

  it('names the page and offers Try again when the read fails', async () => {
    serve(() => ({ status: 500, body: { error: { code: 'down', detail: 'down' } } }));
    show(<FlowPage />, '/flow');
    expect(await screen.findByRole('heading', { name: 'Tasks did not load' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });
});
